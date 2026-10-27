import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_actor, storage_dep
from app.core.config import get_settings
from app.db.models import Document, DocumentSource, DocumentStatus, DocumentType
from app.db.session import get_db
from app.schemas import DocumentDetail, DocumentPage, PastedText, UploadResponse
from app.services.ingestion import (
    TEXT_MIME,
    UnsupportedDocumentError,
    ingest,
    pasted_text_filename,
)
from app.storage import Storage

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _get_document(db: Session, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    response: Response,
    file: UploadFile = File(..., description="A PDF, an .eml email or a .txt file"),
    db: Session = Depends(get_db),
    storage: Storage = Depends(storage_dep),
    actor: str = Depends(current_actor),
):
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The file is too large")
    try:
        result = ingest(
            db,
            storage,
            filename=file.filename or "document",
            content=content,
            source=DocumentSource.UPLOAD,
            actor=actor,
        )
    except UnsupportedDocumentError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    db.commit()
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return UploadResponse(document=result.document, duplicate=result.duplicate)


@router.post("/text", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def submit_text(
    payload: PastedText,
    response: Response,
    db: Session = Depends(get_db),
    storage: Storage = Depends(storage_dep),
    actor: str = Depends(current_actor),
):
    header = "".join(
        f"{name}: {value}\n"
        for name, value in (("From", payload.sender), ("Subject", payload.subject))
        if value
    )
    content = (header + ("\n" if header else "") + payload.body).encode("utf-8")
    result = ingest(
        db,
        storage,
        filename=pasted_text_filename(payload.subject),
        content=content,
        source=DocumentSource.PASTE,
        actor=actor,
        mime_type=TEXT_MIME,
    )
    db.commit()
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return UploadResponse(document=result.document, duplicate=result.duplicate)


@router.get("", response_model=DocumentPage)
def list_documents(
    status_filter: DocumentStatus | None = Query(None, alias="status"),
    doc_type: DocumentType | None = Query(None, alias="type"),
    q: str | None = Query(None, max_length=100, description="Search in the file name"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = select(Document)
    if status_filter:
        query = query.where(Document.status == status_filter.value)
    if doc_type:
        query = query.where(Document.doc_type == doc_type.value)
    if q:
        pattern = f"%{q.replace('%', '').replace('_', '')}%"
        query = query.where(or_(Document.filename.ilike(pattern)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(
        query.order_by(Document.created_at.desc(), Document.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return DocumentPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    document = db.scalar(
        select(Document)
        .where(Document.id == document_id)
        .options(selectinload(Document.children), selectinload(Document.audit_events))
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


@router.get("/{document_id}/file")
def download_file(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: Storage = Depends(storage_dep),
):
    document = _get_document(db, document_id)
    content = storage.read(document.storage_key)
    # inline so the dashboard can show PDFs in an <iframe>; the name is sanitised by ingest().
    return Response(
        content,
        media_type=document.mime_type,
        headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
    )
