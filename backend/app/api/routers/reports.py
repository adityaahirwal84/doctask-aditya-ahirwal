from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.api.schemas import AuditLogOut, ReportOut, VersionOut
from app.api.serializers import build_report_claim_out
from app.db.session import get_session
from app.repositories.audit import AuditRepository
from app.repositories.reports import ReportRepository
from app.services.conflict import _fact_entity_id

router = APIRouter(tags=["reports"], dependencies=[Depends(require_api_key)])


@router.get("/reports/{report_id}", response_model=ReportOut)
async def get_report(report_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ReportOut:
    report = await ReportRepository(session).get(report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    claims = [await build_report_claim_out(session, c) for c in report.claims if c.is_current]
    return ReportOut(
        id=report.id, run_id=report.run_id, version=report.version,
        status=report.status, content=report.content, claims=claims,
    )


@router.get("/versions/recent", response_model=list[VersionOut])
async def get_recent_versions(limit: int = 50, session: AsyncSession = Depends(get_session)) -> list[VersionOut]:
    """Powers the version history page: every fact/report revision across
    the whole system, most recent first - each snapshot already carries
    the fact_key (or report id) and the value, so the page doesn't need a
    second lookup per row."""
    from sqlalchemy import select

    from app.db.models import Version

    result = await session.execute(select(Version).order_by(Version.created_at.desc()).limit(limit))
    return [VersionOut.model_validate(v) for v in result.scalars().all()]


@router.get("/versions/by-fact-key/{fact_key}", response_model=list[VersionOut])
async def get_version_history_by_fact_key(
    fact_key: str, session: AsyncSession = Depends(get_session)
) -> list[VersionOut]:
    """Drill-down from a conflict or the recent-versions list into one
    fact's full chain, without the caller needing to know the uuid5
    convention that derives entity_id from fact_key."""
    entity_id = _fact_entity_id(fact_key)
    history = await AuditRepository(session).history("knowledge_item", entity_id)
    return [VersionOut.model_validate(v) for v in history]


@router.get("/versions/{entity_type}/{entity_id}", response_model=list[VersionOut])
async def get_version_history(
    entity_type: str, entity_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[VersionOut]:
    history = await AuditRepository(session).history(entity_type, entity_id)
    return [VersionOut.model_validate(v) for v in history]


@router.get("/changelog", response_model=list[AuditLogOut])
async def get_changelog(limit: int = 100, session: AsyncSession = Depends(get_session)) -> list[AuditLogOut]:
    entries = await AuditRepository(session).changelog(limit=limit)
    return [AuditLogOut.model_validate(e) for e in entries]


@router.get("/audit-log", response_model=list[AuditLogOut])
async def get_audit_log(limit: int = 100, session: AsyncSession = Depends(get_session)) -> list[AuditLogOut]:
    entries = await AuditRepository(session).changelog(limit=limit)
    return [AuditLogOut.model_validate(e) for e in entries]
