"""
The state object every node reads and partially updates. Kept to plain
JSON-serializable types (strings, not UUID objects) because this is what
the Postgres checkpointer serializes on every node transition - anything
that doesn't round-trip cleanly through JSON would silently break resume.
"""

from __future__ import annotations

from typing import TypedDict


class GraphState(TypedDict):
    run_id: str
    document_ids: list[str]
    pending_document_ids: list[str]
    rule_ids: list[str]
    affected_fact_keys: list[str]
    report_id: str | None
    error: str | None
