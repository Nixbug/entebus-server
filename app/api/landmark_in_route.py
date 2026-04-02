"""
Landmark in Route API Router for EnteBus.

Provides endpoints for managing landmarks in routes, including creation and update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, status
from typing import Tuple
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm.session import Session
from pydantic import BaseModel, Field
from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    LandmarkInRoute,
    Route,
    Landmark,
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
)
from app.src.urls import URL_LANDMARK_IN_ROUTE
from app.src.validators import (
    validate_id,
    verify_token,
    verify_permission,
    validate_route,
)
from app.src.functions import (
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    update_if_changed,
)
from app.src.enums import (
    RouteStatus,
)
from app.src.openobserve import log_event
from app.src import exceptions
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath

route_executive = APIRouter()
route_operator = APIRouter()


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
    """Form data for updating an  landmark in route."""

    distance_from_start: int | None = Field(default=None, gt=-1)
    arrival_delta: int | None = Field(default=None, gt=-1)
    departure_delta: int | None = Field(default=None, gt=-1)


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
            - Logged-in executive must have `create.company.route` or `update.company.route` permission.    
            - Departure delta must be greater than arrival delta.    
            - Duplicate landmarks in the same route are not allowed.    
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
        has_create = verify_permission(
            roles, ExecutivePermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        has_update = verify_permission(
            roles, ExecutivePermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (has_create | has_update):
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
            - Logged-in executive must have `create.company.route` or `update.company.route` permission.    
            - Departure delta must be greater than arrival delta.    
            - Duplicate landmarks in the same route are not allowed.    
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
        has_create = verify_permission(
            roles, ExecutivePermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        has_update = verify_permission(
            roles, ExecutivePermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (has_create | has_update):
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
        ]
    ),
    description=(
        """
            **Creates a new landmark in route.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `create.company.route` or `update.company.route` permission.    
            - Logged-in operator can only add landmarks to routes belonging to their company.    
            - Departure delta must be greater than arrival delta.    
            - Duplicate landmarks in the same route are not allowed.    
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
        has_create = verify_permission(
            roles, OperatorPermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        has_update = verify_permission(
            roles, OperatorPermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (has_create | has_update):
            raise exceptions.NoPermission()

        route = validate_id(
            session,
            Route,
            form_param.route_id,
            LandmarkInRoute.route_id,
            extra_filter=(Route.company_id == token.company_id),
        )
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
            - Logged-in operator must have `create.company.route` or `update.company.route` permission.    
            - Departure delta must be greater than arrival delta.    
            - Duplicate landmarks in the same route are not allowed.    
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
        has_create = verify_permission(
            roles, OperatorPermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False
        )
        has_update = verify_permission(
            roles, OperatorPermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False
        )
        if not (has_create | has_update):
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
