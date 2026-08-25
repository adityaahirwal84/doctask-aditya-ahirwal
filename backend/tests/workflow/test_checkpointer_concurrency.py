"""
Regression tests for two checkpointer initialization bugs, and for the
lifecycle architecture that fixes both at once: schema migration
(AsyncPostgresSaver.setup()) runs exactly once, from
bootstrap_checkpointer_schema(), in a process that fully exits before
api/worker/watcher start; those runtime processes only ever open a
connection pool (open_checkpointer()) and never call setup() themselves.
See app/graph/checkpointer.py's module docstring for the full story.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import session_scope
from app.graph.checkpointer import bootstrap_checkpointer_schema, get_checkpointer, open_checkpointer

pytestmark = pytest.mark.asyncio

settings = get_settings()

_CHECKPOINT_TABLES = (
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoints",
    "checkpoint_migrations",
)


async def _drop_checkpoint_tables() -> None:
    async with await psycopg.AsyncConnection.connect(
        settings.database_url_sync, autocommit=True
    ) as conn:
        await conn.execute(f"DROP TABLE IF EXISTS {', '.join(_CHECKPOINT_TABLES)} CASCADE")


async def _checkpoint_tables_present() -> set[str]:
    async with session_scope() as session:
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(:names)"
            ),
            {"names": list(_CHECKPOINT_TABLES)},
        )
        return {row[0] for row in result}


async def test_bootstrap_is_safe_to_rerun_on_an_already_migrated_database():
    """Exact regression for the second bug report.

    `docker compose up` after a previous, successful `docker compose
    up`/`down` cycle (no volumes wiped) re-runs the `migrate` service -
    and therefore bootstrap_checkpointer_schema() - against a database
    that is already fully migrated. That used to raise:

        psycopg.errors.UniqueViolation: duplicate key value violates
        unique constraint "checkpoint_migrations_pkey"
        DETAIL: Key (v)=(7) already exists.

    A completed, exited bootstrap run followed by a second, independent
    one must be a no-op the second time, not an error - this is the
    literal "fresh docker compose up works without manual DB
    intervention" acceptance criterion.
    """
    await _drop_checkpoint_tables()

    await bootstrap_checkpointer_schema()  # first "docker compose up"
    assert await _checkpoint_tables_present() == set(_CHECKPOINT_TABLES)

    await bootstrap_checkpointer_schema()  # second "docker compose up", nothing wiped in between
    assert await _checkpoint_tables_present() == set(_CHECKPOINT_TABLES)


async def test_concurrent_bootstrap_invocations_do_not_race():
    """Defense in depth: `migrate` is a single one-shot compose service,
    so bootstrap_checkpointer_schema() should never actually be invoked
    concurrently with itself in practice - but nothing stops a developer
    from running the bootstrap command by hand a second time while
    another copy is mid-flight. The advisory lock inside
    bootstrap_checkpointer_schema() must still serialize that safely
    rather than reproducing the original duplicate-key race.

    Bounded with an explicit timeout: bootstrap_checkpointer_schema()
    itself now has a lock_timeout (default 60s) that raises TimeoutError
    instead of blocking forever, but this test's own wait_for is an
    independent, test-level ceiling - it must fail loudly with a clear
    message rather than hang the test run if a future regression
    reintroduces a wedge (e.g. someone changes the lock back to a
    connection-holding blocking wait), which is exactly the class of bug
    this test caught in Docker/Postgres (see checkpointer.py's
    bootstrap_checkpointer_schema docstring for the root cause: the
    previous implementation held one open, idle, blocked-in-Postgres
    connection per waiting caller for the entire wait, and those idle
    connections competed for the same connection budget the eventual
    lock-holder needed to open its own setup() connection - under real
    concurrency, against a real Postgres server also serving
    already-running api/worker/watcher/MCP connections, that could wedge
    solid with nothing left to time out or error, only to sit blocked
    forever)."""
    await _drop_checkpoint_tables()

    try:
        await asyncio.wait_for(
            asyncio.gather(*(bootstrap_checkpointer_schema() for _ in range(6))),
            timeout=90,
        )
    except asyncio.TimeoutError:
        pytest.fail(
            "6-way concurrent bootstrap_checkpointer_schema() did not complete within "
            "90s - this points at a connection-starvation/deadlock regression in the "
            "advisory-lock acquisition, not a genuinely slow migration."
        )

    assert await _checkpoint_tables_present() == set(_CHECKPOINT_TABLES)


async def test_runtime_processes_share_the_schema_without_calling_setup():
    """api, worker, watcher, and the MCP server each call
    open_checkpointer() independently at their own startup, all pointed
    at the same already-migrated database. None of them call setup() or
    touch checkpoint_migrations anymore - open_checkpointer() only opens
    a connection pool - so simulating several such processes opening a
    saver at the same time must never raise, regardless of what order
    they start in."""

    async def open_as_a_separate_process() -> None:
        async with AsyncPostgresSaver.from_conn_string(settings.database_url_sync) as saver:
            # Opening the pool is the whole point of open_checkpointer();
            # a real read against it confirms the pool is actually
            # talking to a fully-migrated schema, not just constructed.
            await saver.aget_tuple({"configurable": {"thread_id": "no-such-thread"}})

    await asyncio.gather(*(open_as_a_separate_process() for _ in range(6)))


async def test_open_checkpointer_is_a_process_local_singleton(_checkpointer):
    """Within a single process, open_checkpointer() must hand back the
    same cached saver on every call rather than opening a new pool each
    time - api and worker code call it defensively in several places."""
    already_open = get_checkpointer()
    savers = await asyncio.gather(*(open_checkpointer() for _ in range(5)))
    assert all(s is already_open for s in savers)
