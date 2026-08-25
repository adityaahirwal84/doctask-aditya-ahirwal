"""
Node functions.

Two conventions hold across every node here, because they are what the
graded behaviors actually depend on:

1. Each node opens its own session via session_scope() and commits before
   returning. A node's DB writes are durable *before* LangGraph writes the
   next checkpoint, so "resume after a crash" and "the database already
   reflects this node's work" never disagree with each other.
2. Every node is safe to re-run from its start (LangGraph's documented
   resume behavior). Where that isn't true for free - ingest, conflict
   detection - the node checks status/existence in the DB before doing
   work, so re-running is a no-op rather than a duplicate.
"""

from __future__ import annotations

import uuid

from langgraph.types import interrupt

from app.db.models import ApprovalDecision, ApprovalItemType, DocumentStatus, WorkflowStatus
from app.db.session import session_scope
from app.graph.state import GraphState
from app.llm.provider import get_provider
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.reports import ReportRepository
from app.repositories.workflow import WorkflowRepository
from app.services.approval import try_commit_report
from app.services.conflict import detect_and_merge
from app.services.ingestion import parse_classify_extract_embed
from app.services.reporting import generate_grounded_report, refresh_claim_for_approved_conflict
from app.services.rules import validate_report_against_rules, validate_sources_against_rules


async def _set_status(run_id: str, status: WorkflowStatus, current_node: str) -> None:
    async with session_scope() as session:
        await WorkflowRepository(session).update_status(uuid.UUID(run_id), status, current_node=current_node)


async def ingest_one_document(state: GraphState) -> dict:
    if not state["pending_document_ids"]:
        return {}
    await _set_status(state["run_id"], WorkflowStatus.running, "ingest_one_document")

    doc_id = uuid.UUID(state["pending_document_ids"][0])
    async with session_scope() as session:
        document = await DocumentRepository(session).get(doc_id)
        if document is None:
            raise ValueError(f"Document {doc_id} referenced by run but not found")
        if document.status != DocumentStatus.parsed:
            provider = get_provider()
            await parse_classify_extract_embed(session, document, provider, uuid.UUID(state["run_id"]))
        # else: already parsed by a prior attempt at this node (crash-resume
        # or a document shared with an earlier run) - nothing to redo.

    return {"pending_document_ids": state["pending_document_ids"][1:]}


def route_after_ingest(state: GraphState) -> str:
    return "ingest_one_document" if state["pending_document_ids"] else "detect_conflicts"


async def detect_conflicts(state: GraphState) -> dict:
    await _set_status(state["run_id"], WorkflowStatus.running, "detect_conflicts")
    async with session_scope() as session:
        doc_ids = [uuid.UUID(d) for d in state["document_ids"]]
        unresolved = await KnowledgeRepository(session).unresolved_for_documents(doc_ids)
        _conflicts, auto_committed_fact_keys = await detect_and_merge(session, unresolved, uuid.UUID(state["run_id"]))
    return {"affected_fact_keys": auto_committed_fact_keys}


async def validate_rules(state: GraphState) -> dict:
    if not state["rule_ids"]:
        return {}
    await _set_status(state["run_id"], WorkflowStatus.running, "validate_rules")
    async with session_scope() as session:
        provider = get_provider()
        rule_ids = [uuid.UUID(r) for r in state["rule_ids"]]
        doc_ids = [uuid.UUID(d) for d in state["document_ids"]]
        run_id = uuid.UUID(state["run_id"])
        await validate_sources_against_rules(session, provider, rule_ids, doc_ids, run_id)
    return {}


async def generate_report(state: GraphState) -> dict:
    await _set_status(state["run_id"], WorkflowStatus.running, "generate_report")
    async with session_scope() as session:
        provider = get_provider()
        run_id = uuid.UUID(state["run_id"])
        touched_keys = set(state.get("affected_fact_keys") or [])

        report = await generate_grounded_report(session, provider, run_id, list(touched_keys))
        if report is None:
            report = await ReportRepository(session).get_singleton()
        report_id = str(report.id) if report else None

        if state["rule_ids"] and report is not None:
            # Only re-validate the claims this run actually touched - a
            # claim nothing changed doesn't need re-checking against
            # report-facing rules every run, for the same "costs like an
            # update" reason it doesn't need re-drafting.
            claims_to_check = [
                (c.id, c.claim_text) for c in report.claims if c.is_current and c.fact_key in touched_keys
            ]
            if claims_to_check:
                rule_ids = [uuid.UUID(r) for r in state["rule_ids"]]
                await validate_report_against_rules(session, provider, rule_ids, claims_to_check, run_id)

    return {"report_id": report_id}


async def human_approval(state: GraphState) -> dict:
    """Interrupts if anything is currently pending. This node's own
    interrupt() call is only reliable the FIRST time it fires within a
    resumed execution - see route_after_human_approval below for why the
    actual "keep asking until clear" loop is implemented as a graph edge,
    not by calling interrupt() repeatedly inside this node.
    """
    run_id = uuid.UUID(state["run_id"])
    async with session_scope() as session:
        pending_count, breakdown = await _pending_summary(session, run_id)
        status = WorkflowStatus.waiting_approval if pending_count > 0 else WorkflowStatus.running
        await WorkflowRepository(session).update_status(run_id, status, current_node="human_approval")

    if pending_count > 0:
        interrupt({"message": "Awaiting human review", **breakdown})
    return {}


async def route_after_human_approval(state: GraphState) -> str:
    """The actual gate. LangGraph resolves an interrupt() call positionally
    against whatever Command(resume=...) value unblocked it - it does NOT
    re-evaluate whether the condition that caused the interrupt still
    holds. Concretely: if human_approval interrupts for 2 pending items,
    and a reviewer decides only 1, resuming the graph replays
    human_approval from its start, reaches its interrupt() call again,
    and that call *resolves* (because a resume value is available) and
    falls through - even though one item is still genuinely pending. This
    was found and fixed during a strict compliance audit; see
    docs/architecture.md for the reproduction.

    The fix: the loop-until-clear logic lives here, in a conditional edge,
    not inside the node. Looping back to human_approval via this edge is a
    genuinely new graph step (not a replay of an old interrupted one), so
    its interrupt() call pauses correctly if anything is still pending -
    verified empirically before relying on it, the same way the original
    crash-resume semantics were.
    """
    run_id = uuid.UUID(state["run_id"])
    async with session_scope() as session:
        pending_count, _ = await _pending_summary(session, run_id)
    return "human_approval" if pending_count > 0 else "commit"


async def _pending_summary(session, run_id: uuid.UUID) -> tuple[int, dict]:
    open_conflicts = await KnowledgeRepository(session).open_conflicts_for_run(run_id)
    rule_repo_findings = await _pending_findings(session, run_id)
    pending_claims = await _pending_claims(session, run_id)
    breakdown = {
        "open_conflicts": len(open_conflicts),
        "pending_findings": len(rule_repo_findings),
        "pending_report_claims": len(pending_claims),
    }
    return len(open_conflicts) + len(rule_repo_findings) + len(pending_claims), breakdown


async def _pending_findings(session, run_id: uuid.UUID) -> list:
    from app.repositories.rules import RuleRepository

    findings = await RuleRepository(session).findings_for_run(run_id)
    return [f for f in findings if f.status == "pending"]


async def _pending_claims(session, run_id: uuid.UUID) -> list:
    report = await ReportRepository(session).get_for_run(run_id)
    if report is None:
        return []
    return [c for c in report.claims if c.is_current and c.status == "pending"]


async def commit(state: GraphState) -> dict:
    await _set_status(state["run_id"], WorkflowStatus.running, "commit")
    run_id = uuid.UUID(state["run_id"])

    async with session_scope() as session:
        provider = get_provider()
        approvals = await WorkflowRepository(session).approvals_for_run(run_id)
        knowledge_repo = KnowledgeRepository(session)
        for approval in approvals:
            if approval.item_type != ApprovalItemType.conflict or approval.decision != ApprovalDecision.approved:
                continue
            conflict = await knowledge_repo.get_conflict(approval.item_id)
            if conflict is not None:
                await refresh_claim_for_approved_conflict(session, provider, run_id, conflict.fact_key)

    if state["report_id"]:
        async with session_scope() as session:
            await try_commit_report(session, uuid.UUID(state["report_id"]))
    return {}


async def finalize(state: GraphState) -> dict:
    await _set_status(state["run_id"], WorkflowStatus.completed, "finalize")
    return {}
