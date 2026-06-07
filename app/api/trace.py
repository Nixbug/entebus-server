"""
Trace API Router for EnteBus.

Provides endpoints for managing traces, including creation,
update, deletion and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from sqlalchemy import or_, String
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from typing import List, Tuple
from sqlalchemy.orm import Session

from app.src.db import Company, ExecutiveToken, OperatorToken, SessionLocal, Trace
from app.src.description import Description
from app.src import exceptions
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
    apply_name_filters,
    apply_status_filters,
    apply_status_filters,
    apply_updated_on_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    update_if_changed,
)
from app.src.openobserve import log_event
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_ROUTE, URL_ROUTE_TRACE
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
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

    name: str = Field(min_length=1, max_length=4096, pattern=NAME_PATTERN)


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new trace for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new trace."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating a trace."""

    name: str = Field(min_length=1, max_length=4096, pattern=NAME_PATTERN, default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering route results."""

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
## Functions
# ---------------------------------------------------------------------------
def create_trace(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new trace record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a trace.

    Returns:
        dict: The created trace data.
    """
    trace = Trace(
        company_id=form_param.company_id,
        name=form_param.name,
    )
    session.add(trace)
    session.commit()
    session.refresh(trace)
    trace_data = jsonable_encoder(trace)
    return trace_data


def update_trace(
    session: Session, id: int, form_param: UpdateForm, extra_filter_for_trace=None
) -> Tuple[bool, dict]:
    """
    Updates an existing trace record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the trace to update.
        form_param (UpdateForm): Form data for updating the trace.
        extra_filter_for_trace (optional): Additional filter to apply when validating the trace ID.

    Returns:
        Tuple[bool, dict]: A tuple containing a boolean indicating whether any updates were made and the updated trace data.
    """
    trace = validate_id(
        session, Trace, id, Trace.id, extra_filter=extra_filter_for_trace
    )
    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(trace, update_data)
    have_updates = session.is_modified(trace)
    if have_updates:
        session.commit()
        session.refresh(trace)

    trace_data = jsonable_encoder(trace)
    return have_updates, trace_data


def delete_trace(session: Session, trace: Trace) -> dict:
    """
    Deletes a trace from the database.

    Args:
        session (Session): SQLAlchemy database session.
        trace (Trace): Trace to delete.

    Returns:
        dict: JSON-encoded representation of the deleted trace.
    """
    trace_data = jsonable_encoder(trace)
    session.delete(trace)
    session.commit()
    return trace_data


def search_trace(session: Session, query_params: QueryParams) -> List[Trace]:
    """
    Search for Traces based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve traces that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Trace]: List of Traces that match the search criteria.
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
    query = apply_status_filters(query, Trace, query_params)

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
## Common descriptions
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new trace.")
    .add_line("Duplicate trace names are not allowed.")
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
        .add_line(
            "Logged-in executive must have `company.route.trace.create` permission."
        )
        .to_string()
    ),
)
async def create_route_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_ROUTE_TRACE],
        )

        validate_id(session, Company, form_param.company_id, Trace.company_id)
        trace_data = create_trace(session, CreateForm(**form_param.model_dump()))

        log_event(token, request_info, trace_data)
        return trace_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_ROUTE_TRACE}/{{id}}",
    summary="Update trace",
    tags=["Trace"],
    response_model=TraceSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.route.trace.update` permission."
        )
        .to_string()
    ),
)
async def update_trace_for_executive(
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
            [ExecutivePermissionPath.UPDATE_COMPANY_ROUTE_TRACE],
        )

        have_updates, trace_data = update_trace(
            session, id, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, trace_data)
        return trace_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_ROUTE_TRACE}/{{id}}",
    summary="Delete trace",
    tags=["Trace"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `company.route.trace.delete` permission."
        )
        .to_string()
    ),
)
async def delete_trace_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_ROUTE_TRACE],
        )

        trace = session.query(Trace).filter(Trace.id == id).first()
        if trace is not None:
            trace_data = delete_trace(session, trace)
            log_event(token, request_info, trace_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_ROUTE_TRACE,
    summary="Fetch trace",
    tags=["Trace"],
    response_model=List[TraceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_traces_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_trace(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_ROUTE_TRACE,
    summary="Create trace",
    tags=["Trace"],
    response_model=TraceSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses([*POST_EXCEPTIONS]),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.route.trace.create` permission."
        )
        .to_string()
    ),
)
async def create_trace_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_ROUTE_TRACE],
        )

        trace_data = create_trace(
            session, CreateForm(**form_param.model_dump(), company_id=token.company_id)
        )
        log_event(token, request_info, trace_data)
        return trace_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_ROUTE_TRACE}/{{id}}",
    summary="Update trace",
    tags=["Trace"],
    response_model=TraceSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.route.trace.update` permission."
        )
        .to_string()
    ),
)
async def update_trace_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_ROUTE_TRACE],
        )

        have_updates, trace_data = update_trace(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            extra_filter_for_trace=(Trace.company_id == token.company_id),
        )
        if have_updates:
            log_event(token, request_info, trace_data)
        return trace_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_ROUTE_TRACE}/{{id}}",
    summary="Delete trace",
    tags=["Trace"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in operator must have the `company.route.trace.delete` permission."
        )
        .to_string()
    ),
)
async def delete_trace_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_ROUTE_TRACE],
        )

        trace = (
            session.query(Trace)
            .filter(Trace.id == id, Trace.company_id == token.company_id)
            .first()
        )
        if trace is not None:
            trace_data = delete_trace(session, trace)
            log_event(token, request_info, trace_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_ROUTE,
    summary="Fetch trace",
    tags=["Trace"],
    response_model=List[TraceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_traces_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_trace(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
