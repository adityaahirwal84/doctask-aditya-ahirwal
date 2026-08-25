"""
The worker.

This is a plain asyncio loop, not a Celery worker - see docs/architecture.md
for why that's a deliberate choice rather than a missing feature. Multiple
copies of this process can run at once (`python -m app.workers.loop`
multiple times, or multiple containers); WorkflowRepository.claim_next_queued
uses SELECT ... FOR UPDATE SKIP LOCKED so they never claim the same run.

Startup does one extra thing a Celery-based design gets for free: recovering
runs left in 'running' by a process that died mid-execution. Because
LangGraph's checkpoint is the source of truth for *where* a run was, all
recovery has to do is find those rows and resume them - it does not need to
know what stage they were in.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import WorkflowRun, WorkflowStatus
from app.db.session import session_scope
from app.graph.checkpointer import close_checkpointer, open_checkpointer
from app.graph.runner import execute_new_run, resume_crashed_run
from app.repositories.workflow import WorkflowRepository

logger = logging.getLogger(__name__)
settings = get_settings()
WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


async def recover_orphaned_runs() -> None:
    """Runs left in 'running' at process start were mid-flight when
    something killed the previous process. Their checkpoint already
    reflects the last node that finished; resuming just continues them."""
    async with session_scope() as session:
        result = await session.execute(
            select(WorkflowRun).where(WorkflowRun.status == WorkflowStatus.running)
        )
        orphaned = list(result.scalars().all())

    for run in orphaned:
        logger.info("Recovering orphaned run %s (was mid-flight at %s)", run.id, run.current_node)
        await _run_and_handle_outcome(run.id, resume_crashed_run)


async def _run_and_handle_outcome(run_id: uuid.UUID, execute) -> None:
    try:
        await execute(run_id)
    except Exception as exc:  # noqa: BLE001 - top-level worker boundary, must not crash the loop
        async with session_scope() as session:
            repo = WorkflowRepository(session)
            retry_count = await repo.increment_retry(run_id)
            if retry_count is None:
                # The run row doesn't exist - deleted out from under us
                # between being claimed/recovered and this handler
                # running, or a stale/bogus run_id some other way. There
                # is nothing to bump a retry_count on and nothing to mark
                # retry/failed: log it explicitly and move on rather than
                # letting the AttributeError from indexing into a None
                # bring down the whole worker loop.
                logger.error(
                    "Run %s failed and no longer exists in the database "
                    "(deleted, or never existed) - nothing to retry or "
                    "update, skipping: %s",
                    run_id, exc,
                )
                return
            if retry_count < settings.max_node_retries:
                logger.warning("Run %s failed (attempt %s), will retry: %s", run_id, retry_count, exc)
                await repo.update_status(run_id, WorkflowStatus.retry, error=str(exc))
            else:
                logger.error("Run %s failed permanently after %s attempts: %s", run_id, retry_count, exc)
                await repo.update_status(run_id, WorkflowStatus.failed, error=str(exc))


async def poll_once() -> bool:
    """Claims and executes one run, if any is available. Returns whether
    a run was claimed, so the caller can back off when the queue is empty."""
    async with session_scope() as session:
        run = await WorkflowRepository(session).claim_next_queued(WORKER_ID)
        if run is None:
            return False
        run_id = run.id
        is_retry = run.retry_count > 0

    execute = resume_crashed_run if is_retry else execute_new_run
    await _run_and_handle_outcome(run_id, execute)
    return True


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await open_checkpointer()
    try:
        await recover_orphaned_runs()
        logger.info(
            "Worker %s started, polling every %.1fs", WORKER_ID, settings.worker_poll_interval_seconds
        )
        while True:
            claimed = await poll_once()
            if not claimed:
                await asyncio.sleep(settings.worker_poll_interval_seconds)
    finally:
        await close_checkpointer()


if __name__ == "__main__":
    asyncio.run(main())
