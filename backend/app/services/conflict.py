"""
Conflict detection and merge.

The rule is simple and deliberately narrow, because it is the one piece of
logic in the whole system that decides whether something reaches a human
before it becomes truth:

  - A fact_key never seen before -> genuinely new information, nothing to
    adjudicate. It commits immediately (becomes is_current=True) and gets
    a Version row so it is still traceable, but it never touches the
    approval queue - there is no judgment call to make about a fact with
    no prior value to disagree with.
  - A fact_key that already has a current value, and the new value matches
    (after normalization) -> confirms what's already known. No-op, no
    approval needed, the new row stays non-current so it isn't a
    duplicate "current" row.
  - A fact_key that already has a current value, and the new value
    genuinely differs -> a Conflict. Both the old and new rows are left
    exactly as they are (old stays current) until a human decides. This
    is the literal mechanism behind "never silently overwrite previous
    knowledge."
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conflict, KnowledgeItem, Version
from app.repositories.audit import AuditRepository
from app.repositories.knowledge import KnowledgeRepository
from app.services.observability import record_audit

# Stable per-fact identity for versioning: the same fact_key always maps to
# the same Version.entity_id across its whole history, even though each
# revision is physically a brand-new KnowledgeItem row.
_FACT_NAMESPACE = uuid.UUID("6f9c2e1a-0000-4000-8000-000000000000")


def _fact_entity_id(fact_key: str) -> uuid.UUID:
    return uuid.uuid5(_FACT_NAMESPACE, fact_key)


def _normalize(value: str) -> str:
    return re.sub(r"[\s,$]+", "", value.strip().lower())


async def detect_and_merge(
    session: AsyncSession, new_items: list[KnowledgeItem], run_id: uuid.UUID,
) -> tuple[list[Conflict], list[str]]:
    """Returns (conflicts_found, auto_committed_fact_keys). The second
    list is exactly the set of facts this run made newly current without
    a conflict - it's what the report-generation node uses to update only
    what changed, not the whole deliverable."""
    knowledge_repo = KnowledgeRepository(session)
    audit_repo = AuditRepository(session)
    conflicts: list[Conflict] = []
    auto_committed_fact_keys: list[str] = []

    # Multiple new documents in one run can extract the same fact_key; work
    # through them in a stable order so conflicts are detected against
    # whatever is *committed*, not against another pending item in the
    # same batch.
    for item in new_items:
        current = await knowledge_repo.current_by_fact_key(item.fact_key)

        if current is None:
            item.is_current = True
            entity_id = _fact_entity_id(item.fact_key)
            version_number = await audit_repo.next_version_number("knowledge_item", entity_id)
            await audit_repo.add_version(
                Version(
                    entity_type="knowledge_item", entity_id=entity_id,
                    version_number=version_number,
                    snapshot={"fact_key": item.fact_key, "fact_value": item.fact_value},
                    change_reason=f"New fact from document {item.document_id}",
                    caused_by_document_id=item.document_id,
                )
            )
            await record_audit(
                session, actor="system", action="knowledge_item.committed",
                entity_type="knowledge_item", entity_id=item.id,
                after={"fact_key": item.fact_key, "fact_value": item.fact_value},
            )
            auto_committed_fact_keys.append(item.fact_key)
            continue

        if _normalize(current.fact_value) == _normalize(item.fact_value):
            # Confirms existing knowledge - no state change, no approval.
            continue

        conflict = await knowledge_repo.add_conflict(
            Conflict(
                fact_key=item.fact_key, item_a_id=current.id, item_b_id=item.id,
                description=(
                    f"'{item.fact_key}' is '{current.fact_value}' in the committed "
                    f"knowledge base but '{item.fact_value}' in a newly ingested document."
                ),
                run_id=run_id,
            )
        )
        conflicts.append(conflict)

    return conflicts, auto_committed_fact_keys
