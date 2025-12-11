"""
Landmark API Router for EnteBus.

Provides endpoints for managing landmarks, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, List
from fastapi import APIRouter, Response, Query, status, Depends
from fastapi.encoders import jsonable_encoder
from geoalchemy2 import Geography
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm.session import Session
from shapely.geometry import Polygon, Point
from sqlalchemy import String, func, or_
from shapely import wkb, wkt

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
AliasName = Annotated[str, StringConstraints(max_length=32)]


class CreateForm(BaseModel):
    """Form data for creating a new landmark."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    boundary: str = Field(
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POLYGON`, "
            f"Max Area: {MAX_LANDMARK_AREA // 1000000} km², "
            f"Min Area: {MIN_LANDMARK_AREA} sq.m"
        )
    )
    type: LandmarkType = Field(
        description=enum_str(LandmarkType), default=LandmarkType.LOCAL
    )
    alias_names: List[AliasName] | None = Field(max_items=32, default=None)


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


def search_landmark(session: Session, query_params: QueryParams) -> List[Landmark]:
    """
    Search for landmarks based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve landmarks that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Landmark]: List of landmarks that match the search criteria.
    """
    query = session.query(Landmark)
    validated_location = None
    if query_params.location is not None:
        geometry = validate_wkt_string(query_params.location, Point)
        validate_srid_4326(geometry)
        validated_location = wkt.dumps(geometry)
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

    query = query.with_entities(
        Landmark, func.ST_AsText(Landmark.boundary).label("boundary_wkt")
    )
    results = query.all()
    landmarks = []
    for landmark_obj, boundary_wkt in results:
        setattr(landmark_obj, Landmark.boundary.name, boundary_wkt)
        landmarks.append(landmark_obj)
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

        validate_boundary(session, form_param.boundary)
        landmark = Landmark(
            name=form_param.name,
            boundary=form_param.boundary,
            type=form_param.type,
            alias_names=form_param.alias_names,
        )
        session.add(landmark)
        session.commit()
        session.refresh(landmark)

        landmark_data = jsonable_encoder(landmark, exclude={Landmark.boundary.name})
        landmark_data[Landmark.boundary.name] = wkb.loads(
            bytes(landmark.boundary.data)
        ).wkt
        log_event(token, request_info, landmark_data)
        return landmark_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_LANDMARK}/{{id}}",
    tags=["Landmark"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        f"""
            **Deletes an existing landmark.**   
            - Requires a valid access token for authentication.         
            - The logged-in executive must have the `landmark.delete` permission.       
            - Returns 204 No Content even if the specified landmark does not exist.         
            - A foreign key constraint error will occur if the landmark is referenced in any other table.    
        """
    ),
)
async def delete_landmark(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.DELETE_LANDMARK)

        landmark = session.query(Landmark).filter(Landmark.id == id).first()
        if landmark is not None:
            landmark_data = jsonable_encoder(landmark, exclude={Landmark.boundary.name})
            landmark_data[Landmark.boundary.name] = wkb.loads(
                bytes(landmark.boundary.data)
            ).wkt
            session.delete(landmark)
            session.commit()
            log_event(token, request_info, landmark_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
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
        """
            **Fetches a list of landmarks.**    
            - Common search supports searching by id, name and alias_names.      
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_landmark(query_params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_landmark(session, query_params)
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
        """
            **Fetches a list of landmarks.**    
            - Common search supports searching by id, name and alias_names.     
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_landmark(query_params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_landmark(session, query_params)
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
        """
            **Fetches a list of landmarks.**    
            - Common search supports searching by id, name and alias_names.     
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_landmark(query_params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_landmark(session, query_params)
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
        """
            **Fetches a list of landmarks.**    
            - Common search supports searching by id, name and alias_names.    
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_landmark(query_params: QueryParams = Depends()):
    try:
        session = SessionLocal()

        return search_landmark(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
