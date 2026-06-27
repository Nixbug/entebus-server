"""
Location In Trace API Router for EnteBus.

Provides endpoints for managing location in traces, including creation and retrieval.
Uses Pydantic schemas for input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from geoalchemy2 import Geography
from shapely import Point, wkt
from sqlalchemy import func
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from typing import List, Dict
from sqlalchemy.orm import Session

from app.src.db import (
    ExecutiveToken,
    LocationInTrace,
    OperatorToken,
    SessionLocal,
    Trace,
)
from app.src.description import Description
from app.src.constants import MAX_LOCATIONS_PER_TRACE
from app.src.enums import LocationType, OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
)
from app.src import exceptions
from app.src.urls import URL_LOCATION_TRACE
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    validate_srid_4326,
    validate_wkt_string,
    verify_token,
)
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.api.bearer import oauth2_executive, bearer_operator

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class LocationInTraceSchema(BaseModel):
    """Schema for location in trace response."""

    id: int
    trace_id: int
    company_id: int
    location: str
    location_type: int
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class LocationInTraceForm(BaseModel):
    """Form data for creating a location in trace."""

    location_type: LocationType = Field()
    locations: List[str] = Field(
        description=(
            "Accepts only SRID 4326 (WGS84) and valid WKT strings representing `POINT`s. Supports batch uploads with a list of WKT strings."
        ),
    )


class CreateForm(BaseModel):
    """Form data for creating locations in trace."""

    trace_id: int = Field()
    trace_locations: List[LocationInTraceForm] = Field()

    @model_validator(mode="after")
    def validate_total_locations(self):
        total_locations = sum(
            len(location.locations) for location in self.trace_locations
        )
        if total_locations == 0:
            raise ValueError("At least 1 location is required per batch upload.")
        if total_locations > 50:
            raise ValueError("At most 50 locations are allowed per batch upload.")
        return self


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    LOCATION = "location"


class QueryParamsForOP(
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
):
    """Query parameters for operators."""

    trace_id: int | None = Field(Query(default=None))
    location: str | None = Field(
        Query(
            default=None,
            description=(
                "Accepts only SRID 4326 (WGS84) and a valid WKT string representing a `POINT`."
            ),
        )
    )
    type: LocationType | None = Field(
        Query(default=None, description=enum_str(LocationType))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Functions
# ---------------------------------------------------------------------------
def create_location_in_trace(
    session: Session, form_param: CreateForm, extra_filter_for_trace=None
) -> None:
    """
    Creates a new location in trace record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a location in trace.
        extra_filter_for_trace (optional): Additional filter to apply when validating the trace ID.

    Returns:
        None
    """
    trace = validate_id(
        session,
        Trace,
        form_param.trace_id,
        LocationInTrace.trace_id,
        extra_filter=extra_filter_for_trace,
    )
    location_count = (
        session.query(LocationInTrace)
        .filter(LocationInTrace.trace_id == form_param.trace_id)
        .count()
    )
    if location_count >= MAX_LOCATIONS_PER_TRACE:
        raise exceptions.LimitExceeded(LocationInTrace)
    for location_data in form_param.trace_locations:
        for location in location_data.locations:
            geometry = validate_wkt_string(location, Point)
            validate_srid_4326(geometry)
            location_in_trace = LocationInTrace(
                trace_id=form_param.trace_id,
                company_id=trace.company_id,
                location=wkt.dumps(geometry),
                location_type=location_data.location_type,
            )
            session.add(location_in_trace)
    session.commit()


def search_location_in_trace(
    session: Session, query_params: QueryParams
) -> List[LocationInTrace]:
    """
    Search for locations in trace based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve locations in trace that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[LocationInTrace]: List of locations in trace that match the search criteria.
    """
    query = session.query(LocationInTrace)
    if query_params.company_id is not None:
        query = query.filter(LocationInTrace.company_id == query_params.company_id)
    validated_location = None
    if query_params.location is not None:
        geometry = validate_wkt_string(query_params.location, Point)
        validate_srid_4326(geometry)
        validated_location = wkt.dumps(geometry)
    if query_params.trace_id is not None:
        query = query.filter(LocationInTrace.trace_id == query_params.trace_id)
    if query_params.type is not None:
        query = query.filter(LocationInTrace.location_type == query_params.type)

    # Generalized filters
    query = apply_id_filters(query, LocationInTrace, query_params)
    query = apply_created_on_filters(query, LocationInTrace, query_params)

    # Ordering and pagination
    if query_params.order_by == OrderBy.LOCATION:
        if validated_location is not None:
            ordering_attr = func.ST_Distance(
                LocationInTrace.location.cast(Geography),
                func.ST_GeogFromText(validated_location),
            )
        else:
            ordering_attr = LocationInTrace.id
    else:
        ordering_attr = getattr(LocationInTrace, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    query = query.with_entities(
        LocationInTrace, func.ST_AsText(LocationInTrace.location).label("location_wkt")
    )
    results = query.all()
    locations_in_trace = []
    for location_in_trace_obj, location_wkt in results:
        location_in_trace_obj.location = location_wkt
        locations_in_trace.append(location_in_trace_obj)

    return locations_in_trace


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.UnknownValue(LocationInTrace.trace_id),
    exceptions.LimitExceeded(LocationInTrace),
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
    .add_head("Creates a new location in trace.")
    .add_line("The location field must be a valid WKT string.")
    .add_line("The coordinates must be in longitude/latitude format.")
    .add_line("Use WGS84 compatible coordinates within SRID 4326 bounds.")
    .add_line("Supports batch uploads")
    .add_line("A maximum of 50 locations can be uploaded in a single batch upload.")
    .add_line(
        f"A maximum of {MAX_LOCATIONS_PER_TRACE} locations can be associated with a single trace."
    )
)

GET_DESCRIPTION = (
    Description()
    .add_head("Fetches a list of locations in trace.")
    .add_line(
        "If location is not provided while using order_by=location, the API will fall back to default ordering by id."
    )
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_LOCATION_TRACE,
    summary="Create location in trace",
    tags=["Location In Trace"],
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            *POST_EXCEPTIONS,
        ]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.trace.create` or `company.trace.update` permission."
        )
        .to_string()
    ),
)
async def create_location_in_trace_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        authorize_executive(
            session,
            access_token,
            [
                ExecutivePermissionPath.CREATE_COMPANY_TRACE,
                ExecutivePermissionPath.UPDATE_COMPANY_TRACE,
            ],
        )

        create_location_in_trace(session, CreateForm(**form_param.model_dump()))
        return Response(status_code=status.HTTP_201_CREATED)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_LOCATION_TRACE,
    summary="Fetch locations in trace",
    tags=["Location In Trace"],
    response_model=List[LocationInTraceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_location_in_trace_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_location_in_trace(
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
@route_operator.post(
    URL_LOCATION_TRACE,
    summary="Create location in trace",
    tags=["Location In Trace"],
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses([*POST_EXCEPTIONS]),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.trace.create` or `company.trace.update` permission."
        )
        .to_string()
    ),
)
async def create_location_in_trace_for_operator(
    form_param: CreateForm,
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [
                OperatorPermissionPath.CREATE_COMPANY_TRACE,
                OperatorPermissionPath.UPDATE_COMPANY_TRACE,
            ],
        )

        create_location_in_trace(
            session,
            CreateForm(
                **form_param.model_dump(),
            ),
            extra_filter_for_trace=(Trace.company_id == token.company_id),
        )
        return Response(status_code=status.HTTP_201_CREATED)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_LOCATION_TRACE,
    summary="Fetch locations in trace",
    tags=["Location In Trace"],
    response_model=List[LocationInTraceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_location_in_trace_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_location_in_trace(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
