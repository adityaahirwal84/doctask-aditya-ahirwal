"""
Every node that calls the LLM provider or changes state routes through
these two helpers, so "a run can report what it spent and where the time
went, stage by stage" and "what changed, when, and because of which
source" are guaranteed to be true of every node, not just the ones a
developer remembered to instrument.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLogEntry, LLMCall
from app.domain.entities import CostRecord
from app.repositories.audit import AuditRepository


async def record_cost(session: AsyncSession, run_id: uuid.UUID | None, cost: CostRecord) -> None:
    repo = AuditRepository(session)
    await repo.record_llm_call(
        LLMCall(
            run_id=run_id, node=cost.node, model=cost.model,
            input_tokens=cost.input_tokens, output_tokens=cost.output_tokens,
            cost_usd=cost.cost_usd, latency_ms=cost.latency_ms,
        )
    )


async def record_audit(
    session: AsyncSession, actor: str, action: str, entity_type: str,
    entity_id: uuid.UUID, before: dict | None = None, after: dict | None = None,
) -> None:
    repo = AuditRepository(session)
    await repo.log(
        AuditLogEntry(
            actor=actor, action=action, entity_type=entity_type, entity_id=entity_id,
            before=before, after=after,
        )
    )
