from datetime import datetime, timezone
from sqlalchemy.orm import Session
import time

from app.src.enums import TriggeringMode
from app.src.redis import (
    acquire_lock,
    release_lock,
    redis_client,
    queue_push,
    queue_pop,
)
from app.src.db import JOB, SessionLocal

JOB_QUEUE_NAME = "job_queue"
LOCK_QUEUE_PUSH_LOCK = "lk_job_queue_push"
GLOB_LAST_JOB_ID = "gb_last_job_id"
JOB_BATCH_SIZE = 100


def master_logic() -> bool:
    """
    Master logic to push jobs to the queue.

    Returns:
        bool: True if jobs were pushed to the queue, False otherwise.
    """
    lock = None
    try:
        session = SessionLocal()
        lock = acquire_lock(LOCK_QUEUE_PUSH_LOCK, blocking=False)
        if not lock.locked():
            return False

        last_job_id = int(redis_client.get(GLOB_LAST_JOB_ID) or 0)
        jobs = (
            session.query(JOB)
            .filter(JOB.id > last_job_id, JOB.triggering_mode == TriggeringMode.AUTO)
            .order_by(JOB.id)
            .limit(JOB_BATCH_SIZE)
            .all()
        )

        # Push jobs to the queue
        for job in jobs:
            queue_push(
                JOB_QUEUE_NAME,
                {"job_id": job.id},
            )

        # Update the last job ID in Redis
        if jobs:
            redis_client.set(
                GLOB_LAST_JOB_ID,
                jobs[-1].id,
            )
        else:
            redis_client.set(
                GLOB_LAST_JOB_ID,
                0,
            )
    finally:
        session.close()
        release_lock(lock)

    return True


def slave_logic():
    """
    Slave logic to pop jobs from the queue and execute them.
    """
    job_lock = None
    try:
        session = SessionLocal()
        queued_job = queue_pop(JOB_QUEUE_NAME)
        job_id = queued_job.get("job_id") if queued_job else None
        job_lock = acquire_lock(f"lk_job_{job_id}")

        if job_id is None:
            return

        job = session.get(
            JOB,
            job_id,
        )

        if job is None:
            return

        utc_now = datetime.now(timezone.utc)
        job.last_trigger_on = utc_now
        job.next_trigger_on = utc_now
        session.add(job)
        session.commit()

        run_job(session, job)

    finally:
        release_lock(job_lock)
        session.close()


def run_job(session: Session, job: JOB) -> bool:
    """
    Execute the job logic.

    Args:
        session (Session): SQLAlchemy session for database operations.
        job (JOB): The job object to be executed.

    Returns:
        bool: True if the job executed successfully, False otherwise.
    """
    time.sleep(10)
    return True
