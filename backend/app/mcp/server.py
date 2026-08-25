"""
MCP server.

Every tool here calls the exact same service-layer functions the REST API
calls - app/graph/runner.py, app/services/approval.py, and the repositories
- so there is no second implementation of "what does approving a conflict
mean" to drift out of sync with the API. This is deliberately the primary
way to drive the system end to end without a human touching a UI: approval
is a first-class tool (submit_decision), not an afterthought.

Run with: python -m app.mcp.server
"""

from __future__ import annotations

import uuid

from mcp.server.mcpserver import MCPServer

from app.db.models import ApprovalDecision, Rule, WorkflowStatus
from app.db.session import session_scope
from app.graph.checkpointer import close_checkpointer, open_checkpointer
from app.graph.runner import create_run, resume_run, wake_runs_waiting_on_approval
from app.repositories.audit import AuditRepository
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.reports import ReportRepository
from app.repositories.rules import RuleRepository
from app.repositories.workflow import WorkflowRepository
from app.services.approval import decide_conflict, decide_finding, decide_report_claim
from app.services.ingestion import DuplicateDocumentError, store_upload

mcp = MCPServer(
    name="doctask",
    description="Agentic document intelligence for vendor contracts, amendments, and invoices.",
)


@mcp.tool()
async def upload_document_text(filename: str, doc_format: str, content_base64: str) -> dict:
    """Upload a document (base64-encoded bytes) for the pipeline to ingest.
    doc_format must be one of: pdf, docx, txt, md."""
    import base64

    data = base64.b64decode(content_base64)
    async with session_scope() as session:
        try:
            document = await store_upload(session, filename, doc_format, data)
        except DuplicateDocumentError as exc:
            return {"error": "duplicate", "existing_document_id": str(exc.existing_document_id)}
        return {"document_id": str(document.id), "status": document.status}


@mcp.tool()
async def define_rule(name: str, source_type: str, rule_text: str) -> dict:
    """Register a rule (compliance_checklist, playbook, or style_guide)
    to validate documents and reports against."""
    async with session_scope() as session:
        rule = await RuleRepository(session).create(
            Rule(name=name, source_type=source_type, rule_text=rule_text)
        )
        return {"rule_id": str(rule.id)}


@mcp.tool()
async def start_run(document_ids: list[str], rule_ids: list[str] | None = None) -> dict:
    """Starts a workflow run over the given documents, optionally
    validated against the given rules. Returns immediately with a run id;
    a worker process executes it."""
    rule_ids = rule_ids or []
    run = await create_run([uuid.UUID(d) for d in document_ids], [uuid.UUID(r) for r in rule_ids])
    return {"run_id": str(run.id), "status": run.status}


@mcp.tool()
async def get_run_status(run_id: str) -> dict:
    """Returns the run's current stage and status: queued, running,
    waiting_approval, retry, completed, or failed."""
    async with session_scope() as session:
        run = await WorkflowRepository(session).get(uuid.UUID(run_id))
        if run is None:
            return {"error": "not found"}
        return {
            "status": run.status, "current_node": run.current_node,
            "retry_count": run.retry_count, "error": run.error,
        }


@mcp.tool()
async def list_pending_approvals(run_id: str) -> dict:
    """Lists every conflict, finding, and report claim still awaiting a
    human decision for this run - the exact set the graph is paused on."""
    rid = uuid.UUID(run_id)
    async with session_scope() as session:
        conflicts = await KnowledgeRepository(session).open_conflicts_for_run(rid)
        findings = [f for f in await RuleRepository(session).findings_for_run(rid) if f.status == "pending"]
        report = await ReportRepository(session).get_for_run(rid)
        claims = [c for c in (report.claims if report else []) if c.is_current and c.status == "pending"]
        return {
            "conflicts": [{"id": str(c.id), "fact_key": c.fact_key, "description": c.description} for c in conflicts],
            "findings": [{"id": str(f.id), "verdict": f.verdict, "evidence_text": f.evidence_text} for f in findings],
            "report_claims": [{"id": str(c.id), "claim_text": c.claim_text} for c in claims],
        }


@mcp.tool()
async def submit_approval(
    run_id: str, item_type: str, item_id: str, decision: str, reviewer: str, comment: str | None = None
) -> dict:
    """Approves or rejects exactly one item (item_type: conflict, finding,
    or report_claim). This is the machine-drivable equivalent of a
    reviewer clicking approve/reject in the UI - nothing becomes final
    without going through this same call, whether a human or a calling
    program invokes it.

    A report_claim decision can unblock OTHER runs too, not just run_id -
    the report is one system-wide singleton (see
    app/repositories/reports.py:get_for_run), so several runs can end up
    waiting on the very same claim. Conflicts and findings are correctly
    scoped to run_id and can only ever affect this one run.
    """
    rid = uuid.UUID(run_id)
    iid = uuid.UUID(item_id)
    approval_decision = ApprovalDecision(decision)
    async with session_scope() as session:
        if item_type == "conflict":
            conflict = await KnowledgeRepository(session).get_conflict(iid)
            if conflict is None:
                return {"error": "conflict not found"}
            await decide_conflict(session, conflict, approval_decision, reviewer, comment)
        elif item_type == "finding":
            finding = await RuleRepository(session).get_finding(iid)
            if finding is None:
                return {"error": "finding not found"}
            await decide_finding(session, finding, approval_decision, reviewer, comment)
        elif item_type == "report_claim":
            claim = await ReportRepository(session).get_claim(iid)
            if claim is None:
                return {"error": "report_claim not found"}
            await decide_report_claim(session, claim, rid, approval_decision, reviewer, comment)
            # Deliberately not committing here - see app/graph/nodes.py:
            # route_after_human_approval. Committing is exclusively the
            # graph's job.
        else:
            return {"error": f"unknown item_type {item_type}"}

    if item_type == "report_claim":
        async with session_scope() as session:
            waiting_ids = await WorkflowRepository(session).ids_waiting_on_approval()
        await wake_runs_waiting_on_approval(waiting_ids)
    else:
        async with session_scope() as session:
            run = await WorkflowRepository(session).get(rid)
            still_waiting = run is not None and run.status == WorkflowStatus.waiting_approval
        if still_waiting:
            await resume_run(rid)
    return {"applied": True}


@mcp.tool()
async def get_run_report(run_id: str) -> dict:
    """Returns the report tied to this run - the report id, status, and
    current claims. This is how a machine client discovers the report_id
    needed by get_report; without it there is no way to find the report
    for a run via MCP alone."""
    async with session_scope() as session:
        report = await ReportRepository(session).get_for_run(uuid.UUID(run_id))
        if report is None:
            return {"error": "no report yet for this run"}
        return {
            "report_id": str(report.id),
            "status": report.status,
            "content": report.content,
            "claims": [
                {
                    "claim_text": c.claim_text, "status": c.status,
                    "is_evidence_not_found": c.is_evidence_not_found,
                }
                for c in report.claims
                if c.is_current
            ],
        }


@mcp.tool()
async def get_report(report_id: str) -> dict:
    """Returns a report's content and, for every claim, whether it is
    grounded (with its source) or Evidence Not Found."""
    async with session_scope() as session:
        report = await ReportRepository(session).get(uuid.UUID(report_id))
        if report is None:
            return {"error": "not found"}
        return {
            "status": report.status,
            "content": report.content,
            "claims": [
                {
                    "claim_text": c.claim_text, "status": c.status,
                    "is_evidence_not_found": c.is_evidence_not_found,
                    "source_chunk_id": str(c.source_chunk_id) if c.source_chunk_id else None,
                }
                for c in report.claims
                if c.is_current
            ],
        }


@mcp.tool()
async def get_changelog(limit: int = 50) -> dict:
    """What changed, when, and which action caused it - the audit trail
    across every run."""
    async with session_scope() as session:
        entries = await AuditRepository(session).changelog(limit=limit)
        return {
            "entries": [
                {"actor": e.actor, "action": e.action, "entity_type": e.entity_type, "at": e.at.isoformat()}
                for e in entries
            ]
        }


@mcp.tool()
async def get_cost_report(run_id: str) -> dict:
    """Token usage, latency, and USD cost for a run, broken down by
    pipeline stage."""
    async with session_scope() as session:
        calls = await AuditRepository(session).cost_for_run(uuid.UUID(run_id))
        return {
            "total_cost_usd": sum(c.cost_usd for c in calls),
            "total_latency_ms": sum(c.latency_ms for c in calls),
            "by_node": [
                {"node": c.node, "cost_usd": c.cost_usd, "latency_ms": c.latency_ms, "tokens": c.input_tokens + c.output_tokens}
                for c in calls
            ],
        }


async def _run() -> None:
    await open_checkpointer()
    try:
        await mcp.run_stdio_async()
    finally:
        await close_checkpointer()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_run())
