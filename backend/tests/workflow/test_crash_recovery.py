"""
Crash recovery, proven with an actual killed OS process, not a caught
Python exception standing in for one.

The test: start a run over two documents, launch a real subprocess that
begins executing it, kill that subprocess with SIGKILL the instant the
first document's chunks land in the database (i.e. mid-graph, after one
node has committed but before the run finished), then launch a second,
independent subprocess to resume it. If crash-resume genuinely works, the
first document is parsed exactly once - not reprocessed - and the run
reaches waiting_approval/completed via the second process alone.
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import time
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Chunk, Document, WorkflowStatus
from app.db.session import session_scope
from app.repositories.workflow import WorkflowRepository
from tests.utils import start_run, upload_fixture

pytestmark = pytest.mark.asyncio

_WORKER_SNIPPET = textwrap.dedent(
    """
    import asyncio, os, sys
    sys.path.insert(0, os.environ["APP_ROOT"])
    from app.graph.checkpointer import open_checkpointer, close_checkpointer
    from app.graph.runner import execute_new_run
    import uuid

    async def main():
        await open_checkpointer()
        try:
            await execute_new_run(uuid.UUID(sys.argv[1]))
        finally:
            await close_checkpointer()

    asyncio.run(main())
    """
)


async def _document_parsed_count(document_id: uuid.UUID) -> int:
    async with session_scope() as session:
        result = await session.execute(select(Chunk).where(Chunk.document_id == document_id))
        return len(list(result.scalars().all()))


async def test_killed_process_resumes_without_reprocessing_completed_document(client, tmp_path):
    contract_id = await upload_fixture(client, "contract.txt")
    invoice_id = await upload_fixture(client, "invoice_1042.txt")
    run_id = await start_run(client, [contract_id, invoice_id])

    script_path = tmp_path / "run_once.py"
    script_path.write_text(_WORKER_SNIPPET)
    app_root = str((__import__("pathlib").Path(__file__).parents[2]))

    env = dict(os.environ)
    env["APP_ROOT"] = app_root
    env["LLM_PROVIDER"] = "fake"
    # Mirror the *actual* database this test process itself is talking to
    # (see _document_parsed_count/session_scope calls throughout this
    # test), rather than a hardcoded guess. get_settings() resolves from
    # the environment exactly like the subprocess's own app.core.config
    # import will, so this is correct in every environment this test
    # suite runs in:
    #   - inside `docker compose exec worker pytest ...`: DATABASE_URL is
    #     already set by docker-compose to postgresql://...@db:5432/doctask
    #     (Postgres is a separate "db" service/container, not localhost,
    #     and there is no separate "doctask_test" database) - this is
    #     what os.environ actually contains here, and get_settings() picks
    #     it up.
    #   - running the suite directly on a machine with no Docker: nothing
    #     sets DATABASE_URL, so conftest.py's os.environ.setdefault(...)
    #     falls back to postgresql://...@localhost:5432/doctask_test, and
    #     get_settings() picks that up instead.
    # A subprocess that hardcodes either one breaks in the other
    # environment; asking get_settings() for the value this process is
    # itself using never can.
    settings = get_settings()
    env["DATABASE_URL"] = settings.database_url
    env["DATABASE_URL_SYNC"] = settings.database_url_sync
    env["UPLOAD_DIR"] = os.environ.get("UPLOAD_DIR", "/tmp/doctask_test_uploads")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(script_path), str(run_id),
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    # Poll the DB until the first document's chunks exist (proof the first
    # ingest node genuinely completed and committed), then kill -9 the
    # process immediately - this is a real, unrecoverable-by-Python crash.
    deadline = time.monotonic() + 15
    first_doc_done = False
    while time.monotonic() < deadline:
        if await _document_parsed_count(contract_id) > 0:
            first_doc_done = True
            break
        await asyncio.sleep(0.05)
    assert first_doc_done, "first document never finished ingesting before timeout"

    proc.kill()
    await proc.wait()
    assert proc.returncode != 0  # genuinely killed, not a clean exit

    chunks_after_kill = await _document_parsed_count(contract_id)
    assert chunks_after_kill > 0  # first document's work survived the kill

    # The run is still marked 'running' - orphaned by the dead process.
    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        assert run.status == WorkflowStatus.running

    # A second, independent process resumes it - simulating the worker
    # fleet's recover_orphaned_runs() picking this up after a real restart.
    resume_script = tmp_path / "resume.py"
    resume_script.write_text(
        textwrap.dedent(
            """
            import asyncio, os, sys
            sys.path.insert(0, os.environ["APP_ROOT"])
            from app.graph.checkpointer import open_checkpointer, close_checkpointer
            from app.graph.runner import resume_crashed_run
            import uuid

            async def main():
                await open_checkpointer()
                try:
                    await resume_crashed_run(uuid.UUID(sys.argv[1]))
                finally:
                    await close_checkpointer()

            asyncio.run(main())
            """
        )
    )
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable, str(resume_script), str(run_id),
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc2.communicate(), timeout=30)
    assert proc2.returncode == 0, stderr.decode()

    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        assert run.status in (WorkflowStatus.waiting_approval, WorkflowStatus.completed)

    # The critical assertion: the contract's chunks were created exactly
    # once. If the ingest node had re-run from scratch after the crash
    # instead of resuming past it, this document would have been
    # re-parsed and would show duplicate chunks (content_hash dedup only
    # protects re-*uploads*, not an in-run re-ingest).
    final_chunk_count = await _document_parsed_count(contract_id)
    assert final_chunk_count == chunks_after_kill, (
        "contract was reprocessed after resume - completed work was redone"
    )

    # And the second document, which the killed process never reached,
    # was picked up and completed by the resuming process.
    assert await _document_parsed_count(invoice_id) > 0
