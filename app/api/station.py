"""
Station API router.

Provides endpoints for managing stations:
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
from shapely import wkt
from sqlalchemy import String, func, or_

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.src.db import (
    Station,
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
from app.src.urls import URL_STATION
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.constants import MAX_STATIONS_PER_LANDMARK
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
    get_by_id,
    get_request_info,
    load_geometry,
    to_WKB,
    update_if_changed,
)

route_executive = APIRouter()
route_vendor = APIRouter()
route_operator = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class StationSchema(BaseModel):
    """Schema for station response."""

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
    """Form data for creating a new station."""

    name: str = Field(min_length=1, max_length=128, pattern=NAME_PATTERN)
    landmark_id: int = Field()
    location: str = Field(
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        )
    )


class UpdateForm(PatchForm):
    """Form data for updating a station."""

    name: str | None = Field(
        min_length=1, max_length=128, pattern=NAME_PATTERN, default=None
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
    """Query parameters for fetching stations."""

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
    Validate a station location geometry. This function takes a WKT string representing a point
    and validates it against the landmark boundary.

    Args:
        session (Session): Active SQLAlchemy database session.
        location_wkt (str): Location in WKT format (Point geometry).
        landmark_id (int): ID of the landmark to validate against.

    Returns:
        Point: Validated Shapely `Point` geometry.

    Raises:
        UnknownValue: If the landmark doesn't exist.
        StationOutsideLandmark: If the location is outside the landmark boundary.
    """
    # Validate WKT and SRID
    location_geom = validate_wkt_string(location_wkt, Point)
    validate_srid_4326(location_geom)

    # Validate if the location is within landmark boundary
    landmark = validate_id(session, Landmark, landmark_id, Station.landmark_id)

    boundary_geom = load_geometry(landmark.boundary)
    if not boundary_geom.contains(location_geom):
        raise exceptions.StationOutsideLandmark()
    return location_geom


def station_to_dict(station: Station) -> dict:
    """
    Convert a Station object to a dictionary representation.

    Args:
        station (Station): The Station object to convert.

    Returns:
        dict: A dictionary representation of the Station object.
    """
    station_data = jsonable_encoder(station, exclude={Station.location.name})
    station_data[Station.location.name] = (load_geometry(station.location)).wkt
    return station_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_station(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new station in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new station.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created station data with location in WKT format.
    """
    station_count = (
        session.query(Station)
        .filter(Station.landmark_id == form_param.landmark_id)
        .count()
    )
    if station_count >= MAX_STATIONS_PER_LANDMARK:
        raise exceptions.LimitExceeded(Station)

    # Validate location (WKT, SRID, and landmark boundary)
    location_geom = validate_location(
        session, form_param.location, form_param.landmark_id
    )
    station = Station(
        name=form_param.name,
        landmark_id=form_param.landmark_id,
        location=to_WKB(location_geom),
    )
    session.add(station)
    session.commit()
    session.refresh(station)

    station_data = station_to_dict(station)
    log_event(token, request_info, station_data)
    return station_data


def update_station(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Update an existing station in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the station to update.
        form_param (UpdateForm): Form data for updating the station.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Updated station data with location in WKT format.
    """
    station = validate_id(session, Station, id, Station.id)

    update_data = form_param.model_dump(exclude_unset=True)
    if "location" in update_data:
        old_location_geom = load_geometry(station.location)
        new_location_geom = validate_location(
            session, update_data["location"], station.landmark_id
        )
        if not new_location_geom.equals(old_location_geom):
            station.location = to_WKB(new_location_geom)
        update_data.pop("location")

    update_if_changed(station, update_data)
    if session.is_modified(station):
        session.commit()
        session.refresh(station)
        station_data = station_to_dict(station)
        log_event(token, request_info, station_data)
    else:
        station_data = station_to_dict(station)
    return station_data


def delete_station(
    session: Session, id: int, token: ExecutiveToken, request_info: schemas.RequestInfo
) -> None:
    """
    Delete a station from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the station to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    station = get_by_id(session, Station, id)
    if station is None:
        return

    station_data = station_to_dict(station)
    session.delete(station)
    session.commit()
    log_event(token, request_info, station_data)


def search_stations(session: Session, query_params: QueryParams) -> list[Station]:
    """
    Search for stations based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve stations that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Station]: List of stations that match the search criteria.
    """
    query = session.query(Station)
    validated_location = None
    if query_params.location is not None:
        geometry = validate_wkt_string(query_params.location, Point)
        validate_srid_4326(geometry)
        validated_location = wkt.dumps(geometry)
    if query_params.landmark_id_list is not None:
        query = query.filter(Station.landmark_id.in_(query_params.landmark_id_list))

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(Station.id.cast(String).ilike(search), Station.name.ilike(search))
        )

    # Generalized filters
    query = apply_id_filters(query, Station, query_params)
    query = apply_created_on_filters(query, Station, query_params)
    query = apply_updated_on_filters(query, Station, query_params)
    query = apply_name_filters(query, Station, query_params)

    # Ordering and pagination
    if query_params.order_by == OrderBy.LOCATION:
        if validated_location is not None:
            ordering_attr = func.ST_Distance(
                Station.location.cast(Geography),
                func.ST_GeogFromText(validated_location),
            )
        else:
            ordering_attr = Station.id
    else:
        ordering_attr = getattr(Station, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    stations = query.all()
    return stations


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.UnknownValue(Station.landmark_id),
    exceptions.StationOutsideLandmark(),
    exceptions.LimitExceeded(Station),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Station.id),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.StationOutsideLandmark(),
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
    .add_head("Creates a new station.")
    .add_line("The location field must be a valid WKT string.")
    .add_line("The coordinates must be in longitude/latitude format.")
    .add_line("Use WGS84 compatible coordinates within SRID 4326 bounds.")
    .add_line("The location must be within the boundary of the landmark.")
    .add_line("Logged-in executive must have `landmark.station.create` permission.")
    .add_line(
        f"A maximum of {MAX_STATIONS_PER_LANDMARK} stations are allowed per landmark."
    )
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing station.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line(
        "When updating the location, it must remain within the landmark boundary."
    )
    .add_line("Logged-in executive must have `landmark.station.update` permission.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing station.")
    .add_line("Returns 204 No Content even if the specified station does not exist.")
    .add_line("Logged-in executive must have `landmark.station.delete` permission.")
)

GET_DESCRIPTION = (
    Description()
    .add_head("Fetches a list of stations.")
    .add_line(
        "If location is not provided while using order_by=location, the API will fall back to default ordering by id."
    )
)


# ---------------------------------------------------------------------------
## Executive
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_STATION,
    summary="Create station",
    tags=["Station"],
    response_model=StationSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_station_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.CREATE_STATION]
        )
        return create_station(session, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_STATION}/{{id}}",
    summary="Update station",
    tags=["Station"],
    response_model=StationSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(PATCH_DESCRIPTION.to_string()),
)
async def update_station_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.UPDATE_STATION]
        )
        return update_station(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_STATION}/{{id}}",
    summary="Delete station",
    tags=["Station"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(DELETE_DESCRIPTION.to_string()),
)
async def delete_station_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.DELETE_STATION]
        )
        delete_station(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_STATION,
    summary="Fetch station",
    tags=["Station"],
    response_model=list[StationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_stations_for_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return [
            station_to_dict(station)
            for station in search_stations(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_STATION,
    summary="Fetch station",
    tags=["Station"],
    response_model=list[StationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_stations_for_vendor(
    query_params: QueryParams = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return [
            station_to_dict(station)
            for station in search_stations(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.get(
    URL_STATION,
    summary="Fetch station",
    tags=["Station"],
    response_model=list[StationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_stations_for_operator(
    query_params: QueryParams = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, OperatorToken, access_token.credentials)
        return [
            station_to_dict(station)
            for station in search_stations(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_STATION,
    summary="Fetch station",
    tags=["Station"],
    response_model=list[StationSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_stations_for_public(
    query_params: QueryParams = Depends(), session: Session = Depends(get_db_session)
):
    try:
        return [
            station_to_dict(station)
            for station in search_stations(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)
