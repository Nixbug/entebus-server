"""
Executive Account API Router for EnteBus.

Provides endpoints for managing landmarks, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from typing import List
from fastapi import APIRouter, Query, status, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from shapely.geometry import Polygon, Point
from sqlalchemy import String, func, or_
from shapely import wkt, wkb
from geoalchemy2 import Geography

from app.api.bearer import oauth2_executive
from app.src.constants import MAX_LANDMARK_AREA, MIN_LANDMARK_AREA
from app.src.db import Landmark, ExecutiveToken, SessionLocal
from app.src.enums import LandmarkType, OrderIn
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
from app.src.urls import URL_LANDMARK
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_name_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_area,
    get_request_info,
    get_executive_roles,
    orm_to_json,
    validate_wkt_string,
    validate_AABB,
    validate_srid_4326,
)

route_executive = APIRouter()
route_vendor = APIRouter()
route_operator = APIRouter()
route_public = APIRouter()


## Output Schema
class LandmarkSchema(BaseModel):
    """Schema for landmark response."""

    id: int
    name: str
    version: int
    alias_names: List[str] | None
    boundary: str
    type: int
    updated_on: datetime | None
    created_on: datetime


## Input Forms
class CreateForm(BaseModel):
    """Form data for creating a new landmark."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    boundary: str = Field(
        description=(
            f"Accepts only SRID 4326 (WGS84)."
            f"valid WKT string representing a `POLYGON`."
            f"Max Area: {MAX_LANDMARK_AREA // 1000000} sq.m, "
            f"Min Area: {MIN_LANDMARK_AREA} sq.m"
        )
    )
    type: LandmarkType = Field(
        description=enum_str(LandmarkType), default=LandmarkType.LOCAL
    )
    alias_names: List[str] | None = Field(max_length=32, default=None)


class UpdateForm:
    pass


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
    type_list: List[LandmarkType] | None = Field(
        Query(default=None, description=enum_str(LandmarkType))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


## Function
def validate_boundary(session: Session, form_param: CreateForm | UpdateForm) -> Polygon:
    """
    Validate and normalize a landmark boundary geometry, this function takes a WKT string representing a polygon and performs
    validation checks on it.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm | UpdateForm): Form instance containing a `boundary` WKT string.

    Returns:
        Polygon: Validated Shapely `Polygon` geometry.

    Raises:
        InvalidBoundaryArea: If the computed area is outside allowed limits.
        OverlappingLandmarkBoundary: If the boundary intersects with an existing landmark.
    """
    # Validate the WKT polygon input string
    boundary_geom = validate_wkt_string(form_param.boundary, Polygon)
    validate_srid_4326(boundary_geom)
    validate_AABB(boundary_geom)

    # Validate the boundary area
    area_in_sq_meters = get_area(boundary_geom)
    if not (MIN_LANDMARK_AREA < area_in_sq_meters < MAX_LANDMARK_AREA):
        raise exceptions.InvalidBoundaryArea()
    # Check for overlapping boundary
    overlapping = session.query(Landmark).filter(
        func.ST_Intersects(
            Landmark.boundary, func.ST_GeomFromText(boundary_geom.wkt, 4326)
        )
    )
    if isinstance(form_param, UpdateForm):
        overlapping = overlapping.filter(Landmark.id != id)
    if overlapping.first():
        raise exceptions.OverlappingLandmarkBoundary()
    form_param.boundary = wkt.dumps(boundary_geom)
    return boundary_geom


def search_landmark(session: Session, query_params: QueryParams) -> List[Landmark]:
    """
    Search for landmarks based on provided query parameters.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Landmark]: List of landmarks that match the search criteria.
    """
    query = session.query(Landmark)
    if query_params.location is not None:
        geometry = validate_wkt_string(query_params.location, Point)
        validate_srid_4326(geometry)
        query_params.location = wkt.dumps(geometry)
    if query_params.type_list is not None:
        query = query.filter(Landmark.type.in_(query_params.type_list))
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

    # Ordering and pagination
    if query_params.order_by == OrderBy.LOCATION:
        if query_params.location is not None:
            ordering_attr = func.ST_Distance(
                Landmark.boundary.cast(Geography),
                func.ST_GeogFromText(query_params.location),
            )
        else:
            ordering_attr = Landmark.boundary
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
    for landmark in landmarks:
        landmark.boundary = wkb.loads(bytes(landmark.boundary.data)).wkt
    return landmarks


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_LANDMARK,
    tags=["Landmark"],
    response_model=LandmarkSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
            exceptions.InvalidAABB(),
            exceptions.InvalidBoundaryArea(),
            exceptions.OverlappingLandmarkBoundary(),
        ]
    ),
    description=(
        f"""
        **Create a new landmark.**       
        - The executive must provide a valid access token.  
        - The authenticated executive must have `landmark.create` permission.        
        - The boundary field must be a valid WKT string.     
        - The coordinates must be in `longitude/latitude` format.       
        - Use WGS84 compatible coordinates within `SRID 4326` bounds.     
        - Form a valid Axis-Aligned Bounding Box (AABB).        
        - The boundary must not intersect or overlap with any existing landmark boundary.     
    """
    ),
)
async def create_landmark(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.CREATE_LANDMARK)

        validate_boundary(session, form_param)
        landmark = Landmark(
            name=form_param.name,
            boundary=form_param.boundary,
            type=form_param.type,
            alias_names=form_param.alias_names,
        )
        session.add(landmark)
        session.commit()
        session.refresh(landmark)

        landmark.boundary = wkb.loads(bytes(landmark.boundary.data)).wkt
        landmark_data, _ = orm_to_json(landmark)
        log_event(token, request_info, landmark_data)
        return landmark_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_LANDMARK,
    tags=["Landmark"],
    response_model=List[LandmarkSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        f"""
            **Fetches a list of landmarks.**    
            - Common search supports searching by id, name and alias_names.  
        """
    ),
)
async def fetch_landmark(query_Params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_landmark(session, query_Params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_LANDMARK,
    tags=["Landmark"],
    response_model=List[LandmarkSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        f"""
            **Fetches a list of landmarks.**    
            - Common search supports searching by id, name and alias_names.  
        """
    ),
)
async def fetch_landmark(query_Params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_landmark(session, query_Params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.get(
    URL_LANDMARK,
    tags=["Landmark"],
    response_model=List[LandmarkSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        f"""
            **Fetches a list of landmarks.**    
            - Common search supports searching by id, name and alias_names.  
        """
    ),
)
async def fetch_landmark(query_Params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_landmark(session, query_Params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_LANDMARK,
    tags=["Landmark"],
    response_model=List[LandmarkSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        f"""
            **Fetches a list of landmarks.**    
            - Common search supports searching by id, name and alias_names.  
        """
    ),
)
async def fetch_landmark(query_Params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_landmark(session, query_Params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
