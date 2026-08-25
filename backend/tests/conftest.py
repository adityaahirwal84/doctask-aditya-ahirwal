"""
Test configuration. The environment variables at the top of this file are
what make the entire test suite run offline: LLM_PROVIDER=fake means no
module anywhere in app/ ever imports the OpenAI SDK path, and no test here
needs, or checks for, an API key. This has to happen before any `app.*`
module is imported anywhere (including indirectly via other fixtures),
because app.core.config.get_settings() is process-wide cached.
"""

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/doctask_test"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql://postgres:postgres@localhost:5432/doctask_test"
)
os.environ.setdefault(
    "DATABASE_URL_ALEMBIC", "postgresql+psycopg://postgres:postgres@localhost:5432/doctask_test"
)
os.environ.setdefault("UPLOAD_DIR", "/tmp/doctask_test_uploads")

import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: F401,E402
from app.graph.checkpointer import (  # noqa: E402
    bootstrap_checkpointer_schema,
    close_checkpointer,
    open_checkpointer,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "docs"
settings = get_settings()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_database():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _checkpointer(_prepare_database):
    # Mirrors the `migrate` docker-compose service: migrate the
    # checkpoint schema once, fully committed, before any "process"
    # (here, the rest of the test session) opens a saver against it -
    # open_checkpointer() itself no longer runs setup().
    await bootstrap_checkpointer_schema()
    await open_checkpointer()
    yield
    await close_checkpointer()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(_prepare_database):
    """Truncates every app table between tests. Random UUID primary keys
    and per-test thread ids mean tests never collide, but starting from an
    empty table set keeps assertions ("there is exactly one open
    conflict") simple and honest rather than scoped with extra filters."""
    engine = create_async_engine(settings.database_url)
    table_names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    yield


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"x-api-key": settings.api_key}
    ) as ac:
        yield ac


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


@pytest.fixture
def new_run_id() -> uuid.UUID:
    return uuid.uuid4()
