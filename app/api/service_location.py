"""
Service Location API Router.

Provides endpoints for managing service locations:
    - PATCH (operator)
    - GET (executive, operator, vendor, public)
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Depends, Query
from geoalchemy2 import Geography
from sqlalchemy import ColumnElement, func
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from shapely.geometry import Point
from shapely import wkt
from fastapi.encoders import jsonable_encoder

from app.src.db import (
    ExecutiveToken,
    OperatorToken,
    ServiceLocation,
    VendorToken,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import OrderIn
from app.src.schemas import PatchForm
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, UpdatedOnFilter
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.urls import URL_SERVICE_TRACE
from app.src.validators import (
    validate_id,
    authorize_operator,
    validate_srid_4326,
    validate_wkt_string,
    verify_token,
)
from app.api.bearer import bearer_operator, oauth2_executive, bearer_vendor
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    load_geometry,
    to_WKB,
)
from app.src import exceptions

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class ServiceLocationSchema(BaseModel):
    """Schema for service location response."""

    id: int
    company_id: int
    service_id: int
    landmark_id: int
    location: str | None
    accuracy: float | None
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class UpdateForm(PatchForm):
    """Form data for updating an existing service location."""

    location: str | None = Field(default=None)
    accuracy: float | None = Field(default=None, ge=0, le=100)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    LOCATION = "location"


class QueryParamsForPU(UpdatedOnFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for public users."""

    service_id: int | None = Field(Query(default=None))
    location: str | None = Field(
        Query(
            default=None,
            description=(
                "Accepts only SRID 4326 (WGS84) and a valid WKT string representing a `POINT`."
            ),
        )
    )
    landmark_id: int | None = Field(Query(default=None))
    accuracy_ge: float | None = Field(Query(default=None))
    accuracy_le: float | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForOP(QueryParamsForPU):
    """Query parameters for operators."""

    pass


class QueryParamsForEX(QueryParamsForPU):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParamsForVE(QueryParamsForEX):
    """Query parameters for vendors."""

    pass


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
def validate_location(location_wkt: str) -> Point:
    """
    Validate a WKT string as a Point geometry with SRID 4326.

    Args:
        location_wkt (str): Location in WKT format (Point geometry).

    Returns:
        Point: Validated Shapely `Point` geometry.
    """
    # Validate WKT and SRID
    location_geom = validate_wkt_string(location_wkt, Point)
    validate_srid_4326(location_geom)
    return location_geom


def service_location_to_dict(service_location: ServiceLocation) -> dict:
    """
    Convert a ServiceLocation SQLAlchemy model instance to a dictionary with WKT location in WKT format.

    Args:
        service_location (ServiceLocation): ServiceLocation model instance.

    Returns:
        dict: Dictionary representation of the service location with location in WKT format.
    """
    service_location_data = jsonable_encoder(
        service_location,
        exclude={ServiceLocation.location.name},
    )
    location_geom = (
        load_geometry(service_location.location)
        if service_location.location is not None
        else None
    )
    service_location_data[ServiceLocation.location.name] = (
        location_geom.wkt if location_geom is not None else None
    )
    return service_location_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def update_service_location(
    session: Session,
    id: int,
    form_param: UpdateForm,
    service_location_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Updates an existing service location record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the service location to update.
        form_param (UpdateForm): Form data for updating the service location.
        service_location_filter (optional): Additional filter to apply when validating the service location ID.

    Returns:
        dict: The updated service location data.
    """
    service_location = validate_id(
        session,
        ServiceLocation,
        id,
        ServiceLocation.id,
        extra_filter=service_location_filter,
    )

    update_data = form_param.model_dump(exclude_unset=True)
    if "location" in update_data and update_data["location"] is not None:
        new_location_geom = validate_location(update_data["location"])
        if service_location.location is not None:
            old_location_geom = load_geometry(service_location.location)
            if not new_location_geom.equals(old_location_geom):
                service_location.location = to_WKB(new_location_geom)
        else:
            service_location.location = to_WKB(new_location_geom)
        update_data.pop("location")

        if "accuracy" in update_data and update_data["accuracy"] is not None:
            if update_data["accuracy"] != service_location.accuracy:
                service_location.accuracy = update_data["accuracy"]
            update_data.pop("accuracy")

    if session.is_modified(service_location):
        session.commit()
        session.refresh(service_location)
    service_location_data = service_location_to_dict(service_location)
    return service_location_data


def search_service_locations(
    session: Session, query_params: QueryParams
) -> list[ServiceLocation]:
    """
    Search for service locations based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve service locations that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[ServiceLocation]: List of service locations that match the search criteria.
    """
    query = session.query(ServiceLocation)
    validated_location = None
    if query_params.location is not None:
        geometry = validate_wkt_string(
            query_params.location,
            Point,
        )
        validate_srid_4326(geometry)
        validated_location = wkt.dumps(geometry)
    if query_params.company_id is not None:
        query = query.filter(ServiceLocation.company_id == query_params.company_id)
    if query_params.service_id is not None:
        query = query.filter(ServiceLocation.service_id == query_params.service_id)
    if query_params.landmark_id is not None:
        query = query.filter(ServiceLocation.landmark_id == query_params.landmark_id)
    if query_params.accuracy_ge is not None:
        query = query.filter(ServiceLocation.accuracy >= query_params.accuracy_ge)
    if query_params.accuracy_le is not None:
        query = query.filter(ServiceLocation.accuracy <= query_params.accuracy_le)

    # Generalized filters
    query = apply_id_filters(query, ServiceLocation, query_params)
    query = apply_created_on_filters(query, ServiceLocation, query_params)
    query = apply_updated_on_filters(query, ServiceLocation, query_params)

    # Ordering and pagination
    if query_params.order_by == OrderBy.LOCATION:
        if validated_location is not None:
            ordering_attr = func.ST_Distance(
                ServiceLocation.location.cast(Geography),
                func.ST_GeogFromText(validated_location),
            )
        else:
            ordering_attr = ServiceLocation.id
    else:
        ordering_attr = getattr(ServiceLocation, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    service_locations = query.all()
    return service_locations


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ServiceLocation.id),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
PATCH_DESCRIPTION = (
    Description()
    .add_head("Update a service location.")
    .add_line(
        "Logged-in operator must have `company.service.ticket.create` permission."
    )
    .add_line("The location field must be a valid WKT string.")
    .add_line("The coordinates must be in longitude/latitude format.")
    .add_line("Use WGS84 compatible coordinates within SRID 4326 bounds.")
    .add_line("Only one entry can be maintained for each service")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of service locations.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_SERVICE_TRACE,
    summary="Fetch service location",
    tags=["Service Location"],
    response_model=list[ServiceLocationSchema],
    responses=fuse_exception_responses([*GET_EXCEPTIONS, exceptions.InvalidToken()]),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_locations_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return [
            service_location_to_dict(service_location)
            for service_location in search_service_locations(
                session,
                QueryParams(**query_params.model_dump()),
            )
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.patch(
    f"{URL_SERVICE_TRACE}/{{id}}",
    summary="Update service location",
    tags=["Service Location"],
    response_model=ServiceLocationSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(PATCH_DESCRIPTION.to_string()),
)
async def update_service_location_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_SERVICE_TICKET],
        )
        return update_service_location(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            service_location_filter=(ServiceLocation.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_SERVICE_TRACE,
    summary="Fetch service location",
    tags=["Service Location"],
    response_model=list[ServiceLocationSchema],
    responses=fuse_exception_responses([*GET_EXCEPTIONS, exceptions.InvalidToken()]),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_locations_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return [
            service_location_to_dict(service_location)
            for service_location in search_service_locations(
                session,
                QueryParams(**query_params.model_dump(), company_id=token.company_id),
            )
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_SERVICE_TRACE,
    summary="Fetch service location",
    tags=["Service Location"],
    response_model=list[ServiceLocationSchema],
    responses=fuse_exception_responses([*GET_EXCEPTIONS, exceptions.InvalidToken()]),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_locations_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return [
            service_location_to_dict(service_location)
            for service_location in search_service_locations(
                session,
                QueryParams(**query_params.model_dump()),
            )
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_SERVICE_TRACE,
    summary="Fetch service location",
    tags=["Service Location"],
    response_model=list[ServiceLocationSchema],
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_locations_for_public(
    query_params: QueryParamsForPU = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        return [
            service_location_to_dict(service_location)
            for service_location in search_service_locations(
                session,
                QueryParams(**query_params.model_dump(), company_id=None),
            )
        ]
    except Exception as e:
        exceptions.handle(e)
