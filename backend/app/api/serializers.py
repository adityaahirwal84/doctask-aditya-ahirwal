"""
Resolves raw ids into the human-readable source references the review UI
needs: which document, which section, what the passage actually says.
Pure read-side composition over existing repositories - no service-layer
or business logic changes.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ConflictOut, FindingOut, KnowledgeItemRef, ReportClaimOut, SourceRef
from app.db.models import Conflict, Finding, ReportClaim
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.rules import RuleRepository

_SNIPPET_MAX_CHARS = 280


async def build_source_ref(session: AsyncSession, chunk_id: uuid.UUID | None) -> SourceRef | None:
    if chunk_id is None:
        return None
    doc_repo = DocumentRepository(session)
    chunk = await doc_repo.get_chunk(chunk_id)
    if chunk is None:
        return None
    document = await doc_repo.get(chunk.document_id)
    snippet = chunk.content[:_SNIPPET_MAX_CHARS]
    if len(chunk.content) > _SNIPPET_MAX_CHARS:
        snippet += "…"
    return SourceRef(
        chunk_id=chunk.id, document_id=chunk.document_id,
        document_filename=document.filename if document else "unknown",
        section_ref=chunk.section_ref, snippet=snippet,
    )


async def build_knowledge_item_ref(session: AsyncSession, item_id: uuid.UUID) -> KnowledgeItemRef:
    item = await KnowledgeRepository(session).get(item_id)
    source = await build_source_ref(session, item.source_chunk_id) if item else None
    return KnowledgeItemRef(id=item_id, fact_value=item.fact_value if item else "unknown", source=source)


async def build_conflict_out(session: AsyncSession, conflict: Conflict) -> ConflictOut:
    return ConflictOut(
        id=conflict.id, fact_key=conflict.fact_key, description=conflict.description,
        status=conflict.status,
        item_a=await build_knowledge_item_ref(session, conflict.item_a_id),
        item_b=await build_knowledge_item_ref(session, conflict.item_b_id),
    )


async def build_finding_out(session: AsyncSession, finding: Finding) -> FindingOut:
    rule = await RuleRepository(session).get(finding.rule_id)
    return FindingOut(
        id=finding.id, rule_id=finding.rule_id, rule_name=rule.name if rule else "unknown",
        target_type=finding.target_type, source=await build_source_ref(session, finding.source_chunk_id),
        verdict=finding.verdict, evidence_text=finding.evidence_text, status=finding.status,
    )


async def build_report_claim_out(session: AsyncSession, claim: ReportClaim) -> ReportClaimOut:
    return ReportClaimOut(
        id=claim.id, claim_text=claim.claim_text,
        source=await build_source_ref(session, claim.source_chunk_id),
        is_evidence_not_found=claim.is_evidence_not_found, status=claim.status,
    )
