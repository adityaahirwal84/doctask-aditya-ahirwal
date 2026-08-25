from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Approval, WorkflowRun, WorkflowStatus


class WorkflowRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, run: WorkflowRun) -> WorkflowRun:
        self.session.add(run)
        await self.session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> WorkflowRun | None:
        return await self.session.get(WorkflowRun, run_id)

    async def get_by_thread_id(self, thread_id: str) -> WorkflowRun | None:
        result = await self.session.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)
        )
        return result.scalar_one_or_none()

    async def claim_next_queued(self, worker_id: str) -> WorkflowRun | None:
        """Atomically claim one queued-or-retry run for this worker.

        SELECT ... FOR UPDATE SKIP LOCKED is what makes concurrent workers
        (or a crashed-and-restarted single worker) safe: two workers racing
        this query never get the same row, and a row locked by a worker
        that died holds its lock only until that worker's transaction ends -
        it does not wedge the queue.
        """
        result = await self.session.execute(
            text(
                """
                SELECT id FROM workflow_runs
                WHERE status IN ('queued', 'retry')
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
        )
        row = result.first()
        if row is None:
            return None
        run = await self.session.get(WorkflowRun, row[0])
        run.status = WorkflowStatus.running
        run.locked_by = worker_id
        run.locked_at = datetime.now(timezone.utc)
        await self.session.flush()
        return run

    async def update_status(
        self, run_id: uuid.UUID, status: WorkflowStatus, current_node: str | None = None,
        error: str | None = None,
    ) -> None:
        run = await self.session.get(WorkflowRun, run_id)
        if run is None:
            return
        run.status = status
        if current_node is not None:
            run.current_node = current_node
        run.error = error

    async def increment_retry(self, run_id: uuid.UUID) -> int | None:
        """Returns the new retry_count, or None if this run no longer
        exists (already deleted, or - see app/workers/loop.py's
        _run_and_handle_outcome - never existed, e.g. a stale run_id).
        Callers must treat None as "nothing to update", not coerce it
        into 0/1: there is no row to bump, and no row to later mark
        retry/failed against either."""
        run = await self.session.get(WorkflowRun, run_id)
        if run is None:
            return None
        run.retry_count += 1
        await self.session.flush()
        return run.retry_count

    async def add_approval(self, approval: Approval) -> Approval:
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def approvals_for_run(self, run_id: uuid.UUID) -> list[Approval]:
        result = await self.session.execute(select(Approval).where(Approval.run_id == run_id))
        return list(result.scalars().all())

    async def decision_for_item(
        self, run_id: uuid.UUID, item_type: str, item_id: uuid.UUID
    ) -> Approval | None:
        result = await self.session.execute(
            select(Approval).where(
                Approval.run_id == run_id,
                Approval.item_type == item_type,
                Approval.item_id == item_id,
            )
        )
        return result.scalar_one_or_none()

    async def ids_waiting_on_approval(self) -> list[uuid.UUID]:
        """Every run currently parked at human_approval. Used to wake all
        of them when a shared report claim is resolved (see
        ReportRepository.get_for_run: the report - and therefore every
        report claim - is one system-wide singleton, not scoped to any
        one run), since resolving a claim under one run's endpoint
        doesn't, by itself, tell any *other* run waiting on that same
        claim to re-check. Conflicts and findings ARE run-scoped, so a
        decision on one of those only ever needs to wake its own run -
        this is specifically for the report-claim, cross-run case."""
        result = await self.session.execute(
            select(WorkflowRun.id).where(WorkflowRun.status == WorkflowStatus.waiting_approval)
        )
        return [row[0] for row in result.all()]
