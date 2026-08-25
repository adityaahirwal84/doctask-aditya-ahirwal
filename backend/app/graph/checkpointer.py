"""
The checkpointer is what makes the graph survive a killed process. It is
opened once at process startup - independently by the API app's lifespan,
by the worker process, by the watcher process, and by the MCP server,
since each is a separate OS process (separate `docker compose` container,
in three of the four cases) with no shared Python state - and reused for
every run. LangGraph writes a checkpoint to Postgres after every node
completes, keyed by thread_id (which we set to the workflow run's id),
and resuming a run is just invoking the graph again against the same
thread_id with no new input.

THE SCHEMA MIGRATION HAS TO HAPPEN EXACTLY ONCE, FROM A PROCESS THAT
EXITS AFTERWARDS - not "once per process, coordinated via a lock".

The first fix for this module serialized AsyncPostgresSaver.setup()
across api/worker/watcher with a Postgres advisory lock, since all three
call open_checkpointer() independently at startup and setup() runs
`CREATE TABLE IF NOT EXISTS`-style migrations that race under true
concurrency. That stopped setup() from running *concurrently*, but not
from stepping on *uncommitted* work: setup() runs on the saver's own
long-lived connection - the same one that then stays open and gets
reused for every checkpoint read/write for the rest of that process's
life - and nothing forces that connection to commit the
checkpoint_migrations row until much later. So api could hold the lock,
apply migration v=7, and release the lock without that insert being
visible yet; worker would then acquire the lock, see no v=7 row, try to
insert it too, and - once api's transaction eventually did commit - hit
`duplicate key value violates unique constraint "checkpoint_migrations_pkey"`,
`Key (v)=(7) already exists`. A lock only orders who goes first; it
doesn't make one process's writes visible to the next before the next
one looks.

The fix is to stop running setup() from api/worker/watcher altogether.
Instead:
  - bootstrap_checkpointer_schema() runs the migration, in a short-lived
    connection it opens and fully closes itself, then returns. It is
    called by app/graph/bootstrap_checkpointer.py, a one-shot script run
    as part of the `migrate` docker-compose service (chained after
    `alembic upgrade head`), which then EXITS. Because api/worker/watcher
    all depend on `migrate` reaching `service_completed_successfully`,
    none of them can start until that process - and its connection, and
    its transaction - is completely gone. There is no other writer of
    checkpoint_migrations, ever, so there is no window for two writes to
    race, committed or not. bootstrap_checkpointer_schema() is still
    advisory-lock-guarded and safe to re-run on an already-migrated
    database (e.g. a later `docker compose up` after a `down` with
    volumes kept), but that's defense in depth, not the load-bearing
    part of the fix.
  - open_checkpointer(), used by api/worker/watcher/MCP server, only
    opens a connection pool against the schema bootstrap already
    migrated. It never calls setup() and never touches
    checkpoint_migrations, so it's safe to call from any number of
    processes at once, in any order, regardless of what the others are
    doing.
"""

from __future__ import annotations

import asyncio
import zlib
from contextlib import AsyncExitStack

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import get_settings

_stack: AsyncExitStack | None = None
_saver: AsyncPostgresSaver | None = None

# Arbitrary but fixed key for the session-level advisory lock used by
# bootstrap_checkpointer_schema(). Kept as defense-in-depth against the
# bootstrap step itself being invoked more than once concurrently (it
# isn't, under normal `docker compose up`, since `migrate` is a single
# one-shot service) - not relied on for correctness against
# api/worker/watcher, which no longer call setup() at all.
_SETUP_LOCK_KEY = zlib.crc32(b"doctask.langgraph_checkpointer.setup")


async def bootstrap_checkpointer_schema(
    dsn: str | None = None,
    *,
    lock_timeout: float = 60.0,
    poll_interval: float = 0.2,
) -> None:
    """Creates/migrates the LangGraph checkpoint schema, then fully
    closes the connection it used, so the migration is guaranteed
    committed and visible before this function returns.

    Meant to be called exactly once, by a process that exits right after
    (see app/graph/bootstrap_checkpointer.py, run as part of the
    `migrate` docker-compose service, before api/worker/watcher start) -
    never by api/worker/watcher/MCP themselves. Safe to call again later
    on an already-migrated database (e.g. a subsequent `docker compose
    up` with volumes kept): setup() checks checkpoint_migrations before
    applying anything, and since this always runs to completion and
    fully exits before anyone else touches the database, there's no
    concurrent writer for it to race against.

    Lock acquisition is a bounded, NON-blocking poll loop
    (pg_try_advisory_lock + asyncio.sleep), not a blocking pg_advisory_lock
    call held open on a dedicated connection. That distinction matters
    under real concurrency: an earlier version used pg_advisory_lock, which
    blocks *inside Postgres* until granted - so every concurrent caller
    held its own connection open, idle, for the whole wait. With several
    callers doing that at once (this function being invoked concurrently,
    plus api/worker/watcher/MCP each already holding their own long-lived
    checkpoint/DB connections), those idle-but-open waiting connections
    compete for the same connection budget as the connection the eventual
    lock-holder needs to actually run setup() on - and since none of the
    waiters release their connection until *after* they get the lock,
    which cannot happen until the holder finishes, which the holder cannot
    do without a connection of its own, the whole thing could wedge solid
    under connection pressure. Polling with pg_try_advisory_lock never
    holds a connection open while merely waiting - each attempt opens,
    tries, and closes if it lost - so waiters never compete with the
    holder for connection slots. lock_timeout is a hard ceiling so a
    genuine deadlock or a stuck holder fails loudly instead of hanging
    forever.
    """
    dsn = dsn or get_settings().database_url_sync
    loop = asyncio.get_running_loop()
    deadline = loop.time() + lock_timeout

    while True:
        lock_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        try:
            cur = await lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (_SETUP_LOCK_KEY,))
            (acquired,) = await cur.fetchone()
            if acquired:
                try:
                    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
                        await saver.setup()
                finally:
                    # Same connection that acquired the lock must release
                    # it - session-level advisory locks are per-backend.
                    await lock_conn.execute("SELECT pg_advisory_unlock(%s)", (_SETUP_LOCK_KEY,))
                return
        finally:
            # Always released before the next poll attempt (or before
            # returning) - no connection is ever left open merely to wait.
            await lock_conn.close()

        if loop.time() >= deadline:
            raise TimeoutError(
                f"Timed out after {lock_timeout}s waiting for the checkpoint schema "
                "advisory lock - another bootstrap_checkpointer_schema() call may be "
                "stuck holding it."
            )
        await asyncio.sleep(poll_interval)


async def open_checkpointer() -> AsyncPostgresSaver:
    """Opens this process's checkpoint connection pool against the
    already-migrated schema. Does NOT run setup()/migrations - that is
    bootstrap_checkpointer_schema()'s job, and it has already run to
    completion (as part of the `migrate` service) before this process
    ever starts. Safe to call concurrently from api, worker, watcher, and
    the MCP server: it only opens a pool and never writes to
    checkpoint_migrations, so there's nothing for concurrent callers to
    race on."""
    global _stack, _saver
    if _saver is not None:
        return _saver
    settings = get_settings()
    stack = AsyncExitStack()
    saver = await stack.enter_async_context(
        AsyncPostgresSaver.from_conn_string(settings.database_url_sync)
    )
    _stack, _saver = stack, saver
    return _saver


async def close_checkpointer() -> None:
    global _stack, _saver
    if _stack is not None:
        await _stack.aclose()
    _stack, _saver = None, None


def get_checkpointer() -> AsyncPostgresSaver:
    if _saver is None:
        raise RuntimeError("Checkpointer not opened - call open_checkpointer() at startup first.")
    return _saver
