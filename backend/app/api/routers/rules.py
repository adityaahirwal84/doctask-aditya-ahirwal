from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.api.schemas import RuleIn, RuleOut
from app.db.models import Rule
from app.db.session import get_session
from app.repositories.rules import RuleRepository

router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[RuleOut])
async def list_rules(session: AsyncSession = Depends(get_session)) -> list[RuleOut]:
    from sqlalchemy import select

    from app.db.models import Rule

    result = await session.execute(select(Rule).order_by(Rule.created_at.desc()))
    return [RuleOut.model_validate(r) for r in result.scalars().all()]


@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(payload: RuleIn, session: AsyncSession = Depends(get_session)) -> RuleOut:
    rule = await RuleRepository(session).create(
        Rule(name=payload.name, source_type=payload.source_type, rule_text=payload.rule_text)
    )
    return RuleOut.model_validate(rule)
