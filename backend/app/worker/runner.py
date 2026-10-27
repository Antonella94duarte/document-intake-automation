"""The worker process: claims jobs from the queue and runs the processing pipeline on them.

Run with `python -m app.worker.runner`. Several can run at once (see app/worker/queue.py).
"""

import logging
import signal
import time
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import AuditAction, AuditEvent, Document, DocumentStatus
from app.worker import queue

logger = logging.getLogger(__name__)

# Processes one document inside the given session; raises on failure.
Handler = Callable[[Session, Document], None]


def run_once(session_factory: sessionmaker, handler: Handler, *, max_attempts: int) -> bool:
    """Process at most one job. Returns False when the queue was empty."""
    with session_factory() as db:
        job = queue.claim_next(db)
        if job is None:
            return False
        document = db.get(Document, job.document_id)
        document.status = DocumentStatus.PROCESSING.value
        document.error = None
        # Commit the claim so the job shows as running while the (slow) model calls happen.
        db.commit()
        job_id, document_id = job.id, document.id

    with session_factory() as db:
        job = db.get(queue.ProcessingJob, job_id)
        document = db.get(Document, document_id)
        try:
            handler(db, document)
            queue.mark_done(db, job)
            db.commit()
            logger.info("Processed document %s -> %s", document_id, document.status)
        except Exception as exc:  # noqa: BLE001 - any failure must be recorded, not crash the loop
            db.rollback()
            job = db.get(queue.ProcessingJob, job_id)
            document = db.get(Document, document_id)
            error = f"{exc.__class__.__name__}: {exc}"
            retrying = queue.mark_failed(db, job, error, max_attempts=max_attempts)
            document.status = (
                DocumentStatus.PENDING.value if retrying else DocumentStatus.FAILED.value
            )
            document.error = error
            if not retrying:
                db.add(
                    AuditEvent(
                        document_id=document_id,
                        actor="system",
                        action=AuditAction.PROCESSING_FAILED.value,
                        details={"error": error, "attempts": job.attempts},
                    )
                )
            db.commit()
            logger.warning(
                "Document %s failed (attempt %s, %s): %s",
                document_id,
                job.attempts,
                "will retry" if retrying else "giving up",
                error,
            )
    return True


def run_forever(session_factory: sessionmaker, handler: Handler) -> None:
    settings = get_settings()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        logger.info("Stopping after the current job")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    logger.info("Worker started")
    while not stopping:
        try:
            worked = run_once(session_factory, handler, max_attempts=settings.worker_max_attempts)
        except Exception:  # database down, etc.: keep the worker alive and try again
            logger.exception("Worker loop error")
            worked = False
        if not worked:
            time.sleep(settings.worker_poll_seconds)


def main() -> None:
    from app.db.session import SessionLocal
    from app.pipeline.processor import process_document

    logging.basicConfig(
        level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run_forever(SessionLocal, process_document)


if __name__ == "__main__":
    main()
