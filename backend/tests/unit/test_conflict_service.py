import uuid

import pytest

from app.db.models import Document, DocumentFormat, DocumentStatus, KnowledgeItem, WorkflowRun
from app.db.session import session_scope
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.workflow import WorkflowRepository
from app.services.conflict import detect_and_merge

pytestmark = pytest.mark.asyncio


async def _make_document(session, suffix: str) -> Document:
    doc = Document(
        filename=f"doc_{suffix}.txt", format=DocumentFormat.txt, content_hash=f"hash_{suffix}",
        storage_path=f"/tmp/doc_{suffix}.txt", status=DocumentStatus.parsed,
    )
    return await DocumentRepository(session).create(doc)


async def _make_run(session) -> WorkflowRun:
    return await WorkflowRepository(session).create(WorkflowRun(thread_id=str(uuid.uuid4())))


async def _make_pending_item(session, document: Document, fact_key: str, fact_value: str) -> KnowledgeItem:
    from app.db.models import Chunk

    chunk = Chunk(document_id=document.id, chunk_index=0, content=fact_value)
    session.add(chunk)
    await session.flush()
    item = KnowledgeItem(
        document_id=document.id, source_chunk_id=chunk.id,
        fact_key=fact_key, fact_value=fact_value, is_current=False,
    )
    return await KnowledgeRepository(session).add(item)


async def test_first_ever_fact_auto_commits_without_conflict():
    async with session_scope() as session:
        doc = await _make_document(session, "a")
        item = await _make_pending_item(session, doc, "payment_terms_days", "30")

        conflicts, auto_committed = await detect_and_merge(session, [item], uuid.uuid4())

        assert conflicts == []
        assert auto_committed == ["payment_terms_days"]
        assert item.is_current is True


async def test_matching_value_confirms_without_conflict_or_duplicate_current():
    async with session_scope() as session:
        run = await _make_run(session)
        doc_a = await _make_document(session, "a")
        first = await _make_pending_item(session, doc_a, "payment_terms_days", "30")
        await detect_and_merge(session, [first], run.id)

        doc_b = await _make_document(session, "b")
        second = await _make_pending_item(session, doc_b, "payment_terms_days", "30")
        conflicts, auto_committed = await detect_and_merge(session, [second], run.id)

        assert conflicts == []
        assert auto_committed == []  # confirms an existing value, nothing newly committed
        assert second.is_current is False  # confirms, does not become a second "current" row
        current = await KnowledgeRepository(session).current_by_fact_key("payment_terms_days")
        assert current.id == first.id


async def test_differing_value_creates_conflict_and_never_overwrites():
    async with session_scope() as session:
        run = await _make_run(session)
        doc_a = await _make_document(session, "a")
        original = await _make_pending_item(session, doc_a, "payment_terms_days", "30")
        await detect_and_merge(session, [original], run.id)

        doc_b = await _make_document(session, "b")
        amended = await _make_pending_item(session, doc_b, "payment_terms_days", "45")
        conflicts, auto_committed = await detect_and_merge(session, [amended], run.id)

        assert len(conflicts) == 1
        assert auto_committed == []  # contested facts are never auto-committed
        assert conflicts[0].item_a_id == original.id
        assert conflicts[0].item_b_id == amended.id
        assert conflicts[0].status == "open"

        # Never silently overwritten: the original is still current, the
        # amended value is not, until a human decides.
        current = await KnowledgeRepository(session).current_by_fact_key("payment_terms_days")
        assert current.id == original.id
        assert amended.is_current is False
