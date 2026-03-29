"""
Vehicle API Router for EnteBus.

Provides endpoints for managing vehicles, including creation, update and retrieval.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion are planned for future implementation.
"""

from typing import Optional, List
from enum import StrEnum
from fastapi import APIRouter, Query, status, Depends
from datetime import datetime
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy import String, or_
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.src.db import (
    Company,
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
    Vehicle,
    VendorToken,
)
from app.src.enums import (
    OrderIn,
    VehicleStatus,
)
from app.src.constants import TMZ_PRIMARY
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.regex import VEHICLE_NUMBER_PATTERN, NAME_PATTERN
from app.src.urls import URL_VEHICLE
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token, validate_id
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    update_if_changed,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_status_filters,
    apply_name_filters,
    resolve_model_defaults,
)
from app.src.filters import (
    IDFilter,
    CreatedOnFilter,
    UpdatedOnFilter,
    PaginationFilter,
    NameFilter,
)

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


## Output Schema
class MaskedVehicleSchema(BaseModel):
    """Schema for masked vehicle responses without revealing all details."""

    id: int
    company_id: int
    registration_number: str
    name: str
    capacity: int
    updated_on: datetime | None
    created_on: datetime


class VehicleSchema(MaskedVehicleSchema):
    """Schema for vehicle response."""

    manufactured_on: Optional[datetime]
    insurance_upto: Optional[datetime]
    pollution_upto: Optional[datetime]
    fitness_upto: Optional[datetime]
    road_tax_upto: Optional[datetime]
    status: int


# Input Forms
class CreateFormForOP(BaseModel):
    """Form data for creating a new vehicle for an operator."""

    registration_number: str = Field(pattern=VEHICLE_NUMBER_PATTERN, max_length=16)
    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    capacity: int = Field(ge=1, le=120)
    manufactured_on: datetime | None = Field(default=None)
    insurance_upto: datetime | None = Field(default=None)
    pollution_upto: datetime | None = Field(default=None)
    fitness_upto: datetime | None = Field(default=None)
    road_tax_upto: datetime | None = Field(default=None)
    status: VehicleStatus = Field(
        description=enum_str(VehicleStatus), default=VehicleStatus.ACTIVE
    )


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new vehicle for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new vehicle."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating a vehicle."""

    name: str = Field(default=None, min_length=1, max_length=32, pattern=NAME_PATTERN)
    capacity: int = Field(ge=1, le=120, default=None)
    manufactured_on: datetime | None = Field(default=None)
    insurance_upto: datetime | None = Field(default=None)
    pollution_upto: datetime | None = Field(default=None)
    fitness_upto: datetime | None = Field(default=None)
    road_tax_upto: datetime | None = Field(default=None)
    status: VehicleStatus = Field(description=enum_str(VehicleStatus), default=None)


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering company results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    LOCATION = "location"


class QueryParamsForPU(
    IDFilter, CreatedOnFilter, UpdatedOnFilter, NameFilter, PaginationFilter
):
    """Query parameters for public users."""

    search: str | None = Field(Query(default=None))
    registration_number: str | None = Field(Query(default=None))
    capacity_ge: int | None = Field(Query(default=None))
    capacity_le: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForOP(QueryParamsForPU):
    """Query parameters for operators users."""

    manufactured_on_ge: datetime | None = Field(Query(default=None))
    manufactured_on_le: datetime | None = Field(Query(default=None))
    insurance_upto_ge: datetime | None = Field(Query(default=None))
    insurance_upto_le: datetime | None = Field(Query(default=None))
    pollution_upto_ge: datetime | None = Field(Query(default=None))
    pollution_upto_le: datetime | None = Field(Query(default=None))
    fitness_upto_ge: datetime | None = Field(Query(default=None))
    fitness_upto_le: datetime | None = Field(Query(default=None))
    road_tax_upto_ge: datetime | None = Field(Query(default=None))
    road_tax_upto_le: datetime | None = Field(Query(default=None))
    status_list: List[VehicleStatus] | None = Field(
        Query(default=None, description=enum_str(VehicleStatus))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives users."""

    company_id: int | None = Field(Query(default=None))


class QueryParamsForVE(QueryParamsForEX):
    """Query parameters for vendor users."""

    pass


class QueryParams(QueryParamsForEX):
    """Generic combined query parameters for vehicles."""

    pass


## Functions
def validate_manufactured_on(
    form_param: CreateFormForOP | CreateFormForEX | UpdateForm,
):
    """
    Validate that the manufactured_on date is not in the future.

    Args:
        form_param (CreateFormForOP | CreateFormForEX | UpdateForm): The form data containing the manufactured_on field.

    Raises:
        exceptions.InvalidValue: If the manufactured_on date is in the future.
    """
    manufactured_on = form_param.manufactured_on
    if manufactured_on is None:
        return None

    if manufactured_on.tzinfo is None:
        manufactured_on = manufactured_on.replace(tzinfo=TMZ_PRIMARY)
    else:
        manufactured_on = manufactured_on.astimezone(TMZ_PRIMARY)

    if manufactured_on > datetime.now(tz=TMZ_PRIMARY):
        raise exceptions.InvalidValue(Vehicle.manufactured_on)
    return manufactured_on


def create_vehicle(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new vehicle record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a vehicle.

    Returns:
        dict: The created vehicle data.
    """
    validate_manufactured_on(form_param)
    vehicle = Vehicle(
        company_id=form_param.company_id,
        registration_number=form_param.registration_number,
        name=form_param.name,
        capacity=form_param.capacity,
        manufactured_on=form_param.manufactured_on,
        insurance_upto=form_param.insurance_upto,
        pollution_upto=form_param.pollution_upto,
        fitness_upto=form_param.fitness_upto,
        road_tax_upto=form_param.road_tax_upto,
        status=form_param.status,
    )
    session.add(vehicle)
    session.commit()
    session.refresh(vehicle)
    vehicle_data = jsonable_encoder(vehicle)
    return vehicle_data


def update_vehicle(session: Session, vehicle: Vehicle, form_param: UpdateForm):
    """
    Updates an existing vehicle record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        vehicle (Vehicle): The existing vehicle record to be updated.
        form_param (UpdateForm): Form data for updating the vehicle.

    Returns:
        dict: The updated vehicle data.
    """
    validate_manufactured_on(form_param)
    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(vehicle, update_data)
    have_updates = session.is_modified(vehicle)
    if have_updates:
        session.commit()
        session.refresh(vehicle)

    vehicle_data = jsonable_encoder(vehicle)
    return have_updates, vehicle_data


def search_vehicle(session: Session, query_params: QueryParams) -> List[Vehicle]:
    """
    Search for Vehicles based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve vehicles that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Vehicle]: List of Vehicles that match the search criteria.
    """
    query = session.query(Vehicle)
    if query_params.company_id is not None:
        query = query.filter(Vehicle.company_id == query_params.company_id)
    if query_params.registration_number is not None:
        query = query.filter(
            Vehicle.registration_number.ilike(f"%{query_params.registration_number}%")
        )
    if query_params.capacity_ge is not None:
        query = query.filter(Vehicle.capacity >= query_params.capacity_ge)
    if query_params.capacity_le is not None:
        query = query.filter(Vehicle.capacity <= query_params.capacity_le)
    if query_params.manufactured_on_ge is not None:
        query = query.filter(Vehicle.manufactured_on >= query_params.manufactured_on_ge)
    if query_params.manufactured_on_le is not None:
        query = query.filter(Vehicle.manufactured_on <= query_params.manufactured_on_le)
    if query_params.insurance_upto_ge is not None:
        query = query.filter(Vehicle.insurance_upto >= query_params.insurance_upto_ge)
    if query_params.insurance_upto_le is not None:
        query = query.filter(Vehicle.insurance_upto <= query_params.insurance_upto_le)
    if query_params.pollution_upto_ge is not None:
        query = query.filter(Vehicle.pollution_upto >= query_params.pollution_upto_ge)
    if query_params.pollution_upto_le is not None:
        query = query.filter(Vehicle.pollution_upto <= query_params.pollution_upto_le)
    if query_params.fitness_upto_ge is not None:
        query = query.filter(Vehicle.fitness_upto >= query_params.fitness_upto_ge)
    if query_params.fitness_upto_le is not None:
        query = query.filter(Vehicle.fitness_upto <= query_params.fitness_upto_le)
    if query_params.road_tax_upto_ge is not None:
        query = query.filter(Vehicle.road_tax_upto >= query_params.road_tax_upto_ge)
    if query_params.road_tax_upto_le is not None:
        query = query.filter(Vehicle.road_tax_upto <= query_params.road_tax_upto_le)

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Vehicle.id.cast(String).ilike(search),
                Vehicle.registration_number.ilike(search),
                Vehicle.name.ilike(search),
                Vehicle.capacity.cast(String).ilike(search),
                Vehicle.manufactured_on.cast(String).ilike(search),
                Vehicle.insurance_upto.cast(String).ilike(search),
                Vehicle.pollution_upto.cast(String).ilike(search),
                Vehicle.fitness_upto.cast(String).ilike(search),
                Vehicle.road_tax_upto.cast(String).ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Vehicle, query_params)
    query = apply_name_filters(query, Vehicle, query_params)
    query = apply_created_on_filters(query, Vehicle, query_params)
    query = apply_updated_on_filters(query, Vehicle, query_params)
    query = apply_status_filters(query, Vehicle, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Vehicle, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    vehicles = query.all()
    return vehicles


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VEHICLE,
    tags=["Vehicle"],
    response_model=VehicleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidValue(Vehicle.manufactured_on),
            exceptions.UnknownValue(Vehicle.company_id),
        ]
    ),
    description=(
        """
            **Creates a new vehicle for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.vehicle.create` permission.    
            - Duplicate registration numbers are not allowed.   
            - Manufactured date cannot be in the future.    
            - By default the vehicle is created in active status.    
        """
    ),
)
async def create_vehicle_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_VEHICLE)

        validate_id(session, Company, form_param.company_id, Vehicle.company_id)
        vehicle_data = create_vehicle(session, CreateForm(**form_param.model_dump()))

        log_event(token, request_info, vehicle_data)
        return vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_VEHICLE}/{{id}}",
    tags=["Vehicle"],
    response_model=VehicleSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidValue(Vehicle.manufactured_on),
            exceptions.UnknownValue(Vehicle.id),
        ]
    ),
    description=(
        """
            **Updates an existing vehicle for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.vehicle.update` permission.    
            - Manufactured date cannot be in the future.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_vehicle_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_VEHICLE)

        vehicle = validate_id(session, Vehicle, id, Vehicle.id)
        have_updates, vehicle_data = update_vehicle(
            session, vehicle, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, vehicle_data)
        return vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_VEHICLE,
    tags=["Vehicle"],
    response_model=List[VehicleSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vehicles.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_vehicle_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_vehicle(
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
    URL_VEHICLE,
    tags=["Vehicle"],
    response_model=VehicleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidValue(Vehicle.manufactured_on),
        ]
    ),
    description=(
        """
            **Creates a new vehicle for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.vehicle.create` permission.    
            - Duplicate registration numbers are not allowed.    
            - Manufactured date cannot be in the future.    
            - By default the vehicle is created in active status.    
        """
    ),
)
async def create_vehicle_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_VEHICLE)

        vehicle_data = create_vehicle(
            session, CreateForm(**form_param.model_dump(), company_id=token.company_id)
        )
        log_event(token, request_info, vehicle_data)
        return vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_VEHICLE}/{{id}}",
    tags=["Vehicle"],
    response_model=VehicleSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidValue(Vehicle.manufactured_on),
            exceptions.UnknownValue(Vehicle.id),
        ]
    ),
    description=(
        """
            **Updates an existing vehicle for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.vehicle.update` permission.    
            - Manufactured date cannot be in the future.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_vehicle_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_VEHICLE)

        vehicle = validate_id(
            session, Vehicle, id, Vehicle.id, (Vehicle.company_id == token.company_id)
        )
        have_updates, vehicle_data = update_vehicle(
            session, vehicle, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )
        if have_updates:
            log_event(token, request_info, vehicle_data)
        return vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_VEHICLE,
    tags=["Vehicle"],
    response_model=List[VehicleSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vehicles.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_vehicle_executive(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_vehicle(
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
    URL_VEHICLE,
    tags=["Vehicle"],
    response_model=List[VehicleSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vehicles.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_vehicle_vendor(
    query_params: QueryParamsForVE = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        return search_vehicle(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_public.get(
    URL_VEHICLE,
    tags=["Vehicle"],
    response_model=List[MaskedVehicleSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vehicles for public users.**    
            - Only masked fields are returned.    
            - By default only active vehicles are returned.    
        """
    ),
)
async def fetch_vehicle_public(query_params: QueryParamsForPU = Depends()):
    try:
        session = SessionLocal()

        query_params = resolve_model_defaults(
            QueryParams, **query_params.model_dump(), status_list=[VehicleStatus.ACTIVE]
        )
        return search_vehicle(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
