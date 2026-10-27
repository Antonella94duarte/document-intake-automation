from conftest import MINIMAL_PDF
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentSource, JobStatus, ProcessingJob
from app.services.ingestion import ingest
from app.worker import queue
from app.worker.runner import run_once


def _ingest(db_session, storage, content=MINIMAL_PDF) -> Document:
    return ingest(
        db_session,
        storage,
        filename="doc.pdf",
        content=content,
        source=DocumentSource.UPLOAD,
        actor="test",
    ).document


def test_empty_queue_does_nothing(session_factory):
    assert run_once(session_factory, lambda db, doc: None, max_attempts=3) is False


def test_successful_job_is_done(db_session, session_factory, storage):
    document = _ingest(db_session, storage)

    def handler(db, doc):
        doc.status = "processed"

    assert run_once(session_factory, handler, max_attempts=3) is True
    job = db_session.query(ProcessingJob).one()
    assert job.status == JobStatus.DONE.value
    assert job.attempts == 1
    assert db_session.get(Document, document.id).status == "processed"


def test_failure_is_retried_then_marked_failed(db_session, session_factory, storage):
    document = _ingest(db_session, storage)

    def boom(db, doc):
        raise RuntimeError("model unavailable")

    assert run_once(session_factory, boom, max_attempts=2)
    job = db_session.query(ProcessingJob).one()
    assert job.status == JobStatus.QUEUED.value
    assert db_session.get(Document, document.id).status == "pending"

    # Make the retry due now instead of in 30 seconds.
    job.run_after = job.created_at
    db_session.flush()
    assert run_once(session_factory, boom, max_attempts=2)

    assert job.status == JobStatus.FAILED.value
    assert job.attempts == 2
    failed = db_session.get(Document, document.id)
    assert failed.status == "failed"
    assert "model unavailable" in failed.error
    db_session.refresh(failed)
    assert failed.audit_events[-1].action == "processing_failed"


def test_two_workers_never_claim_the_same_job(db_engine, storage):
    """Real concurrency needs committed rows on separate connections, so this test commits
    and cleans up after itself instead of using the rolled-back db_session."""
    with Session(db_engine) as setup:
        ids = [_ingest(setup, storage, MINIMAL_PDF + bytes([n])).id for n in range(2)]
        setup.commit()
    try:
        with Session(db_engine) as worker_a, Session(db_engine) as worker_b:
            first = queue.claim_next(worker_a)  # holds its row lock until rollback
            second = queue.claim_next(worker_b)
            third = queue.claim_next(worker_b)
            assert {first.document_id, second.document_id} == set(ids)
            assert third is None
            worker_a.rollback()
            worker_b.rollback()
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(Document).where(Document.id.in_(ids)))
            cleanup.commit()
