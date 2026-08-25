from __future__ import annotations

import mimetypes
import uuid

from httpx import AsyncClient

from tests.conftest import fixture_bytes


async def upload_fixture(client: AsyncClient, filename: str) -> uuid.UUID:
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    resp = await client.post(
        "/documents",
        files={"file": (filename, fixture_bytes(filename), media_type)},
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


async def create_rule(client: AsyncClient, name: str, source_type: str, rule_text: str) -> uuid.UUID:
    resp = await client.post(
        "/rules", json={"name": name, "source_type": source_type, "rule_text": rule_text}
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


async def start_run(client: AsyncClient, document_ids: list[uuid.UUID], rule_ids: list[uuid.UUID] | None = None) -> uuid.UUID:
    resp = await client.post(
        "/runs",
        json={
            "document_ids": [str(d) for d in document_ids],
            "rule_ids": [str(r) for r in (rule_ids or [])],
        },
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])
