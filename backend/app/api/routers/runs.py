from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.api.schemas import (
    ApprovalBatchIn,
    CostLineOut,
    CostReportOut,
    PendingApprovalsOut,
    ReportOut,
    RunCreateIn,
    RunOut,
)
from app.api.serializers import build_conflict_out, build_finding_out, build_report_claim_out
from app.db.models import ApprovalDecision, WorkflowRun, WorkflowStatus
from app.db.session import get_session
from app.graph.runner import create_run, resume_run, wake_runs_waiting_on_approval
from app.repositories.audit import AuditRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.reports import ReportRepository
from app.repositories.rules import RuleRepository
from app.repositories.workflow import WorkflowRepository
from app.services.approval import (
    AlreadyDecidedError,
    decide_conflict,
    decide_finding,
    decide_report_claim,
)

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[RunOut])
async def list_runs(session: AsyncSession = Depends(get_session)) -> list[RunOut]:
    """Powers the workflow dashboard - every run, most recent first."""
    result = await session.execute(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()))
    return [RunOut.model_validate(r) for r in result.scalars().all()]


@router.post("", response_model=RunOut, status_code=201)
async def start_run(payload: RunCreateIn) -> RunOut:
    run = await create_run(payload.document_ids, payload.rule_ids)
    return RunOut.model_validate(run)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await WorkflowRepository(session).get(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return RunOut.model_validate(run)


@router.get("/{run_id}/report", response_model=ReportOut | None)
async def get_run_report(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ReportOut | None:
    """Convenience wrapper so the run-detail page doesn't need to know a
    separate report id - a run has at most one report."""
    report = await ReportRepository(session).get_for_run(run_id)
    if report is None:
        return None
    claims = [await build_report_claim_out(session, c) for c in report.claims if c.is_current]
    return ReportOut(
        id=report.id, run_id=report.run_id, version=report.version,
        status=report.status, content=report.content, claims=claims,
    )


@router.get("/{run_id}/pending-approvals", response_model=PendingApprovalsOut)
async def pending_approvals(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> PendingApprovalsOut:
    conflicts = await KnowledgeRepository(session).open_conflicts_for_run(run_id)
    findings = [f for f in await RuleRepository(session).findings_for_run(run_id) if f.status == "pending"]
    report = await ReportRepository(session).get_for_run(run_id)
    claims = [c for c in (report.claims if report else []) if c.is_current and c.status == "pending"]
    return PendingApprovalsOut(
        conflicts=[await build_conflict_out(session, c) for c in conflicts],
        findings=[await build_finding_out(session, f) for f in findings],
        report_claims=[await build_report_claim_out(session, c) for c in claims],
    )


@router.post("/{run_id}/approvals", response_model=PendingApprovalsOut)
async def submit_approvals(
    run_id: uuid.UUID, payload: ApprovalBatchIn, session: AsyncSession = Depends(get_session)
) -> PendingApprovalsOut:
    """Applies each decision independently - one bad item in the batch
    does not block the rest - then resumes whichever run(s) can now
    proceed (or interrupt again if items are still outstanding).

    Report claims are the one item type NOT scoped to this run - see
    app/repositories/reports.py:get_for_run - so deciding one can clear
    the human_approval block for other runs that happened to be waiting
    on that same shared claim, not just this run_id. Conflicts and
    findings are correctly run-scoped and can only ever affect this run,
    so they keep the narrower single-run resume.
    """
    knowledge_repo = KnowledgeRepository(session)
    rule_repo = RuleRepository(session)
    report_repo = ReportRepository(session)
    workflow_repo = WorkflowRepository(session)
    errors: list[str] = []
    report_claim_decided = False

    for decision in payload.decisions:
        try:
            approval_decision = ApprovalDecision(decision.decision)
            if decision.item_type == "conflict":
                conflict = await knowledge_repo.get_conflict(decision.item_id)
                if conflict is None:
                    errors.append(f"conflict {decision.item_id} not found")
                    continue
                await decide_conflict(session, conflict, approval_decision, decision.reviewer, decision.comment)
            elif decision.item_type == "finding":
                finding = await rule_repo.get_finding(decision.item_id)
                if finding is None:
                    errors.append(f"finding {decision.item_id} not found")
                    continue
                await decide_finding(session, finding, approval_decision, decision.reviewer, decision.comment)
            elif decision.item_type == "report_claim":
                claim = await report_repo.get_claim(decision.item_id)
                if claim is None:
                    errors.append(f"report_claim {decision.item_id} not found")
                    continue
                await decide_report_claim(session, claim, run_id, approval_decision, decision.reviewer, decision.comment)
                report_claim_decided = True
                # Deliberately NOT calling try_commit_report here - see
                # app/graph/nodes.py:route_after_human_approval. Committing
                # is exclusively the graph's job, reached only once every
                # conflict, finding, AND claim for this run is resolved;
                # calling it here (checking claims alone, blind to
                # conflicts/findings) previously let a report get marked
                # "committed" while a real conflict was still open.
            else:
                errors.append(f"unknown item_type {decision.item_type}")
        except AlreadyDecidedError as exc:
            errors.append(str(exc))

    await session.flush()

    if report_claim_decided:
        # Capture the waiting set, then commit, then resume - same
        # ordering as the single-run path below and for the same reason:
        # every one of these resumes opens its own session and must see
        # the decision(s) just made as already committed.
        waiting_ids = await workflow_repo.ids_waiting_on_approval()
        await session.commit()
        await wake_runs_waiting_on_approval(waiting_ids)
    else:
        run = await workflow_repo.get(run_id)
        if run and run.status == WorkflowStatus.waiting_approval:
            await session.commit()  # persist decisions before resuming graph execution
            await resume_run(run_id)


    conflicts = await knowledge_repo.open_conflicts_for_run(run_id)
    findings = [f for f in await rule_repo.findings_for_run(run_id) if f.status == "pending"]
    report = await report_repo.get_for_run(run_id)
    claims = [c for c in (report.claims if report else []) if c.is_current and c.status == "pending"]
    result = PendingApprovalsOut(
        conflicts=[await build_conflict_out(session, c) for c in conflicts],
        findings=[await build_finding_out(session, f) for f in findings],
        report_claims=[await build_report_claim_out(session, c) for c in claims],
    )
    if errors:
        raise HTTPException(
            422,
            {"message": "Some decisions could not be applied", "errors": errors, "result": result.model_dump(mode="json")},
        )
    return result


@router.get("/{run_id}/cost", response_model=CostReportOut)
async def get_cost(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> CostReportOut:
    calls = await AuditRepository(session).cost_for_run(run_id)
    return CostReportOut(
        run_id=run_id,
        total_cost_usd=sum(c.cost_usd for c in calls),
        total_latency_ms=sum(c.latency_ms for c in calls),
        by_node=[
            CostLineOut(
                node=c.node, model=c.model, input_tokens=c.input_tokens,
                output_tokens=c.output_tokens, cost_usd=c.cost_usd, latency_ms=c.latency_ms,
            )
            for c in calls
        ],
    )
