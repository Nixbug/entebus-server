from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
import time
from dateutil import rrule as rrulelib

from app.src import exceptions
from app.src.constants import TMZ_PRIMARY
from app.src.enums import JobType, TriggeringMode
from app.src.redis import (
    acquire_lock,
    release_lock,
    redis_client,
    queue_push,
    queue_pop,
)
from app.src.db import Job, SessionLocal

# ---------------------------------------------------------------------------
## Constants and configurations
# ---------------------------------------------------------------------------
JOB_QUEUE_NAME = "job_queue"
JOB_QUEUE_PUSH_LOCK = "lk_job_queue_push"
JOB_QUEUE_BATCH_SIZE = 100
GLOB_LAST_JOB_ID = "gb_last_job_id"


# ---------------------------------------------------------------------------
## Job execution logic
# ---------------------------------------------------------------------------
def run_service_creation_job(session: Session, job: Job) -> bool:
    """
    Placeholder function to execute a service creation job.
    """
    time.sleep(1)
    print(f"Executed service creation job {job.id} at {datetime.now(TMZ_PRIMARY)}")
    return True


def run_statement_creation_job(session: Session, job: Job) -> bool:
    """
    Placeholder function to execute a statement creation job.
    """
    time.sleep(1)
    print(f"Executed statement creation job {job.id} at {datetime.now(TMZ_PRIMARY)}")
    return True


# ---------------------------------------------------------------------------
## Scheduler logic
# ---------------------------------------------------------------------------
def calculate_next_trigger_on(job: Job) -> Optional[datetime]:
    """
    Calculate the next datetime the job should be triggered.

    Args:
        job (Job): The job object containing scheduling information.

    Returns:
        Optional[datetime]: The next trigger datetime, or None if the job should not be triggered again.
    """
    utc_now = datetime.now(tz=TMZ_PRIMARY)
    # Return None if the validity window has already closed.
    if job.trigger_till is not None and utc_now >= job.trigger_till:
        return None

    if job.last_trigger_on is not None and (job.last_trigger_on > job.trigger_from):
        base_date = job.last_trigger_on.date()
    elif job.trigger_from is not None and (job.trigger_from > utc_now):
        base_date = job.trigger_from.date()
    else:
        base_date = utc_now.date()

    time_of_day = job.trigger_at
    start_datetime = datetime(
        base_date.year,
        base_date.month,
        base_date.day,
        time_of_day.hour,
        time_of_day.minute,
        time_of_day.second,
        tzinfo=time_of_day.tzinfo or TMZ_PRIMARY,
    )
    rule = rrulelib.rrulestr(
        job.recurrence_rule,
        dtstart=start_datetime,
        ignoretz=False,
    )
    # Use "utc_now" as the minimum reference so we don't backfill missed windows
    # and accidentally schedule an already-due occurrence again.
    reference_point: datetime = utc_now
    if job.last_trigger_on is not None and job.last_trigger_on > reference_point:
        reference_point = job.last_trigger_on
    # If trigger_from is in the future, ensure we don't search before it.
    if job.trigger_from is not None and reference_point < job.trigger_from:
        reference_point = job.trigger_from

    candidate = rule.after(reference_point, inc=False)
    # RRULE exhausted (e.g. COUNT or UNTIL reached)
    if candidate is None:
        return None

    # Clamp candidate to trigger_till if it's beyond it.
    if job.trigger_till is not None and candidate >= job.trigger_till:
        return None

    return candidate


def load_jobs_to_queue() -> int:
    """
    Master routine to push jobs to the queue.
    Only one master should be active at a time, enforced by a distributed lock.

    Returns:
        int: The number of jobs pushed to the queue.
    """
    queue_lock = None
    try:
        session = SessionLocal()
        try:
            queue_lock = acquire_lock(JOB_QUEUE_PUSH_LOCK, blocking=False)
        except exceptions.LockAcquireTimeout:
            # Another master is already pushing jobs, skip this cycle.
            return 0

        last_job_id = int(redis_client.get(GLOB_LAST_JOB_ID) or 0)
        jobs = (
            session.query(Job)
            .filter(
                Job.id > last_job_id,
                Job.triggering_mode == TriggeringMode.AUTO,
                Job.next_trigger_on <= datetime.now(TMZ_PRIMARY),
            )
            .order_by(Job.id)
            .limit(JOB_QUEUE_BATCH_SIZE)
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
        release_lock(queue_lock)

    return len(jobs)


def run_job_from_queue(job_id: int):
    """
    Execute a job from the queue.

    Args:
        job_id (int): The ID of the job to be executed.
    """
    job_lock = None
    try:
        session = SessionLocal()
        job_lock = acquire_lock(f"lk_job_{job_id}")

        job = session.query(Job).filter(Job.id == job_id).first()
        if job is None:
            return

        utc_now = datetime.now(TMZ_PRIMARY)
        if job.next_trigger_on is None:
            return
        if job.next_trigger_on > utc_now:
            return
        if not job.triggering_mode == TriggeringMode.AUTO:
            return

        job.next_trigger_on = calculate_next_trigger_on(job)
        job.last_trigger_on = utc_now

        if job.job_type == JobType.SERVICE_CREATION:
            can_run_again = run_service_creation_job(session, job)
        elif job.job_type == JobType.STATEMENT_CREATION:
            can_run_again = run_statement_creation_job(session, job)

        if not can_run_again or job.next_trigger_on is None:
            job.triggering_mode = TriggeringMode.DISABLED

        session.flush()
        session.commit()
    finally:
        release_lock(job_lock)
        session.close()


def start_job_manager():
    """
    Main loop for the job manager. Continuously loads jobs into the queue and processes them.
    Designed to be run in a separate process or thread.
    """
    while True:
        load_jobs_to_queue()

        while True:
            job = queue_pop(JOB_QUEUE_NAME)
            job_id = job.get("job_id") if job else None

            if job_id is None:
                break
            run_job_from_queue(job_id)

        time.sleep(30)
