from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Finding, Rule


class RuleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, rule: Rule) -> Rule:
        self.session.add(rule)
        await self.session.flush()
        return rule

    async def get(self, rule_id: uuid.UUID) -> Rule | None:
        return await self.session.get(Rule, rule_id)

    async def list_by_ids(self, rule_ids: list[uuid.UUID]) -> list[Rule]:
        result = await self.session.execute(select(Rule).where(Rule.id.in_(rule_ids)))
        return list(result.scalars().all())

    async def add_finding(self, finding: Finding) -> Finding:
        self.session.add(finding)
        await self.session.flush()
        return finding

    async def findings_for_run(self, run_id: uuid.UUID) -> list[Finding]:
        result = await self.session.execute(select(Finding).where(Finding.run_id == run_id))
        return list(result.scalars().all())

    async def get_finding(self, finding_id: uuid.UUID) -> Finding | None:
        return await self.session.get(Finding, finding_id)
