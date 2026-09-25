from datetime import datetime
import logging
from typing import Optional
from sqlalchemy.orm import Session
from dateutil import rrule as rrulelib

from app.api.service import create_service
from app.api.service import CreateForm as ServiceCreateForm
from app.api.service_assignment import CreateForm as ServiceAssignmentCreateForm
from app.api.service_assignment import create_service_assignment
from app.src import exceptions
from app.src.constants import TMZ_PRIMARY, TMZ_SECONDARY
from app.src.enums import JobType, NotificationType, OperatorType, TriggeringMode
from app.src.valkey import (
    acquire_lock,
    release_lock,
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
JOB_EXECUTION_LOCK = "lk_job_execution"
JOB_EXECUTION_BATCH_SIZE = 10


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

    local_now = datetime.now(TMZ_SECONDARY)
    for service_automation in service_automations:
        with SessionLocal() as atomic_session:
            starting_at = datetime(
                local_now.year,
                local_now.month,
                local_now.day,
                service_automation.starting_at.hour,
                service_automation.starting_at.minute,
                service_automation.starting_at.second,
                tzinfo=TMZ_SECONDARY,
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
            except exceptions.APIException as e:
                company_notification = CompanyNotification(
                    company_id=service_automation.company_id,
                    operator_types=[OperatorType.ADMIN, OperatorType.MANAGER],
                    type=NotificationType.EXCEPTION,
                    title="SERVICE_CREATION_FAILED",
                    details={
                        "error": e.headers.get("X-Error"),
                        "detail": e.detail,
                        "service_automation": {
                            "id": service_automation.id,
                            "name": service_automation.name,
                        },
                    },
                )
                atomic_session.add(company_notification)
                atomic_session.commit()
                continue

            service_assignment_automations = (
                session.query(ServiceAssignmentAutomation)
                .filter(
                    ServiceAssignmentAutomation.service_automation_id
                    == service_automation.id
                )
                .all()
            )
            for service_assignment in service_assignment_automations:
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


def run_statement_creation_job(session: Session, job: Job):
    """
    Placeholder function to execute a statement creation job.
    """
    print(f"Executed statement creation job {job.id} at {datetime.now(TMZ_PRIMARY)}")


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
        return None

    # Clamp candidate to trigger_till if it's beyond it.
    if job.trigger_till is not None and candidate >= job.trigger_till:
        return None

    return candidate


def start_job_runner():
    queue_lock = None
    try:
        try:
            queue_lock = acquire_lock(JOB_EXECUTION_LOCK, blocking=False)
        except exceptions.LockAcquireTimeout:
            # Another job runner is already handling jobs.
            return 0

        have_jobs = True
        while have_jobs:
            with SessionLocal() as session:
                jobs = (
                    session.query(Job)
                    .filter(
                        Job.triggering_mode == TriggeringMode.AUTO,
                        Job.next_trigger_on <= datetime.now(TMZ_PRIMARY),
                    )
                    .order_by(Job.id)
                    .limit(JOB_EXECUTION_BATCH_SIZE)
                    .all()
                )
                if not jobs:
                    have_jobs = False
                    continue

                for job in jobs:
                    try:
                        if job.job_type == JobType.SERVICE_CREATION:
                            run_service_creation_job(session, job)
                        elif job.job_type == JobType.STATEMENT_CREATION:
                            run_statement_creation_job(session, job)
                    except Exception as e:
                        logging.error("Job %s failed: %s", job.id, str(e))
                        session.rollback()
                        continue

                    job.next_trigger_on = calculate_next_trigger_on(job)
                    job.last_trigger_on = datetime.now(TMZ_PRIMARY)
                    if job.next_trigger_on is None:
                        job.triggering_mode = TriggeringMode.DISABLED
                    session.commit()
    finally:
        release_lock(queue_lock)
