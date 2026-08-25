"""
Application configuration.

Every setting that could plausibly differ between a laptop, CI, and a real
deployment lives here and nowhere else, loaded from the environment. Nothing
in this file is a secret's default value - LLM_API_KEY has no default, so a
misconfigured environment fails loudly at startup instead of silently calling
a real API with an empty key.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database -----------------------------------------------------
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/doctask"
    # Sync URL is needed for Alembic and for the LangGraph Postgres
    # checkpointer, which speaks psycopg (sync) rather than asyncpg.
    # Sync URL is needed for the LangGraph Postgres checkpointer, which
    # speaks psycopg directly (not through SQLAlchemy) and expects a plain
    # DSN with no "+driver" suffix.
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5432/doctask"
    # Same database, SQLAlchemy-flavored URL (explicit psycopg3 driver) -
    # used only by Alembic's sync engine.
    database_url_alembic: str = "postgresql+psycopg://postgres:postgres@localhost:5432/doctask"

    # --- LLM provider ---------------------------------------------------
    # "fake" is a deterministic, offline provider. It is what tests and CI
    # use so the test suite never needs a live key. "openai" is the real
    # provider, selected explicitly for local/dev/prod use.
    llm_provider: Literal["fake", "openai"] = "fake"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4.1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # --- Auth -------------------------------------------------------------
    # Single-reviewer API key auth. See docs/architecture.md for why this
    # is a deliberate cut instead of full JWT/multi-tenant auth.
    api_key: str = "dev-local-key"

    # --- Workflow ----------------------------------------------------------
    max_node_retries: int = 3
    worker_poll_interval_seconds: float = 1.0
    grounding_similarity_threshold: float = 0.45

    # --- Storage -------------------------------------------------------
    upload_dir: str = "./data/uploads"

    # --- Watched location ------------------------------------------------
    # See app/workers/watch_folder.py. A directory that, when a file
    # appears in it, is automatically ingested and run - the concrete
    # implementation of "new documents keep arriving into a watched
    # location" from the brief, as opposed to only accepting documents via
    # an explicit upload call.
    watch_dir: str = "./data/watched"
    watch_poll_interval_seconds: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
