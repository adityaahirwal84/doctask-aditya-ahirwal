from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLogEntry, LLMCall, Version


class AuditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def next_version_number(self, entity_type: str, entity_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.max(Version.version_number)).where(
                Version.entity_type == entity_type, Version.entity_id == entity_id
            )
        )
        current_max = result.scalar()
        return (current_max or 0) + 1

    async def add_version(self, version: Version) -> Version:
        self.session.add(version)
        await self.session.flush()
        return version

    async def history(self, entity_type: str, entity_id: uuid.UUID) -> list[Version]:
        result = await self.session.execute(
            select(Version)
            .where(Version.entity_type == entity_type, Version.entity_id == entity_id)
            .order_by(Version.version_number)
        )
        return list(result.scalars().all())

    async def log(self, entry: AuditLogEntry) -> AuditLogEntry:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def changelog(self, limit: int = 100) -> list[AuditLogEntry]:
        result = await self.session.execute(
            select(AuditLogEntry).order_by(AuditLogEntry.at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def record_llm_call(self, call: LLMCall) -> LLMCall:
        self.session.add(call)
        await self.session.flush()
        return call

    async def cost_for_run(self, run_id: uuid.UUID) -> list[LLMCall]:
        result = await self.session.execute(select(LLMCall).where(LLMCall.run_id == run_id))
        return list(result.scalars().all())
