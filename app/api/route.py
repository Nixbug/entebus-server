"""
Route API Router.

Provides endpoints for managing routes:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator, vendor, public)
"""

from datetime import datetime, time
from enum import StrEnum
from typing import Union
from fastapi import APIRouter, status, Depends, Query, Response
from sqlalchemy.orm.session import Session
from sqlalchemy import or_, String
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.src.db import (
    Route,
    ExecutiveToken,
    OperatorToken,
    Company,
    VendorToken,
    get_db_session,
)
from app.src import exceptions, schemas
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_ROUTE
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    verify_token,
)
from app.src.functions import (
    enum_str,
    get_by_id,
    get_request_info,
    fuse_exception_responses,
    update_if_changed,
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_name_filters,
    apply_status_filters,
    apply_id_filters,
)
from app.src.filters import (
    CreatedOnFilter,
    UpdatedOnFilter,
    NameFilter,
    IDFilter,
    PaginationFilter,
)
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.enums import OrderIn, RouteStatus
from app.src.constants import MAX_ROUTES_PER_COMPANY

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class RouteSchema(BaseModel):
    """Schema for route response."""

    id: int
    company_id: int
    name: str
    version: int
    start_time: time
    status: int
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new route for an operator."""

    name: str = Field(min_length=1, max_length=4096, pattern=NAME_PATTERN)
    start_time: time = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new route for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new route."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating a route."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        pattern=NAME_PATTERN,
    )
    start_time: time | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering route results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForPU(
    IDFilter, CreatedOnFilter, UpdatedOnFilter, NameFilter, PaginationFilter
):
    """Query parameters for public users."""

    search: str | None = Field(Query(default=None))
    start_time_ge: time | None = Field(Query(default=None))
    start_time_le: time | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForOP(QueryParamsForPU):
    """Query parameters for operators."""

    status_list: list[RouteStatus] | None = Field(
        Query(default=None, description=enum_str(RouteStatus))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParamsForVE(QueryParamsForEX):
    """Query parameters for vendors."""

    pass


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_route(
    session: Session,
    form_param: CreateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Creates a new route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a route.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: The created route data.
    """
    route_count = (
        session.query(Route).filter(Route.company_id == form_param.company_id).count()
    )
    if route_count >= MAX_ROUTES_PER_COMPANY:
        raise exceptions.LimitExceeded(Route)

    route = Route(
        company_id=form_param.company_id,
        name=form_param.name,
        start_time=form_param.start_time,
    )
    session.add(route)
    session.commit()
    session.refresh(route)

    route_data = jsonable_encoder(route)
    log_event(token, request_info, route_data)
    return route_data


def update_route(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    route_filter=None,
) -> dict:
    """
    Updates an existing route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the route to update.
        form_param (UpdateForm): Form data for updating the route.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        route_filter (Optional): Additional filter to apply when fetching the route.

    Returns:
        dict: JSON-encoded representation of the updated route.
    """
    route = validate_id(session, Route, id, Route.id, extra_filter=route_filter)

    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(route, update_data)

    if session.is_modified(route):
        route.version += 1
        session.commit()
        session.refresh(route)
        route_data = jsonable_encoder(route)
        log_event(token, request_info, route_data)
    else:
        route_data = jsonable_encoder(route)
    return route_data


def delete_route(
    session: Session,
    id: int,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    route_filter=None,
) -> None:
    """
    Deletes a route from the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the route to delete.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        route_filter (Optional): Additional filter to apply when fetching the route.
    """
    route = get_by_id(session, Route, id, extra_filter=route_filter)
    if route is None:
        return

    route_data = jsonable_encoder(route)
    session.delete(route)
    session.commit()
    log_event(token, request_info, route_data)


def search_routes(session: Session, query_params: QueryParams) -> list[Route]:
    """
    Search for Routes based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve routes that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Route]: List of Routes that match the search criteria.
    """
    query = session.query(Route)
    if query_params.company_id is not None:
        query = query.filter(Route.company_id == query_params.company_id)
    if query_params.start_time_ge is not None:
        query = query.filter(Route.start_time >= query_params.start_time_ge)
    if query_params.start_time_le is not None:
        query = query.filter(Route.start_time <= query_params.start_time_le)

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Route.id.cast(String).ilike(search),
                Route.name.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Route, query_params)
    query = apply_name_filters(query, Route, query_params)
    query = apply_created_on_filters(query, Route, query_params)
    query = apply_updated_on_filters(query, Route, query_params)
    query = apply_status_filters(query, Route, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Route, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    routes = query.all()
    return routes


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.LimitExceeded(Route),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Route.id),
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
    .add_head("Creates a new route.")
    .add_line("Duplicate route names are not allowed.")
    .add_line("By default the status of the route is INVALID.")
    .add_line(f"Maximum `{MAX_ROUTES_PER_COMPANY}` routes allowed per company.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing route.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing route.")
    .add_line("Returns 204 No Content even if the specified route does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of routes.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_ROUTE,
    summary="Create route",
    tags=["Route"],
    response_model=RouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            *POST_EXCEPTIONS,
            exceptions.UnknownValue(Route.company_id),
        ]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.route.create` permission.")
        .to_string()
    ),
)
async def create_route_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_ROUTE],
        )
        validate_id(session, Company, form_param.company_id, Route.company_id)
        return create_route(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_ROUTE}/{{id}}",
    summary="Update route",
    tags=["Route"],
    response_model=RouteSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.route.update` permission.")
        .to_string()
    ),
)
async def update_route_for_executive(
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
            [ExecutivePermissionPath.UPDATE_COMPANY_ROUTE],
        )
        return update_route(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_ROUTE}/{{id}}",
    summary="Delete route",
    tags=["Route"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `company.route.delete` permission."
        )
        .to_string()
    ),
)
async def delete_route_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_ROUTE],
        )
        delete_route(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_ROUTE,
    summary="Fetch route",
    tags=["Route"],
    response_model=list[RouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_routes_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_routes(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_ROUTE,
    summary="Create route",
    tags=["Route"],
    response_model=RouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            *POST_EXCEPTIONS,
            exceptions.LimitExceeded(Route),
        ]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.route.create` permission.")
        .to_string()
    ),
)
async def create_route_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_ROUTE],
        )
        return create_route(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_ROUTE}/{{id}}",
    summary="Update route",
    tags=["Route"],
    response_model=RouteSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.route.update` permission.")
        .to_string()
    ),
)
async def update_route_for_operator(
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
            [OperatorPermissionPath.UPDATE_COMPANY_ROUTE],
        )
        return update_route(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
            route_filter=(Route.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_ROUTE}/{{id}}",
    summary="Delete route",
    tags=["Route"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in operator must have the `company.route.delete` permission."
        )
        .to_string()
    ),
)
async def delete_route_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_ROUTE],
        )
        delete_route(
            session,
            id,
            token,
            request_info,
            route_filter=(Route.company_id == token.company_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_ROUTE,
    summary="Fetch route",
    tags=["Route"],
    response_model=list[RouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_routes_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_routes(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_ROUTE,
    summary="Fetch route",
    tags=["Route"],
    response_model=list[RouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_routes_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return search_routes(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_ROUTE,
    summary="Fetch route",
    tags=["Route"],
    response_model=list[RouteSchema],
    description=(
        GET_DESCRIPTION.copy()
        .add_line("By default only valid routes are returned.")
        .to_string()
    ),
)
async def fetch_routes_for_public(
    query_params: QueryParamsForPU = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        return search_routes(
            session,
            QueryParams(
                **query_params.model_dump(),
                status_list=[RouteStatus.VALID],
                company_id=None,
            ),
        )
    except Exception as e:
        exceptions.handle(e)
