"""
Approval and commit.

Every decision here is scoped to exactly one item (one conflict, one
finding, one report claim). Approving or rejecting one item never touches
the state of any other item - that is what "if rejected, only rejected
items roll back; everything else stays committed" means concretely: there
is no batch-level commit/rollback anywhere in this file, only per-item
ones, and each is wrapped in its own audit trail entry.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Approval,
    ApprovalDecision,
    ApprovalItemType,
    Conflict,
    ConflictStatus,
    Version,
)
from app.repositories.audit import AuditRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.reports import ReportRepository
from app.repositories.workflow import WorkflowRepository
from app.services.conflict import _fact_entity_id
from app.services.observability import record_audit


class AlreadyDecidedError(Exception):
    """A conflict, finding, or claim can be decided exactly once - a
    unique constraint on (run_id, item_type, item_id) backs this up at the
    database level too, so a race between two reviewers can't double-apply
    a decision."""


async def _record_decision(
    session: AsyncSession, run_id: uuid.UUID, item_type: ApprovalItemType, item_id: uuid.UUID,
    decision: ApprovalDecision, reviewer: str, comment: str | None,
) -> Approval:
    workflow_repo = WorkflowRepository(session)
    existing = await workflow_repo.decision_for_item(run_id, item_type, item_id)
    if existing is not None:
        raise AlreadyDecidedError(f"{item_type} {item_id} already decided in run {run_id}")
    return await workflow_repo.add_approval(
        Approval(
            run_id=run_id, item_type=item_type, item_id=item_id,
            decision=decision, reviewer=reviewer, comment=comment,
        )
    )


async def decide_conflict(
    session: AsyncSession, conflict: Conflict, decision: ApprovalDecision,
    reviewer: str, comment: str | None = None,
) -> None:
    await _record_decision(session, conflict.run_id, ApprovalItemType.conflict, conflict.id, decision, reviewer, comment)

    knowledge_repo = KnowledgeRepository(session)
    audit_repo = AuditRepository(session)

    if decision == ApprovalDecision.approved:
        # Accept the new information: old becomes historical, new becomes current.
        await knowledge_repo.mark_superseded(conflict.item_a_id)
        new_item = await knowledge_repo.get(conflict.item_b_id)
        new_item.is_current = True
        new_item.supersedes_id = conflict.item_a_id

        entity_id = _fact_entity_id(conflict.fact_key)
        version_number = await audit_repo.next_version_number("knowledge_item", entity_id)
        await audit_repo.add_version(
            Version(
                entity_type="knowledge_item", entity_id=entity_id, version_number=version_number,
                snapshot={"fact_key": new_item.fact_key, "fact_value": new_item.fact_value},
                change_reason=f"Conflict {conflict.id} resolved: accepted new value",
                caused_by_document_id=new_item.document_id,
            )
        )
        conflict.status = ConflictStatus.approved
        await record_audit(
            session, actor=reviewer, action="conflict.approved",
            entity_type="conflict", entity_id=conflict.id,
            after={"fact_key": conflict.fact_key, "accepted_value": new_item.fact_value},
        )
    else:
        # Reject: the old value stays current, exactly as it was. The
        # pending item is left as a permanent, non-current historical row -
        # nothing about the committed knowledge base changes.
        conflict.status = ConflictStatus.rejected
        await record_audit(
            session, actor=reviewer, action="conflict.rejected",
            entity_type="conflict", entity_id=conflict.id,
            after={"fact_key": conflict.fact_key, "kept_value": "unchanged"},
        )


async def decide_finding(
    session: AsyncSession, finding, decision: ApprovalDecision, reviewer: str, comment: str | None = None,
) -> None:
    await _record_decision(session, finding.run_id, ApprovalItemType.finding, finding.id, decision, reviewer, comment)
    finding.status = "approved" if decision == ApprovalDecision.approved else "rejected"
    await record_audit(
        session, actor=reviewer, action=f"finding.{finding.status}",
        entity_type="finding", entity_id=finding.id,
        after={"verdict": finding.verdict, "status": finding.status},
    )


async def decide_report_claim(
    session: AsyncSession, claim, run_id: uuid.UUID, decision: ApprovalDecision,
    reviewer: str, comment: str | None = None,
) -> None:
    await _record_decision(session, run_id, ApprovalItemType.report_claim, claim.id, decision, reviewer, comment)
    claim.status = "approved" if decision == ApprovalDecision.approved else "rejected"
    await record_audit(
        session, actor=reviewer, action=f"report_claim.{claim.status}",
        entity_type="report_claim", entity_id=claim.id,
        after={"status": claim.status},
    )


async def try_commit_report(session: AsyncSession, report_id: uuid.UUID) -> bool:
    """Commits the report once every *currently active* claim has a
    decision. A claim superseded by a newer fact value (see
    app/services/reporting.py) is excluded here regardless of its own old
    status - it's history, not part of what's being committed, and must
    never block commit or leak into the committed content just because it
    was never explicitly decided before being superseded. Rejected claims
    are dropped from the committed body but their rows - and their
    rejection - remain in the database; nothing is deleted."""
    report_repo = ReportRepository(session)
    report = await report_repo.get(report_id)
    if report is None:
        return False
    active_claims = [c for c in report.claims if c.is_current]
    if any(c.status == "pending" for c in active_claims):
        return False

    approved_lines = []
    for claim in active_claims:
        if claim.status == "approved":
            approved_lines.append(f"- {claim.claim_text}")
    report.content = "\n".join(approved_lines) if approved_lines else "No approved claims."
    report.status = "committed"

    audit_repo = AuditRepository(session)
    entity_id = uuid.uuid5(uuid.UUID("6f9c2e1a-0000-4000-8000-000000000001"), "primary_report")
    version_number = await audit_repo.next_version_number("report", entity_id)
    await audit_repo.add_version(
        Version(
            entity_type="report", entity_id=entity_id, version_number=version_number,
            snapshot={"report_id": str(report.id), "content": report.content},
            change_reason=f"Report {report.id} committed after item-level approval",
        )
    )
    await record_audit(
        session, actor="system", action="report.committed",
        entity_type="report", entity_id=report.id, after={"status": "committed"},
    )
    return True
