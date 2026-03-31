"""
Route API Router for EnteBus.

Provides endpoints for managing routes, including creation,
update, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime, time
from enum import StrEnum
from typing import List
from fastapi import APIRouter, status, Depends, Query
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
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_ROUTE
from app.src.validators import (
    validate_id,
    verify_token,
    verify_permission,
)
from app.src.functions import (
    enum_str,
    get_request_info,
    get_executive_roles,
    get_operator_roles,
    fuse_exception_responses,
    resolve_model_defaults,
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

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


# Output Schema
class RouteSchema(BaseModel):
    """Schema for route response."""

    id: int
    company_id: int
    name: str
    start_time: time
    status: int
    updated_on: datetime | None
    created_on: datetime


# Input Forms
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


## Query Parameters
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


# Functions
def create_route(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a route.

    Returns:
        dict: The created route data.
    """
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


def update_route(session: Session, route: Route, form_param: UpdateForm):
    """
    Updates an existing route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        route (Route): The existing route record to be updated.
        form_param (UpdateForm): Form data for updating the route.

    Returns:
        dict: The updated route data.
    """
    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(route, update_data)
    have_updates = session.is_modified(route)
    if have_updates:
        session.commit()
        session.refresh(route)

    route_data = jsonable_encoder(route)
    return have_updates, route_data


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
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_ROUTE,
    tags=["Route"],
    response_model=RouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Route.company_id),
        ]
    ),
    description=(
        """
            **Creates a new route.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.route.create` permission.    
            - Duplicate route names are not allowed.       
            - By default the status of the route is INVALID.    
        """
    ),
)
async def create_route_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_ROUTE)

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
    tags=["Route"],
    response_model=RouteSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Route.id),
        ]
    ),
    description=(
        """
            **Updates an existing route for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.route.update` permission.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_route_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_ROUTE)

        route = validate_id(session, Route, id, Route.id)
        have_updates, route_data = update_route(
            session, route, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_ROUTE,
    tags=["Route"],
    response_model=List[RouteSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of routes.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_route_executive(
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
    tags=["Route"],
    response_model=RouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new route.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.route.create` permission.    
            - Duplicate route names are not allowed.       
        """
    ),
)
async def create_route_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_ROUTE)

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
    tags=["Route"],
    response_model=RouteSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Route.id),
        ]
    ),
    description=(
        """
            **Updates an existing route for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.route.update` permission.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_route_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_ROUTE)

        route = validate_id(
            session, Route, id, Route.id, (Route.company_id == token.company_id)
        )
        have_updates, route_data = update_route(
            session, route, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )
        if have_updates:
            log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_ROUTE,
    tags=["Route"],
    response_model=List[RouteSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of routes.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_route_operator(
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
    tags=["Route"],
    response_model=List[RouteSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of routes.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_route_vendor(
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
    tags=["Route"],
    response_model=List[RouteSchema],
    description=(
        """
            **Fetches a list of routes for public users.**    
            - By default only valid routes are returned.    
        """
    ),
)
async def fetch_route_public(query_params: QueryParamsForPU = Depends()):
    try:
        session = SessionLocal()

        query_params = resolve_model_defaults(
            QueryParams, **query_params.model_dump(), status_list=[RouteStatus.VALID]
        )
        return search_route(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
