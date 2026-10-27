import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PastedText(BaseModel):
    """An email or document pasted as text instead of uploaded as a file."""

    subject: str | None = Field(default=None, max_length=300)
    sender: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=20, max_length=200_000)


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    source: str
    mime_type: str
    size_bytes: int
    status: str
    doc_type: str | None
    classification_confidence: float | None
    parent_id: uuid.UUID | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None


class UploadResponse(BaseModel):
    document: DocumentSummary
    duplicate: bool


class DocumentPage(BaseModel):
    items: list[DocumentSummary]
    total: int
    page: int
    page_size: int


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str
    action: str
    field: str | None
    old_value: Any
    new_value: Any
    reason: str | None
    details: dict | None
    created_at: datetime


class DocumentDetail(DocumentSummary):
    raw_text: str | None
    children: list[DocumentSummary]
    audit_events: list[AuditEventOut]
