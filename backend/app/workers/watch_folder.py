"""
Filesystem watcher for the "new documents keep arriving into a watched
location" requirement in the brief. This is a genuinely distinct thing
from "a user uploads a document and clicks start" - a file appearing in
watch_dir is picked up and run with no human or external caller
triggering it.

A simple polling loop, not inotify-based - portable across platforms
(inotify is Linux-only; watchdog's cross-platform abstraction is an
extra dependency for a capability a 2-second poll already delivers) and
trivially testable without OS-level event mocking, at the cost of up to
poll_interval latency before a new file is noticed. That tradeoff is
explicit, not hidden.

Every file found goes through the exact same store_upload() and
create_run() functions the REST API and MCP server use - there is no
separate "watcher ingestion path" to drift out of sync with the rest of
the system. Idempotent on restart for free: store_upload() already
rejects a duplicate content hash, so re-scanning a directory after a
crash or restart naturally skips files already ingested. No separate
"have I seen this file" tracking table is needed - the documents table
already is that tracking, keyed by content rather than by filename (so
even a renamed-and-redropped copy of an already-ingested file is
correctly recognized as a duplicate, not re-run).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.db.session import session_scope
from app.graph.checkpointer import close_checkpointer, open_checkpointer
from app.graph.runner import create_run
from app.services.ingestion import DuplicateDocumentError, store_upload

logger = logging.getLogger(__name__)
settings = get_settings()

_SUPPORTED_EXTENSIONS = {"pdf", "docx", "txt", "md"}


async def scan_once(watch_dir: Path) -> list[uuid.UUID]:
    """Scans watch_dir once for files not yet ingested, uploads each
    through the standard ingestion path, and starts a run over it.
    Returns the run ids started this scan - empty if nothing new was
    found, which is the normal case on most polls."""
    if not watch_dir.exists():
        return []

    started_runs: list[uuid.UUID] = []
    for path in sorted(watch_dir.iterdir()):
        if not path.is_file():
            continue
        ext = path.suffix.lstrip(".").lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            continue

        data = path.read_bytes()
        async with session_scope() as session:
            try:
                document = await store_upload(session, path.name, ext, data)
            except DuplicateDocumentError:
                continue  # already ingested on a prior scan (or via API/MCP) - not new

        run = await create_run([document.id], [])
        started_runs.append(run.id)
        logger.info("Watched location: ingested %s, started run %s", path.name, run.id)

    return started_runs


async def watch_forever(watch_dir: Path | None = None, poll_interval: float | None = None) -> None:
    watch_dir = watch_dir or Path(settings.watch_dir)
    poll_interval = poll_interval if poll_interval is not None else settings.watch_poll_interval_seconds
    watch_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Watching %s every %.1fs for new documents", watch_dir, poll_interval)
    while True:
        try:
            await scan_once(watch_dir)
        except Exception:
            logger.exception("Error during watch scan - continuing")
        await asyncio.sleep(poll_interval)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    await open_checkpointer()
    try:
        await watch_forever()
    finally:
        await close_checkpointer()


if __name__ == "__main__":
    asyncio.run(_main())
