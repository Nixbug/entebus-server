from datetime import datetime
from typing import Optional, cast
from sqlalchemy.orm import Session
import logging
import time
from dateutil import rrule as rrulelib

from app.api.service import create_service
from app.api.service import CreateForm as ServiceCreateForm
from app.api.service_assignment import CreateForm as ServiceAssignmentCreateForm
from app.api.service_assignment import create_service_assignment
from app.src import exceptions
from app.src.constants import TMZ_PRIMARY
from app.src.enums import JobType, NotificationType, OperatorType, TriggeringMode
from app.src.redis import (
    acquire_lock,
    release_lock,
    redis_client,
    queue_push,
    queue_pop,
)
from app.src.db import (
    CompanyNotification,
    Job,
    OperatorNotification,
    ServiceAssignmentAutomation,
    ServiceAutomation,
    SessionLocal,
)

# ---------------------------------------------------------------------------
## Constants and configurations
# ---------------------------------------------------------------------------
JOB_QUEUE_NAME = "job_queue"
JOB_QUEUE_PUSH_LOCK = "lk_job_queue_push"
JOB_QUEUE_BATCH_SIZE = 100
GLOB_LAST_JOB_ID = "gb_last_job_id"
JOB_MANAGER_SLEEP_SECONDS = 30

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
## Job execution logic
# ---------------------------------------------------------------------------
def run_service_creation_job(session: Session, job: Job):
    """
    Execute a service creation job by creating services and their assignments based on the
    associated ServiceAutomation and ServiceAssignmentAutomation records.

    Args:
        session (Session): SQLAlchemy database session.
        job (Job): The job object containing scheduling information.
    """
    service_automations = (
        session.query(ServiceAutomation)
        .filter(ServiceAutomation.job_id == job.id)
        .all()
    )
    logger.info(
        "run_service_creation_job started",
        extra={"job_id": job.id, "automation_count": len(service_automations)},
    )

    utc_now = datetime.now(TMZ_PRIMARY)
    for service_automation in service_automations:
        logger.debug(
            "processing service automation",
            extra={
                "job_id": job.id,
                "service_automation_id": service_automation.id,
                "company_id": service_automation.company_id,
                "route_id": service_automation.route_id,
                "vehicle_id": service_automation.vehicle_id,
                "fare_id": service_automation.fare_id,
            },
        )
        with SessionLocal() as atomic_session:
            starting_at = datetime(
                utc_now.year,
                utc_now.month,
                utc_now.day,
                service_automation.starting_at.hour,
                service_automation.starting_at.minute,
                service_automation.starting_at.second,
                tzinfo=service_automation.starting_at.tzinfo or TMZ_PRIMARY,
            )
            logger.debug(
                "computed service start timestamp",
                extra={
                    "job_id": job.id,
                    "service_automation_id": service_automation.id,
                    "starting_at": starting_at.isoformat(),
                },
            )

            try:
                service_data = create_service(
                    atomic_session,
                    ServiceCreateForm(
                        route_id=service_automation.route_id,
                        fare_id=service_automation.fare_id,
                        vehicle_id=service_automation.vehicle_id,
                        name=service_automation.name,
                        ticket_mode=service_automation.ticket_mode,
                        starting_at=starting_at,
                        company_id=service_automation.company_id,
                    ),
                    token=None,
                    request_info=None,
                )
                logger.info(
                    "service created successfully",
                    extra={
                        "job_id": job.id,
                        "service_automation_id": service_automation.id,
                        "service_id": service_data.get("id"),
                        "service_name": service_data.get("name"),
                    },
                )
            except exceptions.APIException as e:
                logger.warning(
                    "service creation failed with API exception",
                    extra={
                        "job_id": job.id,
                        "service_automation_id": service_automation.id,
                        "company_id": service_automation.company_id,
                        "headers": e.headers,
                        "detail": e.detail,
                    },
                )
                company_notification = CompanyNotification(
                    company_id=service_automation.company_id,
                    operator_types=[OperatorType.ADMIN, OperatorType.MANAGER],
                    type=NotificationType.EXCEPTION,
                    title=e.headers,
                    details={
                        "detail": e.detail,
                        "service_automation": {
                            "id": service_automation.id,
                            "name": service_automation.name,
                        },
                    },
                )
                atomic_session.add(company_notification)
                atomic_session.commit()
                logger.info(
                    "company notification committed for service creation failure",
                    extra={
                        "job_id": job.id,
                        "service_automation_id": service_automation.id,
                        "company_id": service_automation.company_id,
                        "notification_type": int(NotificationType.EXCEPTION),
                    },
                )
                continue

            service_assignment_automations = (
                session.query(ServiceAssignmentAutomation)
                .filter(
                    ServiceAssignmentAutomation.service_automation_id
                    == service_automation.id
                )
                .all()
            )
            logger.info(
                "loaded service assignment automations",
                extra={
                    "job_id": job.id,
                    "service_automation_id": service_automation.id,
                    "assignment_automation_count": len(service_assignment_automations),
                },
            )
            for service_assignment in service_assignment_automations:
                logger.debug(
                    "creating service assignment",
                    extra={
                        "job_id": job.id,
                        "service_automation_id": service_automation.id,
                        "service_id": service_data.get("id"),
                        "operator_id": service_assignment.operator_id,
                        "company_id": service_assignment.company_id,
                    },
                )
                service_assignment_data = create_service_assignment(
                    atomic_session,
                    ServiceAssignmentCreateForm(
                        service_id=service_data["id"],
                        operator_id=service_assignment.operator_id,
                        company_id=service_assignment.company_id,
                    ),
                    token=None,
                    request_info=None,
                )
                logger.info(
                    "service assignment created",
                    extra={
                        "job_id": job.id,
                        "service_automation_id": service_automation.id,
                        "service_id": service_data.get("id"),
                        "service_assignment_id": service_assignment_data.get("id"),
                        "operator_id": service_assignment.operator_id,
                    },
                )
                operator_notification = OperatorNotification(
                    company_id=service_assignment.company_id,
                    operator_id=service_assignment.operator_id,
                    type=NotificationType.INFORMATION,
                    title="DUTY_ASSIGNED",
                    details={
                        "service_assignment": {
                            "id": service_assignment_data["id"],
                        },
                        "service": {
                            "id": service_data["id"],
                            "name": service_data["name"],
                        },
                    },
                )
                atomic_session.add(operator_notification)
                atomic_session.commit()
                logger.info(
                    "operator notification committed",
                    extra={
                        "job_id": job.id,
                        "service_automation_id": service_automation.id,
                        "service_assignment_id": service_assignment_data.get("id"),
                        "operator_id": service_assignment.operator_id,
                        "notification_title": "DUTY_ASSIGNED",
                    },
                )

    logger.info("run_service_creation_job completed", extra={"job_id": job.id})


def run_statement_creation_job(session: Session, job: Job):
    """
    Placeholder function to execute a statement creation job.
    """
    logger.info("run_statement_creation_job started", extra={"job_id": job.id})
    time.sleep(1)
    logger.info(
        "run_statement_creation_job completed",
        extra={"job_id": job.id, "executed_at": datetime.now(TMZ_PRIMARY).isoformat()},
    )


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
    logger.debug(
        "calculate_next_trigger_on invoked",
        extra={
            "job_id": job.id,
            "utc_now": utc_now.isoformat(),
            "last_trigger_on": job.last_trigger_on.isoformat()
            if job.last_trigger_on is not None
            else None,
            "trigger_from": job.trigger_from.isoformat()
            if job.trigger_from is not None
            else None,
            "trigger_till": job.trigger_till.isoformat()
            if job.trigger_till is not None
            else None,
            "trigger_at": job.trigger_at.isoformat(),
            "recurrence_rule": job.recurrence_rule,
        },
    )
    # Return None if the validity window has already closed.
    if job.trigger_till is not None and utc_now >= job.trigger_till:
        logger.info(
            "job skipped because trigger_till is in the past",
            extra={"job_id": job.id},
        )
        return None

    if (
        job.last_trigger_on is not None
        and job.trigger_from is not None
        and job.last_trigger_on > job.trigger_from
    ):
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
        logger.info("rrule exhausted for job", extra={"job_id": job.id})
        return None

    # Clamp candidate to trigger_till if it's beyond it.
    if job.trigger_till is not None and candidate >= job.trigger_till:
        logger.info(
            "candidate exceeds trigger_till; disabling future triggers",
            extra={
                "job_id": job.id,
                "candidate": candidate.isoformat(),
                "trigger_till": job.trigger_till.isoformat(),
            },
        )
        return None

    logger.debug(
        "calculated next trigger",
        extra={"job_id": job.id, "next_trigger_on": candidate.isoformat()},
    )
    return candidate


def load_jobs_to_queue() -> int:
    """
    Master routine to push jobs to the queue.
    Only one master should be active at a time, enforced by a distributed lock.

    Returns:
        int: The number of jobs pushed to the queue.
    """
    queue_lock = None
    jobs: list[Job] = []
    logger.debug("load_jobs_to_queue started")
    try:
        try:
            queue_lock = acquire_lock(JOB_QUEUE_PUSH_LOCK, blocking=False)
            logger.debug("acquired queue push lock", extra={"lock": JOB_QUEUE_PUSH_LOCK})
        except exceptions.LockAcquireTimeout:
            # Another master is already pushing jobs, skip this cycle.
            logger.debug(
                "skipping load_jobs_to_queue; queue lock already held",
                extra={"lock": JOB_QUEUE_PUSH_LOCK},
            )
            return 0

        with SessionLocal() as session:
            last_job_id = int(
                cast(Optional[str], redis_client.get(GLOB_LAST_JOB_ID)) or 0
            )
            logger.debug("fetched last job id", extra={"last_job_id": last_job_id})
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
            logger.info("jobs selected for queue", extra={"job_count": len(jobs)})

            # Push jobs to the queue
            for job in jobs:
                queue_push(
                    JOB_QUEUE_NAME,
                    {"job_id": job.id},
                )
                logger.debug(
                    "job pushed to queue",
                    extra={"job_id": job.id, "queue": JOB_QUEUE_NAME},
                )

            # Update the last job ID in Redis
            if jobs:
                redis_client.set(
                    GLOB_LAST_JOB_ID,
                    jobs[-1].id,
                )
                logger.debug(
                    "updated last job id in redis",
                    extra={"last_job_id": jobs[-1].id},
                )
            else:
                redis_client.set(
                    GLOB_LAST_JOB_ID,
                    0,
                )
                logger.debug("no jobs found; reset last job id to 0")
    finally:
        release_lock(queue_lock)
        logger.debug("released queue push lock", extra={"lock": JOB_QUEUE_PUSH_LOCK})

    logger.info("load_jobs_to_queue completed", extra={"queued_jobs": len(jobs)})
    return len(jobs)


def run_job_from_queue(job_id: int):
    """
    Execute a job from the queue.

    Args:
        job_id (int): The ID of the job to be executed.
    """
    job_lock = None
    logger.info("run_job_from_queue started", extra={"job_id": job_id})
    try:
        job_lock = acquire_lock(f"lk_job_{job_id}")
        logger.debug("acquired job lock", extra={"job_id": job_id})

        with SessionLocal() as session:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job is None:
                logger.warning("job not found", extra={"job_id": job_id})
                return

            utc_now = datetime.now(TMZ_PRIMARY)
            if job.next_trigger_on is None:
                logger.info("job has no next trigger; skipping", extra={"job_id": job_id})
                return
            if job.next_trigger_on > utc_now:
                logger.info(
                    "job not due yet; skipping",
                    extra={
                        "job_id": job_id,
                        "next_trigger_on": job.next_trigger_on.isoformat(),
                        "utc_now": utc_now.isoformat(),
                    },
                )
                return
            if job.triggering_mode != TriggeringMode.AUTO:
                logger.info(
                    "job triggering mode is not AUTO; skipping",
                    extra={"job_id": job_id, "triggering_mode": int(job.triggering_mode)},
                )
                return
            logger.debug(
                "executing due job",
                extra={
                    "job_id": job_id,
                    "job_type": int(job.job_type),
                    "current_next_trigger_on": job.next_trigger_on.isoformat(),
                },
            )
            job.next_trigger_on = calculate_next_trigger_on(job)
            job.last_trigger_on = utc_now

            if job.job_type == JobType.SERVICE_CREATION:
                logger.info("dispatching service creation job", extra={"job_id": job_id})
                run_service_creation_job(session, job)
            elif job.job_type == JobType.STATEMENT_CREATION:
                logger.info("dispatching statement creation job", extra={"job_id": job_id})
                run_statement_creation_job(session, job)
            else:
                logger.warning(
                    "unknown job type; no handler executed",
                    extra={"job_id": job_id, "job_type": int(job.job_type)},
                )

            if job.next_trigger_on is None:
                job.triggering_mode = TriggeringMode.DISABLED
                logger.info("job disabled because next trigger is None", extra={"job_id": job_id})
            session.commit()
            logger.info(
                "job execution committed",
                extra={
                    "job_id": job_id,
                    "last_trigger_on": job.last_trigger_on.isoformat(),
                    "next_trigger_on": job.next_trigger_on.isoformat()
                    if job.next_trigger_on is not None
                    else None,
                    "triggering_mode": int(job.triggering_mode),
                },
            )
    except Exception:
        logger.exception("unhandled exception while running job", extra={"job_id": job_id})
    finally:
        release_lock(job_lock)
        logger.debug("released job lock", extra={"job_id": job_id})


def start_job_manager():
    """
    Main loop for the job manager. Continuously loads jobs into the queue and processes them.
    Designed to be run in a separate process or thread.
    """
    logger.info("job manager loop started")
    while True:
        queued_jobs = load_jobs_to_queue()
        logger.debug("queue load cycle finished", extra={"queued_jobs": queued_jobs})

        while True:
            job = queue_pop(JOB_QUEUE_NAME)
            job_id = job.get("job_id") if job else None

            if job_id is None:
                logger.debug("queue is empty; ending processing cycle")
                break
            logger.debug("popped job from queue", extra={"job_id": job_id})
            run_job_from_queue(job_id)

        logger.debug(
            "job manager sleeping before next cycle",
            extra={"sleep_seconds": JOB_MANAGER_SLEEP_SECONDS},
        )
        time.sleep(JOB_MANAGER_SLEEP_SECONDS)
