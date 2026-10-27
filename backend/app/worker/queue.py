"""A small job queue on top of PostgreSQL.

`SELECT ... FOR UPDATE SKIP LOCKED` lets any number of workers poll the same table: each one
locks the row it takes and the others skip it. That covers this workload without running Redis;
the queue sits behind these few functions, so moving to Redis/SQS later only changes this file.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db.models import JobStatus, ProcessingJob

# A job left "running" this long belongs to a worker that died; another worker may take it.
STALE_AFTER = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(db: Session, document_id) -> ProcessingJob:
    job = ProcessingJob(document_id=document_id)
    db.add(job)
    db.flush()
    return job


def claim_next(db: Session) -> ProcessingJob | None:
    """Take the oldest runnable job and mark it running. The caller commits."""
    now = _now()
    job = db.scalar(
        select(ProcessingJob)
        .where(
            or_(
                (ProcessingJob.status == JobStatus.QUEUED.value) & (ProcessingJob.run_after <= now),
                (ProcessingJob.status == JobStatus.RUNNING.value)
                & (ProcessingJob.locked_at < now - STALE_AFTER),
            )
        )
        .order_by(ProcessingJob.run_after, ProcessingJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = JobStatus.RUNNING.value
    job.locked_at = now
    job.attempts += 1
    db.flush()
    return job


def mark_done(db: Session, job: ProcessingJob) -> None:
    job.status = JobStatus.DONE.value
    job.finished_at = _now()
    job.last_error = None
    db.flush()


def mark_failed(db: Session, job: ProcessingJob, error: str, *, max_attempts: int) -> bool:
    """Record the error. Returns True when the job will be retried, False when it gave up.
    Retries wait 30 s, 2 min, 8 min... so a provider outage is not hammered."""
    job.last_error = error[:2000]
    job.locked_at = None
    if job.attempts < max_attempts:
        job.status = JobStatus.QUEUED.value
        job.run_after = _now() + timedelta(seconds=30 * 4 ** (job.attempts - 1))
        db.flush()
        return True
    job.status = JobStatus.FAILED.value
    job.finished_at = _now()
    db.flush()
    return False


def cancel_pending(db: Session, document_id) -> None:
    """Drop queued jobs for a document (e.g. before queueing a reprocess)."""
    db.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.document_id == document_id,
            ProcessingJob.status == JobStatus.QUEUED.value,
        )
        .values(status=JobStatus.DONE.value, finished_at=_now(), last_error="superseded")
    )
