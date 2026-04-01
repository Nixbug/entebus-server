"""
Landmark in Route API Router for EnteBus.

Provides endpoints for managing landmarks in routes, including creation and update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm.session import Session
from pydantic import BaseModel, Field 
from sqlalchemy.orm import Session
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
    verify_permission,
    landmark_in_route,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    update_if_changed,
    apply_status_filters,
    apply_type_filters,
)
from app.src.enums import (
    RouteStatus,
)
from app.src.openobserve import log_event
from app.src import exceptions
from app.src.permissions.executive import  PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import  PermissionPath as OperatorPermissionPath

route_executive = APIRouter()
route_operator = APIRouter()


# Output schema
class LandmarkInRouteSchema(BaseModel):
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
def createLandmarkInRoute(session: Session, route: Route, form_param: CreateForm):
    validate_id(session, Landmark, form_param.landmark_id, LandmarkInRoute.landmark_id)
    if form_param.arrival_delta > form_param.departure_delta:
        raise exceptions.InvalidValue(LandmarkInRoute.arrival_delta)
    return LandmarkInRoute(
        company_id=route.company_id,
        route_id=form_param.route_id,
        landmark_id=form_param.landmark_id,
        distance_from_start=form_param.distance_from_start,
        arrival_delta=form_param.arrival_delta,
        departure_delta=form_param.departure_delta,
    )


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_LANDMARK_IN_ROUTE,
    tags=["LandmarkInRoute"],
    response_model=LandmarkInRouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new landmark in route.**    
            - Executive must have a valid access token. 
            - Logged-in executive must have `landmark_in_route.create` permission.  
            - Duplicate landmarks in the same route are not allowed.  
            - By default the landmark in route is created in active status.  
        """
    ),
)
async def create_landmark_in_route(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        has_create = verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_ROUTE, raise_exception=False)
        has_update = verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_ROUTE, raise_exception=False)
        if not (has_create or has_update):
            raise exceptions.NoPermission()
                
        route = validate_id(session, Route, form_param.route_id, LandmarkInRoute.route_id)
        landmark_route = createLandmarkInRoute(session, route, form_param)
        session.add(landmark_route)

        is_valid = landmark_in_route(route.id, session)
        if is_valid:
            route.status = RouteStatus.VALID
        else:
            route.status = RouteStatus.INVALID

        session.commit()
        session.refresh(landmark_route)

        landmark_route_data = jsonable_encoder(landmark_route)
        log_event(token, request_info, landmark_route_data)
        return landmark_route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()

