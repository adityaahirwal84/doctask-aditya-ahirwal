from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.api.schemas import DocumentOut
from app.db.session import get_session
from app.repositories.documents import DocumentRepository
from app.services.ingestion import DuplicateDocumentError, store_upload

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_api_key)])

_ALLOWED_FORMATS = {"pdf", "docx", "txt", "md"}


@router.get("", response_model=list[DocumentOut])
async def list_documents(session: AsyncSession = Depends(get_session)) -> list[DocumentOut]:
    """Powers the document picker in the new-run form."""
    from sqlalchemy import select

    from app.db.models import Document

    result = await session.execute(select(Document).order_by(Document.uploaded_at.desc()))
    return [DocumentOut.model_validate(d) for d in result.scalars().all()]


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(file: UploadFile, session: AsyncSession = Depends(get_session)) -> DocumentOut:
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_FORMATS:
        raise HTTPException(400, f"Unsupported format '.{ext}'. Allowed: {sorted(_ALLOWED_FORMATS)}")
    data = await file.read()
    try:
        document = await store_upload(session, file.filename, ext, data)
    except DuplicateDocumentError as exc:
        raise HTTPException(
            409, f"Identical document already uploaded as {exc.existing_document_id}"
        ) from exc
    return DocumentOut.model_validate(document)


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> DocumentOut:
    document = await DocumentRepository(session).get(document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    return DocumentOut.model_validate(document)
