"""
Report generation - incremental, not a rewrite.

The deliverable is one living Report (see Report's docstring in
app/db/models.py): the first run that produces any current knowledge
seeds it with a claim per fact; every run after that touches only the
claims for fact_keys this run actually changed. A claim for a fact this
run didn't affect is not re-fetched, not re-embedded, not re-verified,
and not re-written - it is the exact same database row it was before,
which is what "the parts the new source did not affect stay exactly as
they were, and the system can prove that" means as code: the id,
generated_at, and content of an unaffected claim are byte-identical
across runs, not just semantically similar.

Grounding is still applied to every claim this function does touch: the
drafted sentence is embedded and compared against its source chunk, and
anything that doesn't clear the threshold becomes an explicit "Evidence
Not Found" claim rather than a dropped or invented one.
"""

from __future__ import annotations

import math
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Report, ReportClaim
from app.llm.provider import LLMProvider
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.reports import ReportRepository
from app.services.observability import record_cost

settings = get_settings()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (norm_a * norm_b)


def _rebuild_content(report: Report) -> str:
    """Cheap string assembly from whatever is_current, non-superseded
    claims already exist - no LLM or embedding calls, so recomputing this
    display string on every touch doesn't violate 'costs like an update'."""
    lines = []
    for claim in report.claims:
        if not claim.is_current:
            continue
        if claim.is_evidence_not_found:
            lines.append(f"- {claim.claim_text}")
        else:
            lines.append(f"- {claim.claim_text} [{claim.fact_key}]")
    return "\n".join(lines) if lines else "No current knowledge to report on."


async def _draft_and_ground_one_claim(
    session: AsyncSession, provider: LLMProvider, run_id: uuid.UUID | None,
    report_id: uuid.UUID, fact_key: str, fact_value: str, source_chunk_id: uuid.UUID,
    initial_status: str,
) -> ReportClaim:
    """Drafts one claim sentence, grounds it against its source chunk, and
    supersedes whatever claim previously held this fact_key (if any) -
    the single incremental unit of work this module performs."""
    doc_repo = DocumentRepository(session)
    report_repo = ReportRepository(session)

    [claim_text], cost = await provider.draft_report_claims([{"fact_key": fact_key, "fact_value": fact_value}])
    await record_cost(session, run_id, cost)

    chunk = await doc_repo.get_chunk(source_chunk_id)
    is_grounded = False
    if chunk is not None and chunk.embedding is not None:
        [claim_vector], embed_cost = await provider.embed([claim_text])
        await record_cost(session, run_id, embed_cost)
        similarity = _cosine(claim_vector, list(chunk.embedding))
        is_grounded = similarity >= settings.grounding_similarity_threshold

    previous = await report_repo.current_claim_by_fact_key(report_id, fact_key)
    if previous is not None:
        await report_repo.supersede_claim(previous.id)

    if is_grounded:
        new_claim = ReportClaim(
            report_id=report_id, fact_key=fact_key, claim_text=claim_text,
            source_chunk_id=chunk.id, is_evidence_not_found=False,
            is_current=True, status=initial_status,
        )
    else:
        new_claim = ReportClaim(
            report_id=report_id, fact_key=fact_key,
            claim_text=f"{claim_text} Evidence Not Found.",
            source_chunk_id=None, is_evidence_not_found=True,
            is_current=True, status=initial_status,
        )
    return await report_repo.add_claim(new_claim)


async def generate_grounded_report(
    session: AsyncSession, provider: LLMProvider, run_id: uuid.UUID, affected_fact_keys: list[str] | None = None,
) -> Report | None:
    """Seeds the singleton report on its first-ever run (drafts every
    current fact), and on every run after that, updates only the claims
    for affected_fact_keys - everything else is left untouched. Returns
    None if there is nothing to do (an existing report, no facts
    affected) so the caller doesn't bump a version for a no-op."""
    knowledge_repo = KnowledgeRepository(session)
    report_repo = ReportRepository(session)

    report = await report_repo.get_singleton()
    is_first_run = report is None

    if is_first_run:
        report = await report_repo.create(Report(run_id=run_id, version=1, status="draft", content=""))
        current_items = await knowledge_repo.all_current()
        target_keys = {item.fact_key for item in current_items}
    else:
        report.run_id = run_id
        target_keys = set(affected_fact_keys or [])
        if not target_keys:
            return report  # nothing changed - not even a version bump
        report.version += 1
        current_items = [
            item for item in await knowledge_repo.all_current() if item.fact_key in target_keys
        ]

    for item in current_items:
        await _draft_and_ground_one_claim(
            session, provider, run_id, report.id, item.fact_key, item.fact_value,
            item.source_chunk_id, initial_status="pending",
        )

    await session.flush()
    await session.refresh(report, attribute_names=["claims"])
    report.content = _rebuild_content(report)
    return report


async def refresh_claim_for_approved_conflict(
    session: AsyncSession, provider: LLMProvider, run_id: uuid.UUID, fact_key: str,
) -> ReportClaim | None:
    """Called from the commit node for each conflict approved this run.

    The human already made the judgment call when they approved the
    conflict - accepting the new fact value. Restating that already-
    approved fact in the deliverable isn't a new decision, so the
    resulting claim is created pre-approved (status='approved',
    attributed to the system) rather than reopening a second approval
    round for the same decision. It still has to clear the grounding
    check like any other claim - approval doesn't exempt it from that.
    """
    knowledge_repo = KnowledgeRepository(session)
    report_repo = ReportRepository(session)

    report = await report_repo.get_singleton()
    if report is None:
        return None
    current = await knowledge_repo.current_by_fact_key(fact_key)
    if current is None:
        return None

    claim = await _draft_and_ground_one_claim(
        session, provider, run_id, report.id, fact_key, current.fact_value,
        current.source_chunk_id, initial_status="approved",
    )
    report.version += 1
    await session.flush()
    await session.refresh(report, attribute_names=["claims"])
    report.content = _rebuild_content(report)
    return claim
