"""
Landmark in Route API Router for EnteBus.

Provides endpoints for managing landmarks in routes, including creation and update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, exceptions, exceptions
from pydantic import BaseModel, Field 
from sqlalchemy.orm import Session
from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    LandmarkInRoute,
    Route,
    Landmark,
    get_db,
    ExecutiveToken,
    OperatorToken,
)
from app.src.validators import (
    validate_id,
    verify_token,
    verify_permission,
    verify_permission,
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
    landmark = session.query(Landmark).filter(Landmark.id == form_param.landmark_id).first()
    if landmark is None:
        raise exceptions.UnknownValue(LandmarkInRoute.landmark_id)
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