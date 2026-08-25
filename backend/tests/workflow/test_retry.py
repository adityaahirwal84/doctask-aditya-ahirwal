"""
Requirement 1 ("some of those decisions must be able to change the
path: a retry...") was previously satisfied only by declaring a
RetryPolicy on I/O-bound nodes (app/graph/build.py) - no test ever forced
a node to fail and checked what actually happens. This file closes that
gap with three tests, and one of them overturned an assumption the first
version of this test file made: LangGraph's default retry_on filter
deliberately does NOT retry RuntimeError, ValueError, TypeError, OSError,
and a handful of other "this looks like a bug, not a transient failure"
exception types (see langgraph.types.RetryPolicy's default_retry_on,
inspected directly rather than assumed). ConnectionError, HTTP 5xx
errors, and any exception type not on that list ARE retried - which
covers what a real dropped DB connection (sqlalchemy.exc.OperationalError,
asyncpg's connection errors) or LLM API transient failure actually
raises, verified by checking their MRO against the exclusion list before
relying on it.
"""

import logging
import uuid

import pytest

import app.graph.nodes as nodes_module
from app.db.models import WorkflowStatus
from app.db.session import session_scope
from app.graph.runner import execute_new_run
from app.repositories.workflow import WorkflowRepository
from app.workers.loop import _run_and_handle_outcome
from tests.utils import start_run, upload_fixture

pytestmark = pytest.mark.asyncio


async def test_transient_connection_error_is_retried_and_the_run_recovers(client, monkeypatch):
    """ConnectionError is explicitly always-retried by LangGraph's default
    policy - the realistic stand-in for a dropped DB connection or a
    flaky network call."""
    contract_id = await upload_fixture(client, "contract.txt")
    run_id = await start_run(client, [contract_id])

    call_count = {"n": 0}
    real_parse = nodes_module.parse_classify_extract_embed

    async def flaky_parse(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ConnectionError("simulated dropped DB connection")
        return await real_parse(*args, **kwargs)

    monkeypatch.setattr(nodes_module, "parse_classify_extract_embed", flaky_parse)

    await execute_new_run(run_id)

    assert call_count["n"] >= 2, "the node was never actually retried - it only ran once"

    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        assert run.status in (WorkflowStatus.waiting_approval, WorkflowStatus.completed), (
            f"run did not recover from the transient failure, status={run.status}"
        )
        assert run.retry_count == 0, (
            "the in-node RetryPolicy should have absorbed this failure - it should never "
            "have reached the worker's own top-level retry bookkeeping"
        )


async def test_persistently_failing_retryable_error_exhausts_max_attempts(client, monkeypatch):
    """A ConnectionError that never clears must not retry forever - it
    should exhaust RetryPolicy's max_attempts and then surface as a
    worker-level failure, exactly like a real outage that doesn't
    resolve within the retry window."""
    contract_id = await upload_fixture(client, "contract.txt")
    run_id = await start_run(client, [contract_id])

    call_count = {"n": 0}

    async def always_fails(*args, **kwargs):
        call_count["n"] += 1
        raise ConnectionError("simulated persistent outage")

    monkeypatch.setattr(nodes_module, "parse_classify_extract_embed", always_fails)

    await _run_and_handle_outcome(run_id, execute_new_run)

    # settings.max_node_retries=3 by default - the node should have been
    # attempted multiple times, not once, before the worker saw a failure.
    assert call_count["n"] >= 2, f"expected multiple retry attempts, only saw {call_count['n']}"

    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        assert run.status in (WorkflowStatus.retry, WorkflowStatus.failed)
        assert run.error is not None
        assert "simulated persistent outage" in run.error


async def test_non_retryable_exception_type_fails_fast_without_retrying(client, monkeypatch):
    """RuntimeError (and ValueError, TypeError, OSError, ...) are treated
    by LangGraph as bugs, not transient conditions, and are deliberately
    NOT retried - proving this matters as much as proving retry works,
    since silently retrying a genuine bug would just waste time before
    failing anyway."""
    contract_id = await upload_fixture(client, "contract.txt")
    run_id = await start_run(client, [contract_id])

    call_count = {"n": 0}

    async def buggy_parse(*args, **kwargs):
        call_count["n"] += 1
        raise RuntimeError("simulated bug, e.g. a bad assumption in extraction logic")

    monkeypatch.setattr(nodes_module, "parse_classify_extract_embed", buggy_parse)

    await _run_and_handle_outcome(run_id, execute_new_run)

    assert call_count["n"] == 1, (
        "RuntimeError should fail immediately with no in-node retry - "
        f"the node was called {call_count['n']} times instead of exactly once"
    )

    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        assert run.status in (WorkflowStatus.retry, WorkflowStatus.failed)
        assert "simulated bug" in run.error


async def test_missing_run_id_cannot_crash_the_worker(caplog):
    """Regression test for a real worker crash: a run gets claimed (or
    recovered) with a given run_id, execute() raises "Run ... not found"
    (e.g. the row was deleted, or a stale id from a previous DB state got
    picked up some other way), and the exception handler used to blindly
    call increment_retry(run_id) - which does session.get(...), gets
    None back for a nonexistent row, and then

        AttributeError: 'NoneType' object has no attribute 'retry_count'

    - crashing the exception handler itself, and therefore the worker
    loop. increment_retry() now returns None for a missing run, and
    _run_and_handle_outcome() must handle that explicitly (log and
    return) rather than dereferencing it."""
    missing_run_id = uuid.uuid4()

    async def raises_not_found(_run_id):
        raise ValueError(f"Run {missing_run_id} not found")

    # The whole point: this must not raise anything at all, in
    # particular not AttributeError, for a run_id that was never in the
    # database in the first place.
    with caplog.at_level(logging.ERROR):
        await _run_and_handle_outcome(missing_run_id, raises_not_found)

    assert any(str(missing_run_id) in record.message for record in caplog.records), (
        "the missing-run case should be logged explicitly, not silently swallowed"
    )

    # And, per requirement 3: nothing was fabricated for a run that was
    # never there - there is genuinely no row to have created or updated.
    async with session_scope() as session:
        run = await WorkflowRepository(session).get(missing_run_id)
        assert run is None


async def test_existing_run_still_retries_normally_after_the_missing_run_fix(client, monkeypatch):
    """The missing-run fix must not change behaviour for a genuine,
    existing run - this is the same assertion as the persistent-failure
    retry test above, kept here specifically to prove the None-guard in
    increment_retry()/the new branch in _run_and_handle_outcome() is a
    pure addition, not a change to the existing-run path."""
    contract_id = await upload_fixture(client, "contract.txt")
    run_id = await start_run(client, [contract_id])

    async def always_fails(*args, **kwargs):
        raise ConnectionError("simulated persistent outage")

    monkeypatch.setattr(nodes_module, "parse_classify_extract_embed", always_fails)

    await _run_and_handle_outcome(run_id, execute_new_run)

    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None
        assert run.retry_count >= 1
        assert run.status in (WorkflowStatus.retry, WorkflowStatus.failed)
        assert run.error is not None
