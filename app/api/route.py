"""
Route API Router for EnteBus.

Provides endpoints for managing routes, including creation,
update, deletion and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime, time
from enum import StrEnum
from typing import List, Tuple
from fastapi import APIRouter, status, Depends, Query, Response
from sqlalchemy.orm.session import Session
from sqlalchemy import or_, String
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.src.db import (
    Route,
    ExecutiveToken,
    SessionLocal,
    OperatorToken,
    Company,
    VendorToken,
)
from app.src import exceptions
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

    name: str = Field(min_length=1, max_length=4096, pattern=NAME_PATTERN, default=None)
    start_time: time = Field(default=None)


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

    status_list: List[RouteStatus] | None = Field(
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
## Functions
# ---------------------------------------------------------------------------
def create_route(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a route.

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
    return route_data


def update_route(
    session: Session, id: int, form_param: UpdateForm, extra_filter_for_route=None
) -> Tuple[bool, dict]:
    """
    Updates an existing route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the route to update.
        form_param (UpdateForm): Form data for updating the route.
        extra_filter_for_route (optional): Additional filter to apply when validating the route ID.

    Returns:
        Tuple[bool, dict]: A tuple containing a boolean indicating whether any updates were made and the updated route data.
    """
    route = validate_id(
        session, Route, id, Route.id, extra_filter=extra_filter_for_route
    )
    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(route, update_data)
    have_updates = session.is_modified(route)
    if have_updates:
        session.commit()
        session.refresh(route)

    route_data = jsonable_encoder(route)
    return have_updates, route_data


def delete_route(session: Session, route: Route) -> dict:
    """
    Deletes a route from the database.

    Args:
        session (Session): SQLAlchemy database session.
        route (Route): Route to delete.

    Returns:
        dict: JSON-encoded representation of the deleted route.
    """
    route_data = jsonable_encoder(route)
    session.delete(route)
    session.commit()
    return route_data


def search_route(session: Session, query_params: QueryParams) -> List[Route]:
    """
    Search for Routes based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve routes that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Route]: List of Routes that match the search criteria.
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
## Common descriptions
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new route.")
    .add_line("Duplicate route names are not allowed.")
    .add_line("By default the status of the route is INVALID.")
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
            exceptions.LimitExceeded(Route),
        ]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.route.create` permission.")
        .add_line("Duplicate route names are not allowed.")
        .add_line(f"Maximum `{MAX_ROUTES_PER_COMPANY}` routes allowed per company.")
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
            [ExecutivePermissionPath.CREATE_COMPANY_ROUTE],
        )

        validate_id(session, Company, form_param.company_id, Route.company_id)
        route_data = create_route(session, CreateForm(**form_param.model_dump()))

        log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


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
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_ROUTE],
        )

        have_updates, route_data = update_route(
            session, id, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


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
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_ROUTE],
        )

        route = session.query(Route).filter(Route.id == id).first()
        if route is not None:
            route_data = delete_route(session, route)
            log_event(token, request_info, route_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_ROUTE,
    summary="Fetch route",
    tags=["Route"],
    response_model=List[RouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_routes_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_route(
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
        .add_line("Duplicate route names are not allowed.")
        .add_line(f"Maximum `{MAX_ROUTES_PER_COMPANY}` routes allowed per company.")
        .to_string()
    ),
)
async def create_route_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_ROUTE],
        )

        route_data = create_route(
            session, CreateForm(**form_param.model_dump(), company_id=token.company_id)
        )
        log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


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
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_ROUTE],
        )

        have_updates, route_data = update_route(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            extra_filter_for_route=(Route.company_id == token.company_id),
        )
        if have_updates:
            log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


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
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_ROUTE],
        )

        route = (
            session.query(Route)
            .filter(Route.id == id, Route.company_id == token.company_id)
            .first()
        )
        if route is not None:
            route_data = delete_route(session, route)
            log_event(token, request_info, route_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_ROUTE,
    summary="Fetch route",
    tags=["Route"],
    response_model=List[RouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_routes_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_route(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_ROUTE,
    summary="Fetch route",
    tags=["Route"],
    response_model=List[RouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_routes_for_vendor(
    query_params: QueryParamsForVE = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        verify_token(session, VendorToken, access_token.credentials)

        return search_route(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_ROUTE,
    summary="Fetch route",
    tags=["Route"],
    response_model=List[RouteSchema],
    description=(
        GET_DESCRIPTION.copy()
        .add_line("By default only valid routes are returned.")
        .to_string()
    ),
)
async def fetch_routes_for_public(query_params: QueryParamsForPU = Depends()):
    try:
        session = SessionLocal()

        return search_route(
            session,
            QueryParams(
                **query_params.model_dump(),
                status_list=[RouteStatus.VALID],
                company_id=None,
            ),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
