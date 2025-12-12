"""
Bus Stop API Router for EnteBus.

Provides endpoints for managing bus stops, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from shapely.geometry import Point
from shapely import wkb, wkt

from app.api.bearer import oauth2_executive
from app.src.db import BusStop, ExecutiveToken, Landmark, SessionLocal
from app.src.permissions.executive import PermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_BUS_STOP
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    validate_wkt_string,
    validate_srid_4326,
)

route_executive = APIRouter()


## Output Schema
class BusStopSchema(BaseModel):
    """Schema for bus stop response."""

    id: int
    name: str
    landmark_id: int
    location: str
    updated_on: datetime | None
    created_on: datetime


## Input Schema
class CreateForm(BaseModel):
    """Form data for creating a new bus stop."""

    name: str = Field(min_length=1, max_length=128, pattern=NAME_PATTERN)
    landmark_id: int = Field()
    location: str = Field(
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        )
    )


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_BUS_STOP,
    tags=["Bus Stop"],
    response_model=BusStopSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
            exceptions.UnknownValue(BusStop.landmark_id),
            exceptions.BusStopOutsideLandmark(),
        ]
    ),
    description=(
        """
            **Create a new bus stop.**  
            - The executive must provide a valid access token.  
            - The authenticated executive must have `landmark.bus_stop.create` permission.  
            - The location field must be a valid WKT string.    
            - The coordinates must be in `longitude/latitude` format.  
            - Use WGS84 compatible coordinates within `SRID 4326` bounds.  
            - The location must be within the boundary of the landmark.     
        """
    ),
)
async def create_bus_stop(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.CREATE_BUS_STOP)

        # Validate WKT and SRID
        location_geom = validate_wkt_string(form_param.location, Point)
        validate_srid_4326(location_geom)
        validated_location = wkt.dumps(location_geom)
        landmark = (
            session.query(Landmark)
            .filter(Landmark.id == form_param.landmark_id)
            .first()
        )
        if landmark is None:
            raise exceptions.UnknownValue(BusStop.landmark_id)

        # Validate the location is within the landmark boundary
        boundary_geom = wkb.loads(bytes(landmark.boundary.data))
        if not boundary_geom.contains(location_geom):
            raise exceptions.BusStopOutsideLandmark()

        bus_stop = BusStop(
            name=form_param.name,
            landmark_id=form_param.landmark_id,
            location=validated_location,
        )
        session.add(bus_stop)
        session.commit()
        session.refresh(bus_stop)

        bus_stop_data = jsonable_encoder(bus_stop, exclude={BusStop.location.name})
        bus_stop_data[BusStop.location.name] = (
            wkb.loads(bytes(bus_stop.location.data))
        ).wkt
        log_event(token, request_info, bus_stop_data)
        return bus_stop_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
