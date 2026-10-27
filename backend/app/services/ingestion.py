"""Accepting documents: validate, store the file, record it and queue it for processing."""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AuditAction,
    AuditEvent,
    Document,
    DocumentSource,
    DocumentStatus,
)
from app.storage import Storage
from app.worker.queue import enqueue

logger = logging.getLogger(__name__)

PDF_MIME = "application/pdf"
EMAIL_MIME = "message/rfc822"
TEXT_MIME = "text/plain"

# Decided from the extension and checked against the content; the client's Content-Type is
# only a hint and is not trusted.
_EXTENSIONS = {".pdf": PDF_MIME, ".eml": EMAIL_MIME, ".txt": TEXT_MIME}
_STORED_EXTENSION = {PDF_MIME: ".pdf", EMAIL_MIME: ".eml", TEXT_MIME: ".txt"}


class UnsupportedDocumentError(ValueError):
    pass


@dataclass
class IngestResult:
    document: Document
    duplicate: bool


def detect_mime_type(filename: str, content: bytes) -> str:
    extension = PurePath(filename).suffix.lower()
    mime = _EXTENSIONS.get(extension)
    if mime is None:
        raise UnsupportedDocumentError(
            f"Unsupported file type '{extension or filename}'. Accepted: .pdf, .eml, .txt"
        )
    if mime == PDF_MIME and not content.startswith(b"%PDF-"):
        raise UnsupportedDocumentError("The file has a .pdf extension but is not a PDF")
    if mime == TEXT_MIME:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise UnsupportedDocumentError("The text file is not valid UTF-8") from None
    return mime


def ingest(
    db: Session,
    storage: Storage,
    *,
    filename: str,
    content: bytes,
    source: DocumentSource,
    actor: str,
    parent: Document | None = None,
    mime_type: str | None = None,
) -> IngestResult:
    """Store a new document and queue it. The same file uploaded twice returns the existing
    document instead of processing (and paying for) it again."""
    if not content:
        raise UnsupportedDocumentError("The document is empty")
    mime = mime_type or detect_mime_type(filename, content)
    digest = hashlib.sha256(content).hexdigest()

    existing = db.scalar(
        select(Document).where(Document.sha256 == digest).order_by(Document.created_at).limit(1)
    )
    if existing is not None:
        logger.info("Duplicate upload of %s (document %s)", filename, existing.id)
        return IngestResult(document=existing, duplicate=True)

    document_id = uuid.uuid4()
    key = f"{digest[:2]}/{document_id}{_STORED_EXTENSION[mime]}"
    storage.save(key, content)

    document = Document(
        id=document_id,
        filename=PurePath(filename).name[:255] or "document",
        source=source.value,
        mime_type=mime,
        size_bytes=len(content),
        storage_key=key,
        sha256=digest,
        parent_id=parent.id if parent else None,
        status=DocumentStatus.PENDING.value,
    )
    db.add(document)
    db.flush()
    enqueue(db, document_id)
    db.add(
        AuditEvent(
            document_id=document_id,
            actor=actor,
            action=AuditAction.UPLOADED.value,
            details={"filename": document.filename, "source": source.value, "sha256": digest},
        )
    )
    db.flush()
    return IngestResult(document=document, duplicate=False)


def pasted_text_filename(subject: str | None) -> str:
    """A readable name for pasted text, from the email subject when there is one."""
    base = "".join(c if c.isalnum() or c in " -_" else "" for c in (subject or "")).strip()
    return f"{(base or 'pasted-email')[:80]}.txt"
