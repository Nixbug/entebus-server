"""
Bus Stop API Router for EnteBus.

Provides endpoints for managing bus stops, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from typing import List
from fastapi import APIRouter, Response, Query, status, Depends
from fastapi.encoders import jsonable_encoder
from geoalchemy2 import Geography
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session
from shapely.geometry import Point
from shapely import wkb, wkt
from sqlalchemy import String, func, or_

from app.api.bearer import oauth2_executive
from app.src.db import BusStop, ExecutiveToken, Landmark, SessionLocal
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    NameFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.permissions.executive import PermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_BUS_STOP
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_name_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    update_if_changed,
    validate_wkt_string,
    validate_srid_4326,
)

route_executive = APIRouter()
route_vendor = APIRouter()
route_operator = APIRouter()
route_public = APIRouter()


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


class UpdateForm(BaseModel):
    """Form data for updating a bus stop."""

    name: str = Field(min_length=1, max_length=128, pattern=NAME_PATTERN, default=None)
    location: str = Field(
        default=None,
        description=(
            "Accepts only SRID 4326 (WGS84), and a valid WKT string representing a `POINT`."
        ),
    )


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    LOCATION = "location"


class QueryParams(
    UpdatedOnFilter,
    CreatedOnFilter,
    NameFilter,
    IDFilter,
    PaginationFilter,
):
    """Query parameters for fetching bus stops."""

    search: str | None = Field(Query(default=None))
    location: str | None = Field(
        Query(
            default=None,
            description=(
                "Accepts only SRID 4326 (WGS84) and a valid WKT string representing a `POINT`."
            ),
        )
    )
    landmark_id_list: List[int] | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


# Function
def validate_location(session: Session, location_wkt: str, landmark_id: int) -> Point:
    """
    Validate a bus stop location geometry. This function takes a WKT string representing a point
    and validates it against the landmark boundary.

    Args:
        session (Session): Active SQLAlchemy database session.
        location_wkt (str): Location in WKT format (Point geometry).
        landmark_id (int): ID of the landmark to validate against.

    Returns:
        Point: Validated Shapely `Point` geometry.

    Raises:
        UnknownValue: If the landmark doesn't exist.
        BusStopOutsideLandmark: If the location is outside the landmark boundary.
    """
    # Validate WKT and SRID
    location_geom = validate_wkt_string(location_wkt, Point)
    validate_srid_4326(location_geom)

    # Validate location is within landmark boundary
    landmark = session.query(Landmark).filter(Landmark.id == landmark_id).first()
    if landmark is None:
        raise exceptions.UnknownValue(BusStop.landmark_id)

    boundary_geom = wkb.loads(bytes(landmark.boundary.data))
    if not boundary_geom.contains(location_geom):
        raise exceptions.BusStopOutsideLandmark()

    return location_geom


def search_bus_stops(session: Session, query_params: QueryParams) -> List[BusStop]:
    """
    Search for bus stops based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve bus stops that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[BusStop]: List of bus stops that match the search criteria.
    """
    query = session.query(BusStop)
    validated_location = None
    if query_params.location is not None:
        geometry = validate_wkt_string(query_params.location, Point)
        validate_srid_4326(geometry)
        validated_location = wkt.dumps(geometry)
    if query_params.landmark_id_list is not None:
        query = query.filter(BusStop.landmark_id.in_(query_params.landmark_id_list))

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(BusStop.id.cast(String).ilike(search), BusStop.name.ilike(search))
        )

    # Generalized filters
    query = apply_id_filters(query, BusStop, query_params)
    query = apply_created_on_filters(query, BusStop, query_params)
    query = apply_updated_on_filters(query, BusStop, query_params)
    query = apply_name_filters(query, BusStop, query_params)

    # Ordering and pagination
    if query_params.order_by == OrderBy.LOCATION:
        if validated_location is not None:
            ordering_attr = func.ST_Distance(
                BusStop.location.cast(Geography),
                func.ST_GeogFromText(validated_location),
            )
        else:
            ordering_attr = BusStop.id
    else:
        ordering_attr = getattr(BusStop, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    query = query.with_entities(
        BusStop, func.ST_AsText(BusStop.location).label("location_wkt")
    )
    results = query.all()
    bus_stops = []
    for bus_stop_obj, location_wkt in results:
        bus_stop_obj.location = location_wkt
        bus_stops.append(bus_stop_obj)

    return bus_stops


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

        # Validate location (WKT, SRID, and landmark boundary)
        location_geom = validate_location(
            session, form_param.location, form_param.landmark_id
        )
        validated_location = wkt.dumps(location_geom)

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


@route_executive.patch(
    f"{URL_BUS_STOP}/{{id}}",
    tags=["Bus Stop"],
    response_model=BusStopSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(BusStop.id),
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
            exceptions.BusStopOutsideLandmark(),
        ]
    ),
    description=(
        """
            **Updates an existing bus stop.**    
            - Requires a valid access token.    
            - Logged-in executive must have `landmark.bus_stop.update` permission to update bus stops.    
            - Empty PATCH requests are allowed and will result in no changes.    
            - When updating the `location`, it must remain within the landmark boundary.    
        """
    ),
)
async def update_bus_stop(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.UPDATE_BUS_STOP)

        bus_stop = session.query(BusStop).filter(BusStop.id == id).first()
        if bus_stop is None:
            raise exceptions.UnknownValue(BusStop.id)

        update_data = form_param.model_dump(exclude_unset=True)
        if form_param.location is not None:
            # Validate location if changed
            new_geom = validate_location(
                session, form_param.location, bus_stop.landmark_id
            )
            old_geom = wkb.loads(bytes(bus_stop.location.data))

            if new_geom.wkt != old_geom.wkt:
                bus_stop.location = wkt.dumps(new_geom)
            update_data.pop("location")

        update_if_changed(bus_stop, update_data)
        have_updates = session.is_modified(bus_stop)
        if have_updates:
            session.commit()
            session.refresh(bus_stop)

        bus_stop_data = jsonable_encoder(bus_stop, exclude={BusStop.location.name})
        bus_stop_data[BusStop.location.name] = (
            wkb.loads(bytes(bus_stop.location.data))
        ).wkt
        if have_updates:
            log_event(token, request_info, bus_stop_data)
        return bus_stop_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_BUS_STOP}/{{id}}",
    tags=["Bus Stop"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
        **Deletes an existing bus stop.**  
        - Requires a valid access token for authentication.  
        - The logged-in executive must have `landmark.bus_stop.delete` permission.  
        - Returns 204 No Content even if the specified bus stop does not exist.  
        """
    ),
)
async def delete_bus_stop(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.DELETE_BUS_STOP)

        bus_stop = session.query(BusStop).filter(BusStop.id == id).first()
        if bus_stop is not None:
            bus_stop_data = jsonable_encoder(bus_stop, exclude={BusStop.location.name})
            bus_stop_data[BusStop.location.name] = wkb.loads(
                bytes(bus_stop.location.data)
            ).wkt
            session.delete(bus_stop)
            session.commit()
            log_event(token, request_info, bus_stop_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_BUS_STOP,
    tags=["Bus Stop"],
    response_model=List[BusStopSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        """
            **Fetches a list of Bus Stops.**    
            - Common search supports searching by id and name.  
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_bus_stop_executive(query_params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_bus_stops(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_BUS_STOP,
    tags=["Bus Stop"],
    response_model=List[BusStopSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        """
            **Fetches a list of Bus Stops.**    
            - Common search supports searching by id and name.  
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_bus_stop_vendor(query_params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_bus_stops(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.get(
    URL_BUS_STOP,
    tags=["Bus Stop"],
    response_model=List[BusStopSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        """
            **Fetches a list of Bus Stops.**    
            - Common search supports searching by id and name.  
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_bus_stop_operator(query_params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_bus_stops(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_BUS_STOP,
    tags=["Bus Stop"],
    response_model=List[BusStopSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        """
            **Fetches a list of Bus Stops.**    
            - Common search supports searching by id and name.  
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_bus_stop_public(query_params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_bus_stops(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
