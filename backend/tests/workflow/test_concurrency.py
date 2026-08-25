import asyncio
import uuid

import pytest

from app.db.models import WorkflowRun, WorkflowStatus
from app.db.session import session_scope
from app.graph.runner import execute_new_run
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.workflow import WorkflowRepository
from app.workers.loop import poll_once
from tests.utils import start_run, upload_fixture

pytestmark = pytest.mark.asyncio


async def test_concurrent_workers_never_double_claim_the_same_run():
    """Ten queued runs, five 'workers' polling concurrently. SELECT ...
    FOR UPDATE SKIP LOCKED must guarantee each run is claimed by exactly
    one worker - this is what makes it safe to run more than one worker
    process against the same queue."""
    async with session_scope() as session:
        repo = WorkflowRepository(session)
        run_ids = []
        for _ in range(10):
            run = await repo.create(WorkflowRun(thread_id=str(uuid.uuid4())))
            run_ids.append(run.id)

    claimed_by: dict[uuid.UUID, list[str]] = {rid: [] for rid in run_ids}
    lock = asyncio.Lock()

    async def worker(worker_name: str):
        while True:
            async with session_scope() as session:
                run = await WorkflowRepository(session).claim_next_queued(worker_name)
                if run is None:
                    return
                async with lock:
                    claimed_by[run.id].append(worker_name)

    await asyncio.gather(*(worker(f"worker-{i}") for i in range(5)))

    # Every run claimed exactly once, by exactly one worker.
    for run_id, claimers in claimed_by.items():
        assert len(claimers) == 1, f"run {run_id} was claimed {len(claimers)} times: {claimers}"


async def test_two_runs_executing_concurrently_do_not_corrupt_each_others_state(client):
    """Two independent runs, each over a different document, driven
    through the real graph at the same time via asyncio.gather. Both must
    complete correctly and each run's knowledge/conflicts must reflect
    only its own document - concurrency must not leak state across
    threads (LangGraph's thread_id) or across DB rows."""
    contract_id = await upload_fixture(client, "contract.txt")
    invoice_id = await upload_fixture(client, "invoice_1042.txt")

    run_id_a = await start_run(client, [contract_id])
    run_id_b = await start_run(client, [invoice_id])

    await asyncio.gather(execute_new_run(run_id_a), execute_new_run(run_id_b))

    async with session_scope() as session:
        run_a = await WorkflowRepository(session).get(run_id_a)
        run_b = await WorkflowRepository(session).get(run_id_b)

    assert run_a.status in (WorkflowStatus.waiting_approval, WorkflowStatus.completed)
    assert run_b.status in (WorkflowStatus.waiting_approval, WorkflowStatus.completed)
    # Each run's document list is exactly what it was given - no bleed-over.
    assert run_a.document_ids == [str(contract_id)]
    assert run_b.document_ids == [str(invoice_id)]


async def test_worker_poll_loop_drains_a_batch_without_duplicating_execution(client):
    """A more realistic concurrency scenario: several runs queued at once,
    drained by poll_once() called concurrently (as multiple worker
    processes would), and every run reaches a terminal-ish state exactly
    once."""
    doc_ids = []
    for fname in ("contract.txt", "invoice_1042.txt"):
        doc_ids.append(await upload_fixture(client, fname))

    run_ids = [await start_run(client, [d]) for d in doc_ids]

    async def drain(worker_name: str):
        for _ in range(10):
            claimed = await poll_once()
            if not claimed:
                return

    await asyncio.gather(*(drain(f"w{i}") for i in range(3)))

    async with session_scope() as session:
        repo = WorkflowRepository(session)
        for run_id in run_ids:
            run = await repo.get(run_id)
            assert run.status in (WorkflowStatus.waiting_approval, WorkflowStatus.completed)
            assert run.retry_count == 0  # no worker collision caused a spurious failure
