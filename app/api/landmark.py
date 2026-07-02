"""
Landmark API router.

Provides endpoints for managing landmarks:
    - POST (executive)
    - PATCH (executive)
    - DELETE (executive)
    - GET (executive, vendor, operator, public)
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from geoalchemy2 import Geography
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm.session import Session
from shapely.geometry import Polygon, Point
from sqlalchemy import String, func, or_
from shapely import wkt
from shapely.ops import transform
from geoalchemy2.shape import from_shape
import pyproj

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.src.constants import (
    MAX_LANDMARK_AREA,
    MAX_LANDMARK_UPDATE_DISTANCE,
    MIN_LANDMARK_AREA,
)
from app.src.db import (
    BusStop,
    Landmark,
    ExecutiveToken,
    VendorToken,
    OperatorToken,
    get_db_session,
)
from app.src.enums import LandmarkType, OrderIn
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
from app.src.urls import URL_LANDMARK
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.validators import (
    verify_token,
    validate_id,
    validate_wkt_string,
    validate_AABB,
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
    get_area,
    get_request_info,
    load_geometry,
    to_WKB,
    update_if_changed,
    apply_type_filters,
)

route_executive = APIRouter()
route_vendor = APIRouter()
route_operator = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class LandmarkSchema(BaseModel):
    """Schema for landmark response."""

    id: int
    name: str
    version: int
    alias_names: list[str] | None
    boundary: str
    type: int
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
landmark_boundary_description = (
    f"Accepts only SRID 4326 (WGS84), "
    f"valid WKT string representing a `POLYGON`, "
    f"Max Area: {MAX_LANDMARK_AREA // 1000000} km², "
    f"Min Area: {MIN_LANDMARK_AREA} sq.m"
)

AliasName = Annotated[str, StringConstraints(max_length=32)]


class CreateForm(BaseModel):
    """Form data for creating a new landmark."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    boundary: str = Field(description=landmark_boundary_description)
    type: LandmarkType = Field(
        description=enum_str(LandmarkType), default=LandmarkType.LOCAL
    )
    alias_names: list[AliasName] | None = Field(max_length=32, default=None)


class UpdateForm(PatchForm):
    """Form data for updating an existing landmark."""

    name: str | None = Field(
        min_length=1, max_length=32, pattern=NAME_PATTERN, default=None
    )
    boundary: str | None = Field(
        default=None, description=landmark_boundary_description
    )
    type: LandmarkType | None = Field(description=enum_str(LandmarkType), default=None)
    alias_names: list[AliasName] | None = Field(max_length=32, default=None)


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
    """Query parameters for fetching landmarks."""

    search: str | None = Field(Query(default=None))
    location: str | None = Field(
        Query(
            default=None,
            description=(
                "Accepts only SRID 4326 (WGS84) and a valid WKT string representing a `POINT`."
            ),
        )
    )
    alias_names: str | None = Field(Query(default=None))
    type_list: list[LandmarkType] | None = Field(
        Query(default=None, description=enum_str(LandmarkType))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
def validate_boundary(
    session: Session, boundary_wkt: str, landmark_id: int | None = None
) -> Polygon:
    """
    Validate a landmark boundary geometry. This function takes a WKT string representing a polygon and performs
    validation checks on it.

    Args:
        session (Session): Active SQLAlchemy database session.
        boundary_wkt (str): Boundary in WKT format.
        landmark_id (int | None):
            - Pass `None` when creating a landmark.
            - Pass the existing landmark's ID when updating, so its own boundary is ignored during overlap checks.

    Returns:
        Polygon: Validated Shapely `Polygon` geometry.

    Raises:
        InvalidBoundaryArea: If the computed area is outside allowed limits.
    """
    # Validate WKT and SRID and AABB
    boundary_geom = validate_wkt_string(boundary_wkt, Polygon)
    validate_srid_4326(boundary_geom)
    validate_AABB(boundary_geom)

    # Validate the boundary area
    area_in_sq_meters = get_area(boundary_geom)
    if not (MIN_LANDMARK_AREA <= area_in_sq_meters <= MAX_LANDMARK_AREA):
        raise exceptions.InvalidBoundaryArea()

    # Check for overlaps with other landmarks
    overlapping = session.query(Landmark).filter(
        func.ST_Intersects(
            Landmark.boundary, func.ST_GeomFromText(boundary_geom.wkt, 4326)
        )
    )

    # If updating, exclude the current landmark from overlap check
    if landmark_id is not None:
        overlapping = overlapping.filter(Landmark.id != landmark_id)
    if overlapping.first():
        raise exceptions.OverlappingLandmarkBoundary()
    return boundary_geom


def landmark_to_dict(landmark: Landmark) -> dict:
    """
    Convert a Landmark object to a dictionary representation.

    Args:
        landmark (Landmark): The Landmark object to convert.

    Returns:
        dict: A dictionary representation of the Landmark object.
    """
    landmark_data = jsonable_encoder(landmark, exclude={Landmark.boundary.name})
    landmark_data[Landmark.boundary.name] = load_geometry(landmark.boundary).wkt
    return landmark_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_landmark(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new landmark in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a landmark.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created landmark data with boundary in WKT format.
    """
    boundary_geom = validate_boundary(session, form_param.boundary)
    landmark = Landmark(
        name=form_param.name,
        boundary=to_WKB(boundary_geom),
        type=form_param.type,
        alias_names=form_param.alias_names,
    )
    session.add(landmark)
    session.commit()
    session.refresh(landmark)

    landmark_data = landmark_to_dict(landmark)
    log_event(token, request_info, landmark_data)
    return landmark_data


def update_landmark(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Update an existing landmark in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the landmark to update.
        form_param (UpdateForm): Form data for updating the landmark.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Updated landmark data with boundary in WKT format.
    """
    landmark = validate_id(session, Landmark, id, Landmark.id)

    update_data = form_param.model_dump(exclude_unset=True)
    if "boundary" in update_data:
        new_boundary_geom = validate_boundary(session, update_data["boundary"], id)
        old_boundary_geom = load_geometry(landmark.boundary)
        if not new_boundary_geom.equals(old_boundary_geom):
            projection = pyproj.Transformer.from_crs(
                "EPSG:4326", "EPSG:3857", always_xy=True
            ).transform

            old_proj = transform(projection, old_boundary_geom)
            new_proj = transform(projection, new_boundary_geom)
            distance_in_meters = old_proj.centroid.distance(new_proj.centroid)
            if distance_in_meters > MAX_LANDMARK_UPDATE_DISTANCE:
                raise exceptions.LandmarkDistanceLimitExceeded()

            bus_stops = session.query(BusStop).filter(BusStop.landmark_id == id).all()
            for bus_stop in bus_stops:
                bus_stop_geom = load_geometry(bus_stop.location)
                if not bus_stop_geom.within(new_boundary_geom):
                    raise exceptions.BusStopOutsideLandmark()
            landmark.boundary = to_WKB(new_boundary_geom)
        update_data.pop("boundary")

    update_if_changed(landmark, update_data)
    if session.is_modified(landmark):
        landmark.version += 1
        session.commit()
        session.refresh(landmark)
        landmark_data = landmark_to_dict(landmark)
        log_event(token, request_info, landmark_data)
    else:
        landmark_data = landmark_to_dict(landmark)
    return landmark_data


def delete_landmark(
    session: Session, id: int, token: ExecutiveToken, request_info: schemas.RequestInfo
) -> None:
    """
    Delete a landmark from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the landmark to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    landmark = session.query(Landmark).filter(Landmark.id == id).first()
    if landmark is None:
        return

    landmark_data = landmark_to_dict(landmark)
    session.delete(landmark)
    session.commit()
    log_event(token, request_info, landmark_data)


def search_landmarks(session: Session, query_params: QueryParams) -> list[Landmark]:
    """
    Search for landmarks based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve landmarks that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Landmark]: List of landmarks that match the search criteria.
    """
    query = session.query(Landmark)
    validated_location = None
    if query_params.location is not None:
        geometry = validate_wkt_string(query_params.location, Point)
        validate_srid_4326(geometry)
        validated_location = wkt.dumps(geometry)
    if query_params.alias_names is not None:
        query = query.filter(
            func.array_to_string(Landmark.alias_names, ",").ilike(
                f"%{query_params.alias_names}%"
            )
        )

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Landmark.id.cast(String).ilike(search),
                Landmark.name.ilike(search),
                func.array_to_string(Landmark.alias_names, ",").ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Landmark, query_params)
    query = apply_created_on_filters(query, Landmark, query_params)
    query = apply_updated_on_filters(query, Landmark, query_params)
    query = apply_name_filters(query, Landmark, query_params)
    query = apply_type_filters(query, Landmark, query_params)

    # Ordering and pagination
    if query_params.order_by == OrderBy.LOCATION:
        if validated_location is not None:
            ordering_attr = func.ST_Distance(
                Landmark.boundary.cast(Geography),
                func.ST_GeogFromText(validated_location),
            )
        else:
            ordering_attr = Landmark.id
    else:
        ordering_attr = getattr(Landmark, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    landmarks = query.all()
    return landmarks


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.InvalidAABB(),
    exceptions.InvalidBoundaryArea(),
    exceptions.OverlappingLandmarkBoundary(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Landmark.id),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.InvalidAABB(),
    exceptions.InvalidBoundaryArea(),
    exceptions.BusStopOutsideLandmark(),
    exceptions.OverlappingLandmarkBoundary(),
    exceptions.LandmarkDistanceLimitExceeded(),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new landmark.")
    .add_line("The boundary field must be a valid WKT string.")
    .add_line("The coordinates must be in longitude/latitude format.")
    .add_line("Use WGS84 compatible coordinates within SRID 4326 bounds.")
    .add_line("Forms a valid Axis-Aligned Bounding Box (AABB).")
    .add_line(
        "The boundary must not intersect or overlap with any existing landmark boundary."
    )
    .add_line("Logged-in executive must have the `landmark.create` permission.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing landmark.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line(
        f"When updating the boundary, the new centroid cannot be more than {MAX_LANDMARK_UPDATE_DISTANCE / 1000} km from the original centroid."
    )
    .add_line(
        "All bus stops associated with the landmark must remain within the updated boundary."
    )
    .add_line("Logged-in executive must have the `landmark.update` permission.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing landmark.")
    .add_line("Returns 204 No Content even if the specified landmark does not exist.")
    .add_line(
        "A foreign key constraint error will occur if the landmark is referenced in any other table."
    )
    .add_line("Logged-in executive must have the `landmark.delete` permission.")
)

GET_DESCRIPTION = (
    Description()
    .add_head("Fetches a list of landmarks.")
    .add_line(
        "If location is not provided while using order_by=location, the API will fall back to default ordering by id."
    )
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_LANDMARK,
    summary="Create landmark",
    tags=["Landmark"],
    response_model=LandmarkSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_landmark_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.CREATE_LANDMARK]
        )
        return create_landmark(session, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_LANDMARK}/{{id}}",
    summary="Update landmark",
    tags=["Landmark"],
    response_model=LandmarkSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(PATCH_DESCRIPTION.to_string()),
)
async def update_landmark_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.UPDATE_LANDMARK]
        )
        return update_landmark(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_LANDMARK}/{{id}}",
    summary="Delete landmark",
    tags=["Landmark"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(DELETE_DESCRIPTION.to_string()),
)
async def delete_landmark_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.DELETE_LANDMARK]
        )
        delete_landmark(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_LANDMARK,
    summary="Fetch landmark",
    tags=["Landmark"],
    response_model=list[LandmarkSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_landmarks_for_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return [
            landmark_to_dict(landmark)
            for landmark in search_landmarks(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_LANDMARK,
    summary="Fetch landmark",
    tags=["Landmark"],
    response_model=list[LandmarkSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_landmarks_for_vendor(
    query_params: QueryParams = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return [
            landmark_to_dict(landmark)
            for landmark in search_landmarks(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.get(
    URL_LANDMARK,
    summary="Fetch landmark",
    tags=["Landmark"],
    response_model=list[LandmarkSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_landmarks_for_operator(
    query_params: QueryParams = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, OperatorToken, access_token.credentials)
        return [
            landmark_to_dict(landmark)
            for landmark in search_landmarks(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_LANDMARK,
    summary="Fetch landmark",
    tags=["Landmark"],
    response_model=list[LandmarkSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_landmarks_for_public(
    query_params: QueryParams = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        return [
            landmark_to_dict(landmark)
            for landmark in search_landmarks(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)
