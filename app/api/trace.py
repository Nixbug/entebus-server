"""
Trace API Router.

Provides endpoints for managing traces:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator)
"""

from datetime import datetime
from enum import StrEnum
from typing import Union
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import String, or_
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src.db import (
    Company,
    ExecutiveToken,
    OperatorToken,
    Trace,
    get_db_session,
)
from app.src import exceptions, schemas
from app.src.constants import MAX_TRACES_PER_COMPANY
from app.src.description import Description
from app.src.enums import OrderIn
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
from app.src.schemas import PatchForm
from app.src.urls import URL_ROUTE_TRACE
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    verify_token,
)

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class TraceSchema(BaseModel):
    """Schema for trace response."""

    id: int
    company_id: int
    name: str
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new trace for an operator."""

    name: str = Field(min_length=1, max_length=128, pattern=NAME_PATTERN)


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new trace for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new trace."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a trace."""

    name: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=NAME_PATTERN
    )


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering trace results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(
    IDFilter, CreatedOnFilter, UpdatedOnFilter, NameFilter, PaginationFilter
):
    """Query parameters for operators."""

    search: str | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_trace(
    session: Session,
    form_param: CreateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Creates a new trace record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a trace.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: The created trace data.
    """
    trace_count = (
        session.query(Trace).filter(Trace.company_id == form_param.company_id).count()
    )
    if trace_count >= MAX_TRACES_PER_COMPANY:
        raise exceptions.LimitExceeded(Trace)

    trace = Trace(
        company_id=form_param.company_id,
        name=form_param.name,
    )
    session.add(trace)
    session.commit()
    session.refresh(trace)

    trace_data = jsonable_encoder(trace)
    log_event(token, request_info, trace_data)
    return trace_data


def update_trace(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    trace_filter=None,
) -> dict:
    """
    Updates an existing trace record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the trace to update.
        form_param (UpdateForm): Form data for updating the trace.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        trace_filter (Optional): Additional filter to apply when fetching the trace.

    Returns:
        dict: JSON-encoded representation of the updated trace.
    """
    trace = validate_id(session, Trace, id, Trace.id, extra_filter=trace_filter)

    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(trace, update_data)

    if session.is_modified(trace):
        session.commit()
        session.refresh(trace)
        trace_data = jsonable_encoder(trace)
        log_event(token, request_info, trace_data)
    else:
        trace_data = jsonable_encoder(trace)
    return trace_data


def delete_trace(
    session: Session,
    id: int,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    trace_filter=None,
) -> None:
    """
    Deletes a trace from the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the trace to delete.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        trace_filter (Optional): Additional filter to apply when fetching the trace.
    """
    trace = get_by_id(session, Trace, id, extra_filter=trace_filter)
    if trace is None:
        return

    trace_data = jsonable_encoder(trace)
    session.delete(trace)
    session.commit()
    log_event(token, request_info, trace_data)


def search_traces(session: Session, query_params: QueryParams) -> list[Trace]:
    """
    Search for traces based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve traces that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Trace]: List of traces that match the search criteria.
    """
    query = session.query(Trace)
    if query_params.company_id is not None:
        query = query.filter(Trace.company_id == query_params.company_id)

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Trace.id.cast(String).ilike(search),
                Trace.name.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Trace, query_params)
    query = apply_name_filters(query, Trace, query_params)
    query = apply_created_on_filters(query, Trace, query_params)
    query = apply_updated_on_filters(query, Trace, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Trace, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    traces = query.all()
    return traces


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.LimitExceeded(Trace),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Trace.id),
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
    .add_head("Creates a new trace.")
    .add_line("Duplicate trace names are not allowed.")
    .add_line(f"Maximum `{MAX_TRACES_PER_COMPANY}` traces allowed per company.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing trace.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing trace.")
    .add_line("Returns 204 No Content even if the specified trace does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of traces.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_ROUTE_TRACE,
    summary="Create trace",
    tags=["Trace"],
    response_model=TraceSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            *POST_EXCEPTIONS,
            exceptions.UnknownValue(Trace.company_id),
        ]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.trace.create` permission.")
        .to_string()
    ),
)
async def create_trace_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_TRACE],
        )
        validate_id(session, Company, form_param.company_id, Trace.company_id)
        return create_trace(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_ROUTE_TRACE}/{{id}}",
    summary="Update trace",
    tags=["Trace"],
    response_model=TraceSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.trace.update` permission.")
        .to_string()
    ),
)
async def update_trace_for_executive(
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
            [ExecutivePermissionPath.UPDATE_COMPANY_TRACE],
        )
        return update_trace(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_ROUTE_TRACE}/{{id}}",
    summary="Delete trace",
    tags=["Trace"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `company.trace.delete` permission."
        )
        .to_string()
    ),
)
async def delete_trace_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_TRACE],
        )
        delete_trace(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_ROUTE_TRACE,
    summary="Fetch trace",
    tags=["Trace"],
    response_model=list[TraceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_traces_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_traces(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_ROUTE_TRACE,
    summary="Create trace",
    tags=["Trace"],
    response_model=TraceSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.trace.create` permission.")
        .to_string()
    ),
)
async def create_trace_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_TRACE],
        )
        return create_trace(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_ROUTE_TRACE}/{{id}}",
    summary="Update trace",
    tags=["Trace"],
    response_model=TraceSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.trace.update` permission.")
        .to_string()
    ),
)
async def update_trace_for_operator(
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
            [OperatorPermissionPath.UPDATE_COMPANY_TRACE],
        )
        return update_trace(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
            trace_filter=(Trace.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_ROUTE_TRACE}/{{id}}",
    summary="Delete trace",
    tags=["Trace"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in operator must have the `company.trace.delete` permission."
        )
        .to_string()
    ),
)
async def delete_trace_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_TRACE],
        )
        delete_trace(
            session,
            id,
            token,
            request_info,
            trace_filter=(Trace.company_id == token.company_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_ROUTE_TRACE,
    summary="Fetch trace",
    tags=["Trace"],
    response_model=list[TraceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_traces_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_traces(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
