import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DocumentSource(str, Enum):
    UPLOAD = "upload"
    EMAIL = "email"  # a PDF that came attached to an uploaded email
    PASTE = "paste"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    FAILED = "failed"


class DocumentType(str, Enum):
    INVOICE = "invoice"
    PURCHASE_ORDER = "purchase_order"
    OTHER = "other"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class AuditAction(str, Enum):
    UPLOADED = "uploaded"
    PROCESSED = "processed"
    PROCESSING_FAILED = "processing_failed"
    FIELD_EDITED = "field_edited"
    APPROVED = "approved"
    REPROCESSED = "reprocessed"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Document(Base):
    """One incoming document and where it is in the pipeline."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(20))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Relative key inside the storage backend, not a filesystem path.
    storage_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    # An email's PDF attachments are documents of their own, linked to the email.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.PENDING.value)
    doc_type: Mapped[str | None] = mapped_column(String(20))
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    # Text the model was given; kept so reviewers can see exactly what was read.
    raw_text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    children: Mapped[list["Document"]] = relationship(back_populates="parent")
    parent: Mapped["Document | None"] = relationship(
        back_populates="children", remote_side=lambda: [Document.id]
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="document", order_by="AuditEvent.id", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_documents_status_created", "status", "created_at"),)


class ProcessingJob(Base):
    """Work queue for the worker. Claimed with SELECT ... FOR UPDATE SKIP LOCKED, so several
    workers can run side by side without taking the same job."""

    __tablename__ = "processing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_processing_jobs_status_run_after", "status", "run_after"),)


class AuditEvent(Base):
    """Append-only history: what happened to a document, who did it and what changed."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # "system" for the pipeline, otherwise the reviewer's email.
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(30))
    field: Mapped[str | None] = mapped_column(String(100))
    old_value: Mapped[object | None] = mapped_column(JSONB)
    new_value: Mapped[object | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="audit_events")
