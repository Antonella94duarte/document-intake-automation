"""Runs one document through the pipeline. Filled in by the next phases."""

from sqlalchemy.orm import Session

from app.db.models import Document


def process_document(db: Session, document: Document) -> None:
    raise NotImplementedError("The processing pipeline is not implemented yet")
