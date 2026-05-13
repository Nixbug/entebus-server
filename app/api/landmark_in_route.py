"""
Landmark in Route API Router for EnteBus.

Provides endpoints for managing landmarks in routes, including creation,
update, deletion and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Depends, status, Query, Response
from typing import List, Tuple
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
    SessionLocal,
)
from app.src.enums import OrderIn
from app.src.urls import URL_LANDMARK_IN_ROUTE
from app.src.validators import (
    validate_id,
    verify_token,
    verify_permission,
    validate_route,
)
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    update_if_changed,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
)
from app.src.enums import RouteStatus
from app.src.filters import (
    IDFilter,
    CreatedOnFilter,
    UpdatedOnFilter,
    PaginationFilter,
)
from app.src.openobserve import log_event
from app.src import exceptions
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.constants import MAX_LANDMARKS_PER_ROUTE

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


# Output schema
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


# Input Forms
class CreateForm(BaseModel):
    """Form data for creating a new landmark in route."""

    route_id: int = Field()
    landmark_id: int = Field()
    distance_from_start: int = Field(gt=-1)
    arrival_delta: int = Field(gt=-1)
    departure_delta: int = Field(gt=-1)


class UpdateForm(BaseModel):
    """Form data for updating an landmark in route."""

    distance_from_start: int = Field(default=None, gt=-1)
    arrival_delta: int = Field(default=None, gt=-1)
    departure_delta: int = Field(default=None, gt=-1)


## Query Parameters
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


# Functions
def create_landmark_in_route(
    session: Session, route: Route, form_param: CreateForm
) -> dict:
    """
    Creates a new landmark in route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        route (Route): The route to which the landmark will be added.
        form_param (CreateForm): Form data for creating a landmark in route.

    Returns:
        dict: The created landmark in route data.
    """
    validate_id(session, Landmark, form_param.landmark_id, LandmarkInRoute.landmark_id)
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
    return landmark_in_route_data


def update_landmark_in_route(
    session: Session, landmark_in_route: LandmarkInRoute, form_param: UpdateForm
) -> Tuple[bool, dict]:
    """
    Updates an existing landmark in route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        landmark_in_route (LandmarkInRoute): The existing landmark in route record to be updated.
        form_param (UpdateForm): Form data for updating the landmark in route.


    Returns:
        Tuple[bool, dict]: A tuple containing a boolean indicating if updates were made and the updated landmark in route data.
    """
    arrival_delta = (
        form_param.arrival_delta
        if form_param.arrival_delta is not None
        else landmark_in_route.arrival_delta
    )
    departure_delta = (
        form_param.departure_delta
        if form_param.departure_delta is not None
        else landmark_in_route.departure_delta
    )

    if (
        arrival_delta is not None
        and departure_delta is not None
        and arrival_delta > departure_delta
    ):
        raise exceptions.InvalidValue(LandmarkInRoute.arrival_delta)

    route = validate_id(
        session, Route, landmark_in_route.route_id, LandmarkInRoute.route_id
    )

    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(landmark_in_route, update_data)

    have_updates = session.is_modified(landmark_in_route)
    if have_updates:
        is_valid = validate_route(route.id, session)
        if is_valid:
            route.status = RouteStatus.VALID
        else:
            route.status = RouteStatus.INVALID

        session.commit()
        session.refresh(landmark_in_route)

    landmark_in_route_data = jsonable_encoder(landmark_in_route)
    return have_updates, landmark_in_route_data


def delete_landmark_in_route(
    session: Session, landmark_in_route: LandmarkInRoute
) -> dict:
    """
    Deletes a landmark in route record from the database.

    Args:
        session (Session): SQLAlchemy database session.
        landmark_in_route (LandmarkInRoute): The landmark in route record to be deleted.

    Returns:
        dict: The deleted landmark in route data.
    """
    route = validate_id(
        session, Route, landmark_in_route.route_id, LandmarkInRoute.route_id
    )
    landmark_in_route_data = jsonable_encoder(landmark_in_route)
    session.delete(landmark_in_route)
    session.flush()
    is_valid = validate_route(route.id, session)
    if is_valid:
        route.status = RouteStatus.VALID
    else:
        route.status = RouteStatus.INVALID

    session.commit()
    return landmark_in_route_data


def search_landmark_in_route(
    session: Session, query_params: QueryParams
) -> List[LandmarkInRoute]:
    """
    Search for Landmark In Route based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve landmarks in route that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[LandmarkInRoute]: List of Landmark In Route that match the search criteria.
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
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_LANDMARK_IN_ROUTE,
    tags=["Landmark In Route"],
    response_model=LandmarkInRouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidValue(LandmarkInRoute.arrival_delta),
            exceptions.UnknownValue(LandmarkInRoute.route_id),
            exceptions.UnknownValue(LandmarkInRoute.landmark_id),
        ]
    ),
    description=(
        """
            **Creates a new landmark in route.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.route.create` or `company.route.update` permission.    
            - Departure delta must be greater than arrival delta.    
            - When creating a new landmark in a route, the route will be validated and status of the route will be updated.      
        """
    ),
)
async def create_landmark_in_route_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        can_create = verify_permission(
            roles, ExecutivePermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        can_update = verify_permission(
            roles, ExecutivePermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (can_create | can_update):
            raise exceptions.NoPermission()

        route = validate_id(
            session, Route, form_param.route_id, LandmarkInRoute.route_id
        )
        landmark_in_route_data = create_landmark_in_route(session, route, form_param)

        log_event(token, request_info, landmark_in_route_data)
        return landmark_in_route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_LANDMARK_IN_ROUTE}/{{id}}",
    tags=["Landmark In Route"],
    response_model=LandmarkInRouteSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidValue(LandmarkInRoute.arrival_delta),
            exceptions.UnknownValue(LandmarkInRoute.id),
        ]
    ),
    description=(
        """
            **Updates an existing landmark in route.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.route.create` or `company.route.update` permission.    
            - Departure delta must be greater than arrival delta.    
            - When updating a landmark in a route, the route will be validated and status of the route will be updated.    
        """
    ),
)
async def update_landmark_in_route_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        can_create = verify_permission(
            roles, ExecutivePermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        can_update = verify_permission(
            roles, ExecutivePermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (can_create | can_update):
            raise exceptions.NoPermission()

        landmark_in_route = validate_id(
            session, LandmarkInRoute, id, LandmarkInRoute.id
        )
        have_updates, landmark_in_route_data = update_landmark_in_route(
            session,
            landmark_in_route,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
        )

        if have_updates:
            log_event(token, request_info, landmark_in_route_data)
        return landmark_in_route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_LANDMARK_IN_ROUTE}/{{id}}",
    tags=["Landmark In Route"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
        ]
    ),
    description=(
        """
            **Deletes a specific landmark assigned to a route.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.route.create` or `company.route.update` permission.    
            - When deleting a landmark in a route, the route will be validated and status will be updated.    
            - Returns 204 No Content even if the specified landmark in route does not exist.    
        """
    ),
)
async def delete_landmark_in_route_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        can_create = verify_permission(
            roles, ExecutivePermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        can_update = verify_permission(
            roles, ExecutivePermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (can_create | can_update):
            raise exceptions.NoPermission()

        landmark_in_route = (
            session.query(LandmarkInRoute).filter(LandmarkInRoute.id == id).first()
        )
        if landmark_in_route is not None:
            landmark_in_route_data = delete_landmark_in_route(
                session, landmark_in_route
            )
            log_event(token, request_info, landmark_in_route_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_LANDMARK_IN_ROUTE,
    tags=["Landmark In Route"],
    response_model=List[LandmarkInRouteSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of landmarks in route.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_landmark_in_route_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_landmark_in_route(
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
    URL_LANDMARK_IN_ROUTE,
    tags=["Landmark In Route"],
    response_model=LandmarkInRouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidValue(LandmarkInRoute.arrival_delta),
            exceptions.UnknownValue(LandmarkInRoute.route_id),
            exceptions.UnknownValue(LandmarkInRoute.landmark_id),
            exceptions.LimitExceeded(LandmarkInRoute),
        ]
    ),
    description=(
        f"""
            **Creates a new landmark in route.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.route.create` or `company.route.update` permission.    
            - Logged-in operator can only add landmarks to routes belonging to their company.    
            - Departure delta must be greater than arrival delta.    
            - When creating a landmark in a route, the route will be validated and status of the route will be updated.  
            - Maximum `{MAX_LANDMARKS_PER_ROUTE}` landmarks allowed per route
        """
    ),
)
async def create_landmark_in_route_for_operator(
    form_param: CreateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        can_create = verify_permission(
            roles, OperatorPermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        can_update = verify_permission(
            roles, OperatorPermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (can_create | can_update):
            raise exceptions.NoPermission()

        route = validate_id(
            session,
            Route,
            form_param.route_id,
            LandmarkInRoute.route_id,
            extra_filter=(Route.company_id == token.company_id),
        )
        landmark_count = (
            session.query(LandmarkInRoute)
            .filter(
                LandmarkInRoute.route_id == route.id,
            )
            .count()
        )

        if landmark_count >= MAX_LANDMARKS_PER_ROUTE:
            raise exceptions.LimitExceeded(LandmarkInRoute)

        landmark_in_route_data = create_landmark_in_route(session, route, form_param)
        log_event(token, request_info, landmark_in_route_data)
        return landmark_in_route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_LANDMARK_IN_ROUTE}/{{id}}",
    tags=["Landmark In Route"],
    response_model=LandmarkInRouteSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidValue(LandmarkInRoute.arrival_delta),
            exceptions.UnknownValue(LandmarkInRoute.id),
        ]
    ),
    description=(
        """
            **Updates an existing landmark in route.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.route.create` or `company.route.update` permission.    
            - Logged-in operator can only update landmarks in routes belonging to their company.    
            - Departure delta must be greater than arrival delta.    
            - When updating a landmark in a route, the route will be validated and status of the route will be updated.    
        """
    ),
)
async def update_landmark_in_route_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        can_create = verify_permission(
            roles, OperatorPermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        can_update = verify_permission(
            roles, OperatorPermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (can_create | can_update):
            raise exceptions.NoPermission()

        landmark_in_route = validate_id(
            session,
            LandmarkInRoute,
            id,
            LandmarkInRoute.id,
            extra_filter=(LandmarkInRoute.company_id == token.company_id),
        )
        have_updates, landmark_in_route_data = update_landmark_in_route(
            session,
            landmark_in_route,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
        )

        if have_updates:
            log_event(token, request_info, landmark_in_route_data)
        return landmark_in_route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_LANDMARK_IN_ROUTE}/{{id}}",
    tags=["Landmark In Route"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
        ]
    ),
    description=(
        """
            **Deletes a specific landmark assigned to a route.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.route.create` or `company.route.update` permission.    
            - Logged-in operator can only delete landmarks from routes belonging to their company.    
            - When deleting a landmark in a route, the route will be validated and status will be updated.    
            - Returns 204 No Content even if the specified landmark in route does not exist.    
        """
    ),
)
async def delete_landmark_in_route_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        can_create = verify_permission(
            roles, OperatorPermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        can_update = verify_permission(
            roles, OperatorPermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (can_create | can_update):
            raise exceptions.NoPermission()

        landmark_in_route = (
            session.query(LandmarkInRoute)
            .filter(
                LandmarkInRoute.id == id, LandmarkInRoute.company_id == token.company_id
            )
            .first()
        )
        if landmark_in_route is not None:
            landmark_in_route_data = delete_landmark_in_route(
                session, landmark_in_route
            )
            log_event(token, request_info, landmark_in_route_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_LANDMARK_IN_ROUTE,
    tags=["Landmark In Route"],
    response_model=List[LandmarkInRouteSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of landmarks in route.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_landmark_in_route_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_landmark_in_route(
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
    URL_LANDMARK_IN_ROUTE,
    tags=["Landmark In Route"],
    response_model=List[LandmarkInRouteSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of landmarks in route.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_landmark_in_route_for_vendor(
    query_params: QueryParamsForVE = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        verify_token(session, VendorToken, access_token.credentials)

        return search_landmark_in_route(
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
    URL_LANDMARK_IN_ROUTE,
    tags=["Landmark In Route"],
    response_model=List[LandmarkInRouteSchema],
    description=(
        """
            **Fetches a list of landmarks in route for public users.**    
        """
    ),
)
async def fetch_landmark_in_route_for_public(
    query_params: QueryParamsForPU = Depends(),
):
    try:
        session = SessionLocal()

        return search_landmark_in_route(
            session,
            QueryParams(
                **query_params.model_dump(),
                company_id=None,
            ),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
