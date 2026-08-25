"""
Verifies the actual distinction the audit called out: a file appearing in
a watched directory is ingested and run with no explicit upload call or
human action - not the same thing as "a user uploads and clicks start".
"""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.session import session_scope
from app.repositories.documents import DocumentRepository
from app.repositories.workflow import WorkflowRepository
from app.workers.watch_folder import scan_once
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.asyncio


@pytest.fixture
def watch_dir(tmp_path) -> Path:
    d = tmp_path / "watched"
    d.mkdir()
    return d


async def test_scan_ingests_a_new_file_and_starts_a_run_automatically(watch_dir):
    shutil.copy(FIXTURES_DIR / "contract.txt", watch_dir / "contract.txt")

    run_ids = await scan_once(watch_dir)

    assert len(run_ids) == 1
    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_ids[0])
        assert run is not None
        assert len(run.document_ids) == 1

        import hashlib

        doc_repo = DocumentRepository(session)
        document = await doc_repo.get_by_content_hash(
            hashlib.sha256((watch_dir / "contract.txt").read_bytes()).hexdigest()
        )
        assert document is not None
        assert document.filename == "contract.txt"


async def test_scanning_again_does_not_reingest_or_rerun_the_same_file(watch_dir):
    shutil.copy(FIXTURES_DIR / "contract.txt", watch_dir / "contract.txt")

    first_scan = await scan_once(watch_dir)
    assert len(first_scan) == 1

    # The file is still sitting in the directory (a real watcher doesn't
    # delete what it processed) - the next poll must not re-ingest it or
    # start a second run, exactly the "no completed work should rerun"
    # property the rest of the system guarantees.
    second_scan = await scan_once(watch_dir)
    assert second_scan == []

    async with session_scope() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM documents"))
        assert result.scalar() == 1
        result = await session.execute(text("SELECT COUNT(*) FROM workflow_runs"))
        assert result.scalar() == 1


async def test_scan_picks_up_a_second_new_file_dropped_later(watch_dir):
    shutil.copy(FIXTURES_DIR / "contract.txt", watch_dir / "contract.txt")
    first_scan = await scan_once(watch_dir)
    assert len(first_scan) == 1

    # A second document "arrives" - simulating it showing up after the
    # first poll, not being present from the start.
    shutil.copy(FIXTURES_DIR / "invoice_1042.txt", watch_dir / "invoice_1042.txt")
    second_scan = await scan_once(watch_dir)
    assert len(second_scan) == 1

    async with session_scope() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM documents"))
        assert result.scalar() == 2
        result = await session.execute(text("SELECT COUNT(*) FROM workflow_runs"))
        assert result.scalar() == 2


async def test_scan_ignores_unsupported_file_extensions(watch_dir):
    (watch_dir / "notes.exe").write_bytes(b"not a real document")
    (watch_dir / "readme.md").write_text("# Notes\n\nSome markdown content that is a real supported format.")

    run_ids = await scan_once(watch_dir)

    assert len(run_ids) == 1  # only the .md file, not the .exe
    async with session_scope() as session:
        result = await session.execute(text("SELECT filename FROM documents"))
        filenames = [row[0] for row in result]
        assert filenames == ["readme.md"]


async def test_scan_on_a_nonexistent_directory_returns_empty_without_error(tmp_path):
    missing_dir = tmp_path / "does_not_exist_yet"
    run_ids = await scan_once(missing_dir)
    assert run_ids == []
