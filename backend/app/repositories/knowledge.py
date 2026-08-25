from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conflict, ConflictStatus, KnowledgeItem


class KnowledgeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, item_id: uuid.UUID) -> KnowledgeItem | None:
        return await self.session.get(KnowledgeItem, item_id)

    async def current_by_fact_key(self, fact_key: str) -> KnowledgeItem | None:
        result = await self.session.execute(
            select(KnowledgeItem)
            .where(KnowledgeItem.fact_key == fact_key, KnowledgeItem.is_current.is_(True))
            .order_by(KnowledgeItem.extracted_at.desc())
        )
        return result.scalars().first()

    async def all_current(self) -> list[KnowledgeItem]:
        result = await self.session.execute(
            select(KnowledgeItem).where(KnowledgeItem.is_current.is_(True))
        )
        return list(result.scalars().all())

    async def unresolved_for_documents(self, document_ids: list[uuid.UUID]) -> list[KnowledgeItem]:
        """Items extracted this run that are neither committed (is_current)
        nor already turned into a Conflict - i.e. not yet processed by
        conflict detection. Safe to call repeatedly: once an item is
        resolved either way, it stops matching this query, which is what
        makes the conflict-detection node idempotent on resume."""
        already_conflicted = select(Conflict.item_b_id)
        result = await self.session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.document_id.in_(document_ids),
                KnowledgeItem.is_current.is_(False),
                KnowledgeItem.id.not_in(already_conflicted),
            )
        )
        return list(result.scalars().all())

    async def add(self, item: KnowledgeItem) -> KnowledgeItem:
        self.session.add(item)
        await self.session.flush()
        return item

    async def mark_superseded(self, item_id: uuid.UUID) -> None:
        item = await self.session.get(KnowledgeItem, item_id)
        if item:
            item.is_current = False

    async def add_conflict(self, conflict: Conflict) -> Conflict:
        self.session.add(conflict)
        await self.session.flush()
        return conflict

    async def open_conflicts_for_run(self, run_id: uuid.UUID) -> list[Conflict]:
        result = await self.session.execute(
            select(Conflict).where(Conflict.run_id == run_id, Conflict.status == ConflictStatus.open)
        )
        return list(result.scalars().all())

    async def get_conflict(self, conflict_id: uuid.UUID) -> Conflict | None:
        return await self.session.get(Conflict, conflict_id)
