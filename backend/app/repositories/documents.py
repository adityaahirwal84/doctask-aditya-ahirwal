from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self.session.get(Document, document_id)

    async def get_by_content_hash(self, content_hash: str) -> Document | None:
        result = await self.session.execute(
            select(Document).where(Document.content_hash == content_hash)
        )
        return result.scalar_one_or_none()

    async def list_by_ids(self, document_ids: list[uuid.UUID]) -> list[Document]:
        result = await self.session.execute(
            select(Document).where(Document.id.in_(document_ids))
        )
        return list(result.scalars().all())

    async def create(self, document: Document) -> Document:
        self.session.add(document)
        await self.session.flush()
        return document

    async def add_chunks(self, chunks: list[Chunk]) -> None:
        self.session.add_all(chunks)
        await self.session.flush()

    async def list_chunks(self, document_id: uuid.UUID) -> list[Chunk]:
        result = await self.session.execute(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
        )
        return list(result.scalars().all())

    async def get_chunk(self, chunk_id: uuid.UUID) -> Chunk | None:
        return await self.session.get(Chunk, chunk_id)

    async def similarity_search(
        self, embedding: list[float], limit: int = 5, document_ids: list[uuid.UUID] | None = None
    ) -> list[Chunk]:
        """Cosine-distance nearest neighbours via pgvector's <=> operator,
        optionally filtered to a set of documents (metadata filtering)."""
        query = select(Chunk).order_by(Chunk.embedding.cosine_distance(embedding)).limit(limit)
        if document_ids:
            query = query.where(Chunk.document_id.in_(document_ids))
        result = await self.session.execute(query)
        return list(result.scalars().all())
