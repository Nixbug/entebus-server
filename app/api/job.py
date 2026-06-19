"""
Job API Router for EnteBus.

Provides endpoints for managing jobs within the EnteBus system, including creating,
updating, deleting, and retrieving job information. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime, timezone
from enum import StrEnum
from sqlalchemy import or_
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from typing import List, Tuple
from sqlalchemy.orm import Session

from app.src.db import Company, ExecutiveToken, Job, OperatorToken, SessionLocal
from app.src.description import Description
from app.src import exceptions
from app.src.enums import JobType, OrderIn, TriggeringMode
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    NameFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_name_filters,
    apply_updated_on_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    update_if_changed,
)
from app.src.openobserve import log_event
from app.src.regex import NAME_PATTERN
from app.src.scheduler import calculate_next_trigger_on
from app.src.urls import URL_JOB
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    validate_rrule_string,
    verify_token,
)
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.api.bearer import oauth2_executive, bearer_operator

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class JobSchema(BaseModel):
    """Pydantic schema for job response."""

    id: int
    company_id: int
    name: str
    description: str | None = None
    job_type: int
    recurrence_rule: str
    trigger_at: str
    triggering_mode: int
    next_trigger_on: datetime | None = None
    last_trigger_on: datetime | None = None
    trigger_from: datetime | None = None
    trigger_till: datetime | None = None
    updated_on: datetime | None = None
    created_on: datetime | None = None


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new job for an operator."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    job_type: JobType = Field(
        description=enum_str(JobType), default=JobType.SERVICE_CREATION
    )
    recurrence_rule: str = Field(min_length=1, max_length=256)
    trigger_at: datetime = Field()
    triggering_mode: TriggeringMode = Field(default=TriggeringMode.AUTO)
    trigger_from: datetime | None = Field(default=None)
    trigger_till: datetime | None = Field(default=None)


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new job for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new job."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating a job."""

    name: str = Field(default=None, min_length=1, max_length=32, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    recurrence_rule: str = Field(default=None, min_length=1, max_length=256)
    trigger_at: datetime = Field(default=None)
    triggering_mode: TriggeringMode = Field(default=None)
    trigger_from: datetime | None = Field(default=None)
    trigger_till: datetime | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering service results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    TRIGGER_FROM = "trigger_from"
    TRIGGER_TILL = "trigger_till"
    NEXT_TRIGGER_ON = "next_trigger_on"
    LAST_TRIGGER_ON = "last_trigger_on"


class QueryParamsForOP(
    IDFilter, CreatedOnFilter, UpdatedOnFilter, NameFilter, PaginationFilter
):
    """Query parameters for operators."""

    search: str | None = Field(Query(default=None))
    description: str | None = Field(Query(default=None))
    job_type_list: List[JobType] | None = Field(
        Query(default=None, description=enum_str(JobType))
    )
    recurrence_rule: str | None = Field(Query(default=None))
    trigger_at_ge: datetime | None = Field(Query(default=None))
    trigger_at_le: datetime | None = Field(Query(default=None))
    triggering_mode_list: List[TriggeringMode] | None = Field(
        Query(default=None, description=enum_str(TriggeringMode))
    )
    next_trigger_on_ge: datetime | None = Field(Query(default=None))
    next_trigger_on_le: datetime | None = Field(Query(default=None))
    last_trigger_on_ge: datetime | None = Field(Query(default=None))
    last_trigger_on_le: datetime | None = Field(Query(default=None))
    trigger_from_ge: datetime | None = Field(Query(default=None))
    trigger_from_le: datetime | None = Field(Query(default=None))
    trigger_till_ge: datetime | None = Field(Query(default=None))
    trigger_till_le: datetime | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executive users."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """Generic combined query parameters for jobs."""

    pass


# ---------------------------------------------------------------------------
## Functions
# ---------------------------------------------------------------------------
def create_job(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new job record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a job.

    Returns:
        dict: The created job data.
    """
    validate_rrule_string(form_param.recurrence_rule)
    job = Job(
        company_id=form_param.company_id,
        name=form_param.name,
        description=form_param.description,
        job_type=form_param.job_type,
        recurrence_rule=form_param.recurrence_rule,
        trigger_at=form_param.trigger_at,
        triggering_mode=form_param.triggering_mode,
        trigger_from=form_param.trigger_from,
        trigger_till=form_param.trigger_till,
    )
    # The next_trigger_on will always be after the current time.
    job.next_trigger_on = datetime.now(timezone.utc)
    job.next_trigger_on = calculate_next_trigger_on(job)
    session.add(job)
    session.commit()
    session.refresh(job)
    job_data = jsonable_encoder(job)
    return job_data


def update_job(
    session: Session, id: int, form_param: UpdateForm, extra_filter_for_job=None
) -> Tuple[bool, dict]:
    """
    Updates an existing job record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the job to update.
        form_param (UpdateForm): Form data for updating the job.
        extra_filter_for_job: Optional additional filter to apply when validating the job ID.

    Returns:
        Tuple[bool, dict]: A tuple containing a boolean indicating if updates were made, and the updated job data.
    """
    job = validate_id(session, Job, id, extra_filter=extra_filter_for_job)
    if form_param.recurrence_rule is not None:
        validate_rrule_string(form_param.recurrence_rule)

    update_if_changed(job, form_param)
    have_updates = session.is_modified(job)
    if have_updates:
        # If any of the fields that affect the next trigger time are updated, we need to recalculate it.
        if form_param.recurrence_rule is not None or form_param.trigger_at is not None:
            # The next_trigger_on will always be after the current time.
            job.next_trigger_on = datetime.now(timezone.utc)
            job.next_trigger_on = calculate_next_trigger_on(job)

        session.flush()
        session.commit()
    job_data = jsonable_encoder(job)
    return have_updates, job_data


def delete_job(session: Session, job: Job) -> dict:
    """
    Deletes a job from the database.

    Args:
        session (Session): SQLAlchemy database session.
        job (Job): Job to delete.

    Returns:
        dict: JSON-encoded representation of the deleted job.
    """
    job_data = jsonable_encoder(job)
    session.delete(job)
    session.commit()
    return job_data


def search_job(session: Session, query_params: QueryParams) -> List[Job]:
    """
    Searches for jobs in the database based on the provided query parameters.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters for filtering and sorting jobs.

    Returns:
        List[Job]: A list of job records matching the search criteria.
    """
    query = session.query(Job)
    if query_params.company_id is not None:
        query = query.filter(Job.company_id == query_params.company_id)
    if query_params.description is not None:
        query = query.filter(Job.description.ilike(f"%{query_params.description}%"))
    if query_params.job_type_list is not None:
        query = query.filter(Job.job_type.in_(query_params.job_type_list))
    if query_params.recurrence_rule is not None:
        query = query.filter(
            Job.recurrence_rule.ilike(f"%{query_params.recurrence_rule}%")
        )
    if query_params.trigger_at_ge is not None:
        query = query.filter(Job.trigger_at >= query_params.trigger_at_ge.time())
    if query_params.trigger_at_le is not None:
        query = query.filter(Job.trigger_at <= query_params.trigger_at_le.time())
    if query_params.triggering_mode_list is not None:
        query = query.filter(Job.triggering_mode.in_(query_params.triggering_mode_list))
    if query_params.next_trigger_on_ge is not None:
        query = query.filter(Job.next_trigger_on >= query_params.next_trigger_on_ge)
    if query_params.next_trigger_on_le is not None:
        query = query.filter(Job.next_trigger_on <= query_params.next_trigger_on_le)
    if query_params.last_trigger_on_ge is not None:
        query = query.filter(Job.last_trigger_on >= query_params.last_trigger_on_ge)
    if query_params.last_trigger_on_le is not None:
        query = query.filter(Job.last_trigger_on <= query_params.last_trigger_on_le)
    if query_params.trigger_from_ge is not None:
        query = query.filter(Job.trigger_from >= query_params.trigger_from_ge)
    if query_params.trigger_from_le is not None:
        query = query.filter(Job.trigger_from <= query_params.trigger_from_le)
    if query_params.trigger_till_ge is not None:
        query = query.filter(Job.trigger_till >= query_params.trigger_till_ge)
    if query_params.trigger_till_le is not None:
        query = query.filter(Job.trigger_till <= query_params.trigger_till_le)

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Job.name.ilike(search),
                Job.description.ilike(search),
                Job.recurrence_rule.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, query_params)
    query = apply_name_filters(query, query_params)
    query = apply_created_on_filters(query, query_params)
    query = apply_updated_on_filters(query, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Job, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    jobs = query.all()
    return jobs


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidRRULEstring(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidRRULEstring(),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new job.")
    .add_line("Jobs can be used to automate recurring tasks.")
    .add_line(
        "The `recurrence_rule` defines how often the job will run (iCalendar RRULE (RFC5545) format)."
    )
    .add_line(
        "The `trigger_at` time defines the time of day when the job will be triggered."
    )
    .add_line("The `triggering_mode` defines how the job will be triggered.")
    .add_line(
        "If the `triggering_mode` is AUTO, the system will automatically trigger the job based on the `next_trigger_on` field."
    )
    .add_line(
        "Job types can be used to categorize jobs and determine their behavior in the system."
    )
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing job.")
    .add_line(
        "The `next_trigger_on` will be updated if the job's `recurrence_rule` or `trigger_at` changes."
    )
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing job.")
    .add_line("Returns 204 No Content even if the specified job does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of jobs.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_JOB,
    summary="Create Job",
    tags=["Job"],
    response_model=JobSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.job.create` permission.")
        .to_string()
    ),
)
async def create_job_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_JOB],
        )

        validate_id(session, Company, form_param.company_id, Job.company_id)
        job_data = create_job(session, form_param)

        log_event(token, request_info, job_data)
        return job_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_JOB}/{{id}}",
    summary="Update Job",
    tags=["Job"],
    response_model=JobSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.job.update` permission.")
        .to_string()
    ),
)
async def update_job_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_JOB],
        )

        have_updates, job_data = update_job(
            session, id, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, job_data)
        return job_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_JOB}/{{id}}",
    summary="Delete Job",
    tags=["Job"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.job.delete` permission.")
        .to_string()
    ),
)
async def delete_job_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_JOB],
        )

        job = session.query(Job).filter(Job.id == id).first()
        if job is not None:
            job_data = delete_job(session, job)
            log_event(token, request_info, job_data)
        log_event(token, request_info, job_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_JOB,
    summary="Get Jobs",
    tags=["Job"],
    response_model=List[JobSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def get_jobs_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_job(session, QueryParams(**query_params.model_dump()))
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_JOB,
    summary="Create Job",
    tags=["Job"],
    response_model=JobSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.job.create` permission.")
        .to_string()
    ),
)
async def create_job_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token,
            [OperatorPermissionPath.CREATE_COMPANY_JOB],
        )

        job_data = create_job(
            session, CreateForm(**form_param.model_dump(), company_id=token.company_id)
        )
        log_event(token, request_info, job_data)
        return job_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_JOB}/{{id}}",
    summary="Update Job",
    tags=["Job"],
    response_model=JobSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.job.update` permission.")
        .to_string()
    ),
)
async def update_job_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token,
            [OperatorPermissionPath.UPDATE_COMPANY_JOB],
        )

        have_updates, job_data = update_job(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            extra_filter_for_job=(Job.company_id == token.company_id),
        )
        if have_updates:
            log_event(token, request_info, job_data)
        return job_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_JOB}/{{id}}",
    summary="Delete Job",
    tags=["Job"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.job.delete` permission.")
        .to_string()
    ),
)
async def delete_job_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token,
            [OperatorPermissionPath.DELETE_COMPANY_JOB],
        )

        job = (
            session.query(Job)
            .filter(Job.id == id, Job.company_id == token.company_id)
            .first()
        )
        if job is not None:
            job_data = delete_job(session, job)
            log_event(token, request_info, job_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_JOB,
    summary="Get Jobs",
    tags=["Job"],
    response_model=List[JobSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def get_jobs_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token)

        return search_job(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
