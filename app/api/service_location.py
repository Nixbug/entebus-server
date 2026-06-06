"""
Service Location API Router for EnteBus.

Provides endpoints for managing service locations, including creation,
update, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Depends, Query
from geoalchemy2 import Geography
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from shapely.geometry import Point
from shapely import wkt, wkb
from fastapi.encoders import jsonable_encoder
from typing import List, Tuple

from app.src.db import (
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
    ServiceLocation,
    VendorToken,
)
from app.src.description import Description
from app.src.enums import OrderIn
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
    get_request_info,
    update_if_changed,
)
from app.src.openobserve import log_event
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
    landmark_id: int | None
    location: str | None
    accuracy: float | None
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class UpdateForm(BaseModel):
    """Form data for updating an existing service location."""

    location: str | None = Field(default=None)
    accuracy: float | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    LOCATION = "location"


class QueryParamsForPU(
    UpdatedOnFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
):
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
## Functions
# ---------------------------------------------------------------------------
def update_service_location(
    session: Session,
    id: int,
    form_param: UpdateForm,
    extra_filter_for_service_location=None,
) -> Tuple[bool, dict]:
    """
    Updates an existing service location record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the service location to update.
        form_param (UpdateForm): Form data for updating the service location.
        extra_filter_for_service_location (optional): Additional filter to apply when validating the service location ID.

    Returns:
        Tuple[bool, dict]: A tuple containing a boolean indicating whether any updates were made and the updated service location data.
    """
    service_location = validate_id(
        session,
        ServiceLocation,
        id,
        ServiceLocation.id,
        extra_filter=extra_filter_for_service_location,
    )
    update_data = form_param.model_dump(exclude_unset=True)
    if form_param.location is not None:
        geometry = validate_wkt_string(
            form_param.location,
            Point,
        )
        validate_srid_4326(geometry)
        service_location.location = wkt.dumps(geometry)
        update_data.pop("location")

    update_if_changed(service_location, update_data)
    have_updates = session.is_modified(service_location)
    if have_updates:
        session.commit()
        session.refresh(service_location)

    service_location_data = jsonable_encoder(
        service_location,
        exclude={ServiceLocation.location.name},
    )
    if service_location.location is not None:
        service_location_data[ServiceLocation.location.name] = (
            wkb.loads(bytes(service_location.location.data))
        ).wkt
    else:
        service_location_data[ServiceLocation.location.name] = None
    return have_updates, service_location_data


def search_service_location(
    session: Session, query_params: QueryParams
) -> List[ServiceLocation]:
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

    query = query.with_entities(
        ServiceLocation,
        func.ST_AsText(ServiceLocation.location).label("location_wkt"),
    )
    results = query.all()
    service_locations = []
    for service_location_obj, location_wkt in results:
        service_location_obj.location = location_wkt
        service_locations.append(service_location_obj)

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
## Common descriptions
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
    response_model=List[ServiceLocationSchema],
    responses=fuse_exception_responses([*GET_EXCEPTIONS, exceptions.InvalidToken()]),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_locations_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_service_location(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


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
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_SERVICE_TICKET],
        )

        have_updates, service_location_data = update_service_location(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            extra_filter_for_service_location=(
                ServiceLocation.company_id == token.company_id
            ),
        )

        if have_updates:
            log_event(token, request_info, service_location_data)
        return service_location_data

    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_SERVICE_TRACE,
    summary="Fetch service location",
    tags=["Service Location"],
    response_model=List[ServiceLocationSchema],
    responses=fuse_exception_responses([*GET_EXCEPTIONS, exceptions.InvalidToken()]),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_locations_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_service_location(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_SERVICE_TRACE,
    summary="Fetch service location",
    tags=["Service Location"],
    response_model=List[ServiceLocationSchema],
    responses=fuse_exception_responses([*GET_EXCEPTIONS, exceptions.InvalidToken()]),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_locations_for_vendor(
    query_params: QueryParamsForVE = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        verify_token(session, VendorToken, access_token.credentials)

        return search_service_location(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_SERVICE_TRACE,
    summary="Fetch service location",
    tags=["Service Location"],
    response_model=List[ServiceLocationSchema],
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_locations_for_public(
    query_params: QueryParamsForPU = Depends(),
):
    try:
        session = SessionLocal()

        return search_service_location(
            session,
            QueryParams(
                **query_params.model_dump(),
                company_id=None,
            ),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
