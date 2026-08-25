from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Report, ReportClaim


class ReportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, report: Report) -> Report:
        self.session.add(report)
        await self.session.flush()
        return report

    async def add_claim(self, claim: ReportClaim) -> ReportClaim:
        self.session.add(claim)
        await self.session.flush()
        return claim

    async def get(self, report_id: uuid.UUID) -> Report | None:
        result = await self.session.execute(
            select(Report).where(Report.id == report_id).options(selectinload(Report.claims))
        )
        return result.scalar_one_or_none()

    async def get_singleton(self) -> Report | None:
        """The report is one living deliverable, not one per run - this
        is the only real lookup; get_for_run below is kept as a
        documented alias for existing call sites."""
        result = await self.session.execute(
            select(Report).options(selectinload(Report.claims)).order_by(Report.generated_at.desc()).limit(1)
        )
        return result.scalars().first()

    async def get_for_run(self, run_id: uuid.UUID) -> Report | None:
        """Alias for get_singleton(): there is one deliverable system-wide,
        so 'the report for run X' and 'the current report' are the same
        thing. The run_id parameter is unused; kept for call-site
        stability."""
        return await self.get_singleton()

    async def get_claim(self, claim_id: uuid.UUID) -> ReportClaim | None:
        return await self.session.get(ReportClaim, claim_id)

    async def current_claim_by_fact_key(self, report_id: uuid.UUID, fact_key: str) -> ReportClaim | None:
        result = await self.session.execute(
            select(ReportClaim).where(
                ReportClaim.report_id == report_id,
                ReportClaim.fact_key == fact_key,
                ReportClaim.is_current.is_(True),
            )
        )
        return result.scalars().first()

    async def supersede_claim(self, claim_id: uuid.UUID) -> None:
        claim = await self.session.get(ReportClaim, claim_id)
        if claim:
            claim.is_current = False
