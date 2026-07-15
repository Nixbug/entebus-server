"""
Job API Router.

Provides endpoints for managing jobs:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator)
"""

from datetime import datetime, time
from enum import StrEnum
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import String, or_
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions
from app.src.db import Company, ExecutiveToken, Job, OperatorToken, get_db_session
from app.src.description import Description
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
    apply_id_filters,
    apply_name_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    update_if_changed,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.regex import NAME_PATTERN
from app.src.scheduler import calculate_next_trigger_on
from app.src.schemas import PatchForm
from app.src.urls import URL_JOB
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    validate_rrule_string,
    verify_token,
)

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class JobSchema(BaseModel):
    """Schema for job response."""

    id: int
    company_id: int
    name: str
    description: str | None
    job_type: int
    recurrence_rule: str
    trigger_at: time
    triggering_mode: int
    next_trigger_on: datetime | None
    last_trigger_on: datetime | None
    trigger_from: datetime | None
    trigger_till: datetime | None
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new job for an operator."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    job_type: JobType = Field(
        default=JobType.SERVICE_CREATION,
        description=enum_str(JobType),
    )
    recurrence_rule: str = Field(min_length=1, max_length=256)
    trigger_at: time = Field()
    triggering_mode: TriggeringMode = Field(
        default=TriggeringMode.AUTO,
        description=enum_str(TriggeringMode),
    )
    trigger_from: datetime | None = Field(default=None)
    trigger_till: datetime | None = Field(default=None)


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new job for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new job."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a job."""

    name: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=NAME_PATTERN
    )
    description: Annotated[str | None, "nullable"] = Field(
        default=None, max_length=1024
    )
    recurrence_rule: str | None = Field(default=None, min_length=1, max_length=256)
    trigger_at: time | None = Field(default=None)
    triggering_mode: TriggeringMode | None = Field(
        default=None, description=enum_str(TriggeringMode)
    )
    trigger_from: Annotated[datetime | None, "nullable"] = Field(default=None)
    trigger_till: Annotated[datetime | None, "nullable"] = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering job results."""

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
    job_type_list: list[JobType] | None = Field(
        Query(default=None, description=enum_str(JobType))
    )
    recurrence_rule: str | None = Field(Query(default=None))
    trigger_at_ge: time | None = Field(Query(default=None))
    trigger_at_le: time | None = Field(Query(default=None))
    triggering_mode_list: list[TriggeringMode] | None = Field(
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
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_job(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | OperatorToken,
    request_info,
) -> dict:
    """
    Creates a new job record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a job.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info: Request information for logging.

    Returns:
        dict: The created job data.
    """
    # Validate the recurrence rule string before creating the job
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
    if job.triggering_mode == TriggeringMode.AUTO:
        job.next_trigger_on = calculate_next_trigger_on(job)
    else:
        job.next_trigger_on = None

    session.add(job)
    session.commit()
    session.refresh(job)

    job_data = jsonable_encoder(job)
    log_event(token, request_info, job_data)
    return job_data


def update_job(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken | OperatorToken,
    request_info,
    job_filter=None,
) -> dict:
    """
    Updates an existing job record.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the job to update.
        form_param (UpdateForm): Form data for updating the job.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info: Request information for logging.
        job_filter: Additional filter for job validation.

    Returns:
        dict: JSON-encoded representation of the updated job.
    """
    job = validate_id(session, Job, id, Job.id, extra_filter=job_filter)

    update_data = form_param.model_dump(exclude_unset=True)
    if "recurrence_rule" in update_data:
        if update_data["recurrence_rule"] != job.recurrence_rule:
            validate_rrule_string(update_data["recurrence_rule"])
            job.recurrence_rule = update_data["recurrence_rule"]
        update_data.pop("recurrence_rule", None)

    update_if_changed(job, update_data)
    if session.is_modified(job):
        if job.triggering_mode != TriggeringMode.AUTO:
            job.next_trigger_on = None
        else:
            have_critical_change = (
                form_param.recurrence_rule is not None
                or form_param.trigger_at is not None
                or form_param.trigger_from is not None
                or form_param.trigger_till is not None
            )
            if have_critical_change:
                job.next_trigger_on = calculate_next_trigger_on(job)

        session.commit()
        session.refresh(job)
        job_data = jsonable_encoder(job)
        log_event(token, request_info, job_data)
    else:
        job_data = jsonable_encoder(job)
    return job_data


def delete_job(
    session: Session,
    id: int,
    token: ExecutiveToken | OperatorToken,
    request_info,
    job_filter=None,
) -> None:
    """
    Deletes a job from the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the job to delete.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info: Request information for logging.
        job_filter: Additional filter for job validation.
    """
    job = get_by_id(session, Job, id, extra_filter=job_filter)
    if job is None:
        return

    job_data = jsonable_encoder(job)
    session.delete(job)
    session.commit()
    log_event(token, request_info, job_data)


def search_jobs(session: Session, query_params: QueryParams) -> list[Job]:
    """
    Searches for jobs in the database based on the provided query parameters.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters for filtering and sorting jobs.

    Returns:
        list[Job]: A list of job records matching the search criteria.
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
        query = query.filter(Job.trigger_at >= query_params.trigger_at_ge)
    if query_params.trigger_at_le is not None:
        query = query.filter(Job.trigger_at <= query_params.trigger_at_le)
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
                Job.id.cast(String).ilike(search),
                Job.name.ilike(search),
                Job.description.ilike(search),
                Job.recurrence_rule.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Job, query_params)
    query = apply_name_filters(query, Job, query_params)
    query = apply_created_on_filters(query, Job, query_params)
    query = apply_updated_on_filters(query, Job, query_params)

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
    exceptions.InvalidRRULEString(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Job.id),
    exceptions.InvalidRRULEString(),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common description
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
    summary="Create job",
    tags=["Job"],
    response_model=JobSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            *POST_EXCEPTIONS,
            exceptions.UnknownValue(Job.company_id),
        ]
    ),
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
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_JOB],
        )
        validate_id(session, Company, form_param.company_id, Job.company_id)
        return create_job(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_JOB}/{{id}}",
    summary="Update job",
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
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_JOB],
        )
        return update_job(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_JOB}/{{id}}",
    summary="Delete job",
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
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_JOB],
        )
        delete_job(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_JOB,
    summary="Fetch jobs",
    tags=["Job"],
    response_model=list[JobSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_jobs_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_jobs(session, QueryParams(**query_params.model_dump()))
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_JOB,
    summary="Create job",
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
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_JOB],
        )
        return create_job(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_JOB}/{{id}}",
    summary="Update job",
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
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_JOB],
        )
        return update_job(
            session,
            id,
            form_param,
            token,
            request_info,
            job_filter=(Job.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_JOB}/{{id}}",
    summary="Delete job",
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
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_JOB],
        )
        delete_job(
            session,
            id,
            token,
            request_info,
            job_filter=(Job.company_id == token.company_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_JOB,
    summary="Fetch jobs",
    tags=["Job"],
    response_model=list[JobSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_jobs_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_jobs(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
