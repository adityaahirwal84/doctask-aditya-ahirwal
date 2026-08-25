"""
One-shot entrypoint that migrates the LangGraph Postgres checkpoint
schema and then exits.

Run as part of the `migrate` docker-compose service, chained after
`alembic upgrade head` and before api/worker/watcher start - see
docker-compose.yml and the module docstring in app/graph/checkpointer.py
for why this has to be a separate, single, exiting process rather than
something api/worker/watcher each do for themselves at their own
startup.

Run with: python -m app.graph.bootstrap_checkpointer
"""

from __future__ import annotations

import asyncio
import logging

from app.graph.checkpointer import bootstrap_checkpointer_schema

logger = logging.getLogger(__name__)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Migrating LangGraph checkpoint schema...")
    await bootstrap_checkpointer_schema()
    logger.info("LangGraph checkpoint schema is up to date.")


if __name__ == "__main__":
    asyncio.run(_main())
