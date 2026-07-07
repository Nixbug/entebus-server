"""
Landmark in Route API Router.

Provides endpoints for managing landmarks in routes:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator, vendor, public)
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Depends, status, Query, Response
from typing import Union
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm.session import Session
from pydantic import BaseModel, Field
from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.src.db import (
    LandmarkInRoute,
    Route,
    Landmark,
    ExecutiveToken,
    OperatorToken,
    VendorToken,
    get_db_session,
)
from app.src.enums import OrderIn
from app.src.urls import URL_LANDMARK_IN_ROUTE
from app.src.validators import (
    validate_id,
    verify_token,
    validate_route,
    authorize_executive,
    authorize_operator,
)
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    update_if_changed,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
)
from app.src.redis import acquire_lock, release_lock
from app.src.enums import RouteStatus
from app.src.filters import (
    IDFilter,
    CreatedOnFilter,
    UpdatedOnFilter,
    PaginationFilter,
)
from app.src.openobserve import log_event
from app.src.description import Description
from app.src import exceptions, schemas
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.constants import MAX_LANDMARKS_PER_ROUTE

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class LandmarkInRouteSchema(BaseModel):
    """Schema for landmark in route response."""

    id: int
    company_id: int
    route_id: int
    landmark_id: int
    distance_from_start: int
    arrival_delta: int
    departure_delta: int
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """Form data for creating a new landmark in route."""

    route_id: int = Field()
    landmark_id: int = Field()
    distance_from_start: int = Field(gt=-1)
    arrival_delta: int = Field(gt=-1)
    departure_delta: int = Field(gt=-1)


class UpdateForm(BaseModel):
    """Form data for updating an landmark in route."""

    distance_from_start: int | None = Field(default=None, gt=-1)
    arrival_delta: int | None = Field(default=None, gt=-1)
    departure_delta: int | None = Field(default=None, gt=-1)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering landmark in route results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    DISTANCE_FROM_START = "distance_from_start"


class QueryParamsForPU(IDFilter, CreatedOnFilter, UpdatedOnFilter, PaginationFilter):
    """Query parameters for public."""

    route_id: int | None = Field(Query(default=None))
    landmark_id: int | None = Field(Query(default=None))
    distance_from_start_ge: int | None = Field(Query(default=None))
    distance_from_start_le: int | None = Field(Query(default=None))
    arrival_delta_ge: int | None = Field(Query(default=None))
    arrival_delta_le: int | None = Field(Query(default=None))
    departure_delta_ge: int | None = Field(Query(default=None))
    departure_delta_le: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForOP(QueryParamsForPU):
    """Query parameters for operators."""

    pass


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParamsForVE(QueryParamsForEX):
    """Query parameters for vendors."""

    pass


class QueryParams(QueryParamsForEX):
    """Generic combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Lock Generator
# ---------------------------------------------------------------------------
def construct_route_transition_lock(route_id: int) -> str:
    """
    Creates a Redis lock key used to prevent concurrent route transition operations.

    Prevents concurrent create, update, and delete operations on the same
    route, as these actions can affect the route's status and validation.
    Only one operation is allowed at a time.

    Args:
        route_id (int): The ID of the route for which to create the lock.
    """
    return f"lk_route_:{route_id}"


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_landmark_in_route(
    session: Session,
    form_param: CreateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    route_filter=None,
) -> dict:
    """
    Creates a new landmark in route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a landmark in route.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        route_filter (Optional): Additional filter to apply when validating the route ID.

    Returns:
        dict: The created landmark in route data.
    """
    route_lock = None
    try:
        route = validate_id(
            session,
            Route,
            form_param.route_id,
            LandmarkInRoute.route_id,
            extra_filter=route_filter,
        )
        route_lock = acquire_lock(construct_route_transition_lock(route.id))
        session.refresh(route)

        landmark_count = (
            session.query(LandmarkInRoute)
            .filter(
                LandmarkInRoute.route_id == route.id,
            )
            .count()
        )
        if landmark_count >= MAX_LANDMARKS_PER_ROUTE:
            raise exceptions.LimitExceeded(LandmarkInRoute)

        validate_id(
            session, Landmark, form_param.landmark_id, LandmarkInRoute.landmark_id
        )
        if form_param.arrival_delta > form_param.departure_delta:
            raise exceptions.InvalidValue(LandmarkInRoute.arrival_delta)

        landmark_in_route = LandmarkInRoute(
            company_id=route.company_id,
            route_id=form_param.route_id,
            landmark_id=form_param.landmark_id,
            distance_from_start=form_param.distance_from_start,
            arrival_delta=form_param.arrival_delta,
            departure_delta=form_param.departure_delta,
        )
        session.add(landmark_in_route)
        session.flush()

        is_valid = validate_route(route.id, session)
        if is_valid:
            route.status = RouteStatus.VALID
        else:
            route.status = RouteStatus.INVALID
        session.commit()
        session.refresh(landmark_in_route)

        landmark_in_route_data = jsonable_encoder(landmark_in_route)
        log_event(token, request_info, landmark_in_route_data)
        return landmark_in_route_data
    finally:
        release_lock(route_lock)


def update_landmark_in_route(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    landmark_in_route_filter=None,
) -> dict:
    """
    Updates an existing landmark in route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the landmark in route to update.
        form_param (UpdateForm): Form data for updating the landmark in route.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        landmark_in_route_filter (Optional): Additional filter to apply when validating the landmark in route ID.

    Returns:
        dict: JSON-encoded representation of the updated landmark in route.
    """
    route_lock = None
    try:
        landmark_in_route = validate_id(
            session,
            LandmarkInRoute,
            id,
            LandmarkInRoute.id,
            extra_filter=landmark_in_route_filter,
        )

        route_lock = acquire_lock(
            construct_route_transition_lock(landmark_in_route.route_id)
        )
        session.refresh(landmark_in_route)

        # Validate arrival and departure deltas
        update_data = form_param.model_dump(exclude_unset=True)
        if "arrival_delta" in update_data:
            arrival_delta = update_data["arrival_delta"]
        else:
            arrival_delta = landmark_in_route.arrival_delta
        if "departure_delta" in update_data:
            departure_delta = update_data["departure_delta"]
        else:
            departure_delta = landmark_in_route.departure_delta
        if arrival_delta > departure_delta:
            raise exceptions.InvalidValue(LandmarkInRoute.arrival_delta)

        update_if_changed(landmark_in_route, update_data)
        if session.is_modified(landmark_in_route):
            route = validate_id(
                session, Route, landmark_in_route.route_id, LandmarkInRoute.route_id
            )
            is_valid = validate_route(route.id, session)
            if is_valid:
                route.status = RouteStatus.VALID
            else:
                route.status = RouteStatus.INVALID

            session.commit()
            session.refresh(landmark_in_route)
            landmark_in_route_data = jsonable_encoder(landmark_in_route)
            log_event(token, request_info, landmark_in_route_data)
        else:
            landmark_in_route_data = jsonable_encoder(landmark_in_route)
        return landmark_in_route_data
    finally:
        release_lock(route_lock)


def delete_landmark_in_route(
    session: Session,
    id: int,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    landmark_in_route_filter=None,
) -> None:
    """
    Deletes a landmark in route record from the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the landmark in route to delete.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        landmark_in_route_filter (Optional): Additional filter while fetching landmark in route.
    """
    route_lock = None
    try:
        landmark_in_route = get_by_id(
            session,
            LandmarkInRoute,
            id,
            extra_filter=landmark_in_route_filter,
        )
        if landmark_in_route is None:
            return

        route = validate_id(
            session, Route, landmark_in_route.route_id, LandmarkInRoute.route_id
        )
        route_lock = acquire_lock(construct_route_transition_lock(route.id))
        session.refresh(route)

        landmark_in_route_data = jsonable_encoder(landmark_in_route)
        session.delete(landmark_in_route)
        session.flush()

        is_valid = validate_route(route.id, session)
        if is_valid:
            route.status = RouteStatus.VALID
        else:
            route.status = RouteStatus.INVALID

        session.commit()
        log_event(token, request_info, landmark_in_route_data)
    finally:
        release_lock(route_lock)


def search_landmarks_in_route(
    session: Session, query_params: QueryParams
) -> list[LandmarkInRoute]:
    """
    Search for Landmark In Route based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve landmarks in route that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[LandmarkInRoute]: List of Landmark In Route that match the search criteria.
    """
    query = session.query(LandmarkInRoute)
    if query_params.company_id is not None:
        query = query.filter(LandmarkInRoute.company_id == query_params.company_id)
    if query_params.route_id is not None:
        query = query.filter(LandmarkInRoute.route_id == query_params.route_id)
    if query_params.landmark_id is not None:
        query = query.filter(LandmarkInRoute.landmark_id == query_params.landmark_id)
    if query_params.distance_from_start_ge is not None:
        query = query.filter(
            LandmarkInRoute.distance_from_start >= query_params.distance_from_start_ge
        )
    if query_params.distance_from_start_le is not None:
        query = query.filter(
            LandmarkInRoute.distance_from_start <= query_params.distance_from_start_le
        )
    if query_params.arrival_delta_ge is not None:
        query = query.filter(
            LandmarkInRoute.arrival_delta >= query_params.arrival_delta_ge
        )
    if query_params.arrival_delta_le is not None:
        query = query.filter(
            LandmarkInRoute.arrival_delta <= query_params.arrival_delta_le
        )
    if query_params.departure_delta_ge is not None:
        query = query.filter(
            LandmarkInRoute.departure_delta >= query_params.departure_delta_ge
        )
    if query_params.departure_delta_le is not None:
        query = query.filter(
            LandmarkInRoute.departure_delta <= query_params.departure_delta_le
        )

    # Generalized filters
    query = apply_id_filters(query, LandmarkInRoute, query_params)
    query = apply_created_on_filters(query, LandmarkInRoute, query_params)
    query = apply_updated_on_filters(query, LandmarkInRoute, query_params)

    # Ordering and pagination
    ordering_attr = getattr(LandmarkInRoute, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    landmark_in_routes = query.all()
    return landmark_in_routes


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidValue(LandmarkInRoute.arrival_delta),
    exceptions.UnknownValue(LandmarkInRoute.route_id),
    exceptions.UnknownValue(LandmarkInRoute.landmark_id),
    exceptions.LimitExceeded(LandmarkInRoute),
    exceptions.LockAcquireTimeout(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidValue(LandmarkInRoute.arrival_delta),
    exceptions.UnknownValue(LandmarkInRoute.id),
    exceptions.LockAcquireTimeout(),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.LockAcquireTimeout(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new landmark in route.")
    .add_line("Arrival delta cannot exceed departure delta.")
    .add_line(
        "When creating a landmark in a route, the route will be validated and status of the route will be updated."
    )
    .add_line(f"Maximum `{MAX_LANDMARKS_PER_ROUTE}` landmarks allowed per route")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing landmark in route.")
    .add_line("Arrival delta cannot exceed departure delta.")
    .add_line(
        "When updating a landmark in a route, the route will be validated and status of the route will be updated."
    )
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes a specific landmark assigned to a route.")
    .add_line(
        "When deleting a landmark in a route, the route will be validated and status will be updated."
    )
    .add_line(
        "Returns 204 No Content even if the specified landmark in route does not exist."
    )
)

GET_DESCRIPTION = Description().add_head("Fetches a list of landmarks in route.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_LANDMARK_IN_ROUTE,
    summary="Create landmark in route",
    tags=["Landmark In Route"],
    response_model=LandmarkInRouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.route.create` or `company.route.update` permission."
        )
        .to_string()
    ),
)
async def create_landmark_in_route_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [
                ExecutivePermissionPath.CREATE_COMPANY_ROUTE,
                ExecutivePermissionPath.UPDATE_COMPANY_ROUTE,
            ],
        )
        return create_landmark_in_route(
            session,
            form_param,
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_LANDMARK_IN_ROUTE}/{{id}}",
    summary="Update landmark in route",
    tags=["Landmark In Route"],
    response_model=LandmarkInRouteSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.route.create` or `company.route.update` permission."
        )
        .to_string()
    ),
)
async def update_landmark_in_route_for_executive(
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
            [
                ExecutivePermissionPath.CREATE_COMPANY_ROUTE,
                ExecutivePermissionPath.UPDATE_COMPANY_ROUTE,
            ],
        )
        return update_landmark_in_route(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_LANDMARK_IN_ROUTE}/{{id}}",
    summary="Delete landmark in route",
    tags=["Landmark In Route"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.route.create` or `company.route.update` permission."
        )
        .to_string()
    ),
)
async def delete_landmark_in_route_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [
                ExecutivePermissionPath.CREATE_COMPANY_ROUTE,
                ExecutivePermissionPath.UPDATE_COMPANY_ROUTE,
            ],
        )
        delete_landmark_in_route(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_LANDMARK_IN_ROUTE,
    summary="Fetch landmark in route",
    tags=["Landmark In Route"],
    response_model=list[LandmarkInRouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_landmarks_in_route_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_landmarks_in_route(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_LANDMARK_IN_ROUTE,
    summary="Create landmark in route",
    tags=["Landmark In Route"],
    response_model=LandmarkInRouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.route.create` or `company.route.update` permission."
        )
        .to_string()
    ),
)
async def create_landmark_in_route_for_operator(
    form_param: CreateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [
                OperatorPermissionPath.CREATE_COMPANY_ROUTE,
                OperatorPermissionPath.UPDATE_COMPANY_ROUTE,
            ],
        )
        return create_landmark_in_route(
            session,
            form_param,
            token,
            request_info,
            route_filter=(Route.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_LANDMARK_IN_ROUTE}/{{id}}",
    summary="Update landmark in route",
    tags=["Landmark In Route"],
    response_model=LandmarkInRouteSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.route.create` or `company.route.update` permission."
        )
        .to_string()
    ),
)
async def update_landmark_in_route_for_operator(
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
            [
                OperatorPermissionPath.CREATE_COMPANY_ROUTE,
                OperatorPermissionPath.UPDATE_COMPANY_ROUTE,
            ],
        )
        return update_landmark_in_route(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
            landmark_in_route_filter=(LandmarkInRoute.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_LANDMARK_IN_ROUTE}/{{id}}",
    summary="Delete landmark in route",
    tags=["Landmark In Route"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.route.create` or `company.route.update` permission."
        )
        .to_string()
    ),
)
async def delete_landmark_in_route_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [
                OperatorPermissionPath.CREATE_COMPANY_ROUTE,
                OperatorPermissionPath.UPDATE_COMPANY_ROUTE,
            ],
        )
        delete_landmark_in_route(
            session,
            id,
            token,
            request_info,
            landmark_in_route_filter=(LandmarkInRoute.company_id == token.company_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_LANDMARK_IN_ROUTE,
    summary="Fetch landmark in route",
    tags=["Landmark In Route"],
    response_model=list[LandmarkInRouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_landmarks_in_route_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_landmarks_in_route(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_LANDMARK_IN_ROUTE,
    summary="Fetch landmark in route",
    tags=["Landmark In Route"],
    response_model=list[LandmarkInRouteSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_landmarks_in_route_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return search_landmarks_in_route(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_LANDMARK_IN_ROUTE,
    summary="Fetch landmark in route",
    tags=["Landmark In Route"],
    response_model=list[LandmarkInRouteSchema],
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_landmarks_in_route_for_public(
    query_params: QueryParamsForPU = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        return search_landmarks_in_route(
            session,
            QueryParams(
                **query_params.model_dump(),
                company_id=None,
            ),
        )
    except Exception as e:
        exceptions.handle(e)
