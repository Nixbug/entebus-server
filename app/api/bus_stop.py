"""
Bus Stop API router.

Provides endpoints for managing bus stops:
    - POST (executive)
    - PATCH (executive)
    - DELETE (executive)
    - GET (executive, vendor, operator, public)
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Response, Query, status, Depends
from fastapi.encoders import jsonable_encoder
from geoalchemy2 import Geography
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session
from shapely.geometry import Point
from shapely import wkb, wkt
from sqlalchemy import String, func, or_

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.src.db import (
    BusStop,
    ExecutiveToken,
    Landmark,
    OperatorToken,
    VendorToken,
    get_db_session,
)
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    NameFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.permissions.executive import PermissionPath
from app.src import exceptions, schemas
from app.src.schemas import PatchForm
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_BUS_STOP
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.constants import MAX_BUS_STOPS_PER_LANDMARK
from app.src.validators import (
    verify_token,
    validate_id,
    validate_wkt_string,
    validate_srid_4326,
    authorize_executive,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_name_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    load_geometry,
    update_if_changed,
)

route_executive = APIRouter()
route_vendor = APIRouter()
route_operator = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class BusStopSchema(BaseModel):
    """Schema for bus stop response."""

    id: int
    name: str
    landmark_id: int
    location: str
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """Form data for creating a new bus stop."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    landmark_id: int = Field()
    location: str = Field(
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        )
    )


class UpdateForm(PatchForm):
    """Form data for updating a bus stop."""

    name: str | None = Field(
        min_length=1, max_length=32, pattern=NAME_PATTERN, default=None
    )
    location: str | None = Field(
        default=None,
        description=(
            "Accepts only SRID 4326 (WGS84), and a valid WKT string representing a `POINT`."
        ),
    )


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
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
    landmark_id_list: list[int] | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
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

    # Validate if the location is within landmark boundary
    landmark = validate_id(session, Landmark, landmark_id, BusStop.landmark_id)

    boundary_geom = load_geometry(landmark.boundary)
    if not boundary_geom.contains(location_geom):
        raise exceptions.BusStopOutsideLandmark()
    return location_geom


def bus_stop_to_dict(bus_stop: BusStop) -> dict:
    """
    Convert a BusStop object to a dictionary representation.

    Args:
        bus_stop (BusStop): The BusStop object to convert.

    Returns:
        dict: A dictionary representation of the BusStop object.
    """
    bus_stop_data = jsonable_encoder(bus_stop, exclude={BusStop.location.name})
    bus_stop_data[BusStop.location.name] = (load_geometry(bus_stop.location)).wkt
    return bus_stop_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_bus_stop(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new bus stop in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new bus stop.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created bus stop data with location in WKT format.
    """
    bus_stop_count = (
        session.query(BusStop)
        .filter(BusStop.landmark_id == form_param.landmark_id)
        .count()
    )
    if bus_stop_count >= MAX_BUS_STOPS_PER_LANDMARK:
        raise exceptions.LimitExceeded(BusStop)

    # Validate location (WKT, SRID, and landmark boundary)
    location_geom = validate_location(
        session, form_param.location, form_param.landmark_id
    )
    location = wkt.dumps(location_geom)

    bus_stop = BusStop(
        name=form_param.name,
        landmark_id=form_param.landmark_id,
        location=location,
    )
    session.add(bus_stop)
    session.commit()
    session.refresh(bus_stop)

    bus_stop_data = bus_stop_to_dict(bus_stop)
    log_event(token, request_info, bus_stop_data)
    return bus_stop_data


def update_bus_stop(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Update an existing bus stop in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the bus stop to update.
        form_param (UpdateForm): Form data for updating the bus stop.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Updated bus stop data with location in WKT format.
    """
    bus_stop = validate_id(session, BusStop, id, BusStop.id)

    update_data = form_param.model_dump(exclude_unset=True)
    if "location" in update_data:
        old_location_geom = load_geometry(bus_stop.location)
        new_location_geom = validate_location(
            session, update_data["location"], bus_stop.landmark_id
        )
        if not new_location_geom.equals(old_location_geom):
            bus_stop.location = wkt.dumps(new_location_geom)
        update_data.pop("location")

    update_if_changed(bus_stop, update_data)
    if session.is_modified(bus_stop):
        session.commit()
        session.refresh(bus_stop)
        bus_stop_data = bus_stop_to_dict(bus_stop)
        log_event(token, request_info, bus_stop_data)
    else:
        bus_stop_data = bus_stop_to_dict(bus_stop)
    return bus_stop_data


def delete_bus_stop(
    session: Session, id: int, token: ExecutiveToken, request_info: schemas.RequestInfo
):
    """
    Delete a bus stop from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the bus stop to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    bus_stop = session.query(BusStop).filter(BusStop.id == id).first()
    if bus_stop is None:
        return

    bus_stop_data = bus_stop_to_dict(bus_stop)
    session.delete(bus_stop)
    session.commit()
    log_event(token, request_info, bus_stop_data)


def search_bus_stops(session: Session, query_params: QueryParams) -> list[BusStop]:
    """
    Search for bus stops based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve bus stops that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[BusStop]: List of bus stops that match the search criteria.
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

    bus_stops = query.all()
    return bus_stops


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.UnknownValue(BusStop.landmark_id),
    exceptions.BusStopOutsideLandmark(),
    exceptions.LimitExceeded(BusStop),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(BusStop.id),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.BusStopOutsideLandmark(),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new bus stop.")
    .add_line("The location field must be a valid WKT string.")
    .add_line("The coordinates must be in longitude/latitude format.")
    .add_line("Use WGS84 compatible coordinates within SRID 4326 bounds.")
    .add_line("The location must be within the boundary of the landmark.")
    .add_line("Logged-in executive must have `landmark.bus_stop.create` permission.")
    .add_line(
        f"A maximum of {MAX_BUS_STOPS_PER_LANDMARK} bus stops are allowed per landmark."
    )
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing bus stop.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line(
        "When updating the location, it must remain within the landmark boundary."
    )
    .add_line("Logged-in executive must have `landmark.bus_stop.update` permission.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing bus stop.")
    .add_line("Returns 204 No Content even if the specified bus stop does not exist.")
    .add_line("Logged-in executive must have `landmark.bus_stop.delete` permission.")
)

GET_DESCRIPTION = (
    Description()
    .add_head("Fetches a list of bus stops.")
    .add_line(
        "If location is not provided while using order_by=location, the API will fall back to default ordering by id."
    )
)


# ---------------------------------------------------------------------------
## Executive
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_BUS_STOP,
    summary="Create bus stop",
    tags=["Bus Stop"],
    response_model=BusStopSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_bus_stop_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.CREATE_BUS_STOP]
        )
        return create_bus_stop(session, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_BUS_STOP}/{{id}}",
    summary="Update bus stop",
    tags=["Bus Stop"],
    response_model=BusStopSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(PATCH_DESCRIPTION.to_string()),
)
async def update_bus_stop_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.UPDATE_BUS_STOP]
        )
        return update_bus_stop(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_BUS_STOP}/{{id}}",
    summary="Delete bus stop",
    tags=["Bus Stop"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(DELETE_DESCRIPTION.to_string()),
)
async def delete_bus_stop_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.DELETE_BUS_STOP]
        )
        delete_bus_stop(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_BUS_STOP,
    summary="Fetch bus stop",
    tags=["Bus Stop"],
    response_model=list[BusStopSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_bus_stops_for_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return [
            bus_stop_to_dict(bus_stop)
            for bus_stop in search_bus_stops(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_BUS_STOP,
    summary="Fetch bus stop",
    tags=["Bus Stop"],
    response_model=list[BusStopSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_bus_stops_for_vendor(
    query_params: QueryParams = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return [
            bus_stop_to_dict(bus_stop)
            for bus_stop in search_bus_stops(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.get(
    URL_BUS_STOP,
    summary="Fetch bus stop",
    tags=["Bus Stop"],
    response_model=list[BusStopSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_bus_stops_for_operator(
    query_params: QueryParams = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, OperatorToken, access_token.credentials)
        return [
            bus_stop_to_dict(bus_stop)
            for bus_stop in search_bus_stops(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_BUS_STOP,
    summary="Fetch bus stop",
    tags=["Bus Stop"],
    response_model=list[BusStopSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_bus_stops_for_public(
    query_params: QueryParams = Depends(), session: Session = Depends(get_db_session)
):
    try:
        return [
            bus_stop_to_dict(bus_stop)
            for bus_stop in search_bus_stops(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)
