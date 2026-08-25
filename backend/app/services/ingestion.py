from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Chunk, Document, DocumentStatus, KnowledgeItem
from app.llm.provider import LLMProvider
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge import KnowledgeRepository
from app.services.observability import record_cost
from app.services.parsing import parse_document

settings = get_settings()


class DuplicateDocumentError(Exception):
    """Raised when a document with the same content hash already exists.
    This is what makes re-uploading the same file idempotent instead of
    silently duplicating knowledge."""

    def __init__(self, existing_document_id: uuid.UUID):
        self.existing_document_id = existing_document_id
        super().__init__(f"Document already ingested as {existing_document_id}")


async def store_upload(session: AsyncSession, filename: str, doc_format: str, data: bytes) -> Document:
    content_hash = hashlib.sha256(data).hexdigest()
    repo = DocumentRepository(session)
    existing = await repo.get_by_content_hash(content_hash)
    if existing is not None:
        raise DuplicateDocumentError(existing.id)

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage_path = upload_dir / f"{content_hash}_{filename}"
    storage_path.write_bytes(data)

    document = Document(
        filename=filename, format=doc_format, content_hash=content_hash,
        storage_path=str(storage_path), status=DocumentStatus.uploaded,
    )
    return await repo.create(document)


async def parse_classify_extract_embed(
    session: AsyncSession, document: Document, provider: LLMProvider, run_id: uuid.UUID | None,
) -> list[KnowledgeItem]:
    """The full per-document ingest pipeline: parse -> classify -> extract
    facts -> embed chunks. Returns the newly created (not-yet-current)
    KnowledgeItem rows so the caller (the conflict-detection node) can
    compare them against current knowledge without re-querying.
    """
    doc_repo = DocumentRepository(session)
    knowledge_repo = KnowledgeRepository(session)

    raw_bytes = Path(document.storage_path).read_bytes()
    raw_chunks = parse_document(raw_bytes, document.format)

    full_text = "\n\n".join(c.content for c in raw_chunks)
    classification, classify_cost = await provider.classify_document(full_text[:6000])
    document.document_type = classification.document_type
    document.classification_confidence = classification.confidence
    await record_cost(session, run_id, classify_cost)

    chunk_rows = [
        Chunk(document_id=document.id, chunk_index=i, content=rc.content, section_ref=rc.section_ref)
        for i, rc in enumerate(raw_chunks)
    ]
    await doc_repo.add_chunks(chunk_rows)

    vectors, embed_cost = await provider.embed([c.content for c in raw_chunks])
    for chunk_row, vector in zip(chunk_rows, vectors, strict=True):
        chunk_row.embedding = vector
    await record_cost(session, run_id, embed_cost)

    created_items: list[KnowledgeItem] = []
    for chunk_row, raw_chunk in zip(chunk_rows, raw_chunks, strict=True):
        facts, extract_cost = await provider.extract_facts(raw_chunk.content)
        await record_cost(session, run_id, extract_cost)
        for fact in facts:
            item = await knowledge_repo.add(
                KnowledgeItem(
                    document_id=document.id, source_chunk_id=chunk_row.id,
                    fact_key=fact.fact_key, fact_value=fact.fact_value,
                    confidence=fact.confidence, is_current=False,
                )
            )
            created_items.append(item)

    document.status = DocumentStatus.parsed
    return created_items
