"""
Vehicle API Router for EnteBus.

Provides endpoints for managing vehicles, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from typing import Optional, List, Tuple
from enum import StrEnum
from datetime import datetime
from fastapi import APIRouter, status, Depends, Response, Query
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
    AppID,
    OrderIn,
    VehicleStatus,
)
from app.src.constants import MAX_VEHICLES_PER_COMPANY, TMZ_PRIMARY
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.regex import VEHICLE_NUMBER_PATTERN, NAME_PATTERN
from app.src.urls import URL_VEHICLE
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.validators import (
    verify_permission,
    verify_token,
    validate_id,
    validate_state_transition,
    authorize_executive,
    authorize_operator,
)
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_operator_roles,
    get_request_info,
    update_if_changed,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_status_filters,
    apply_name_filters,
    resolve_model_defaults,
    normalize_timestamp,
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


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
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

    manufactured_on: datetime | None
    insurance_upto: datetime | None
    pollution_upto: datetime | None
    fitness_upto: datetime | None
    road_tax_upto: datetime | None
    status: int
    version: int


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
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


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new vehicle for an executive."""

    company_id: int = Field()
    status: VehicleStatus = Field(
        description=enum_str(VehicleStatus), default=VehicleStatus.CREATED
    )


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


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering vehicle results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForPU(
    IDFilter, CreatedOnFilter, UpdatedOnFilter, NameFilter, PaginationFilter
):
    """Query parameters for public."""

    search: str | None = Field(Query(default=None))
    registration_number: str | None = Field(Query(default=None))
    capacity_ge: int | None = Field(Query(default=None))
    capacity_le: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForOP(QueryParamsForPU):
    """Query parameters for operators."""

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
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParamsForVE(QueryParamsForEX):
    """Query parameters for vendors."""

    pass


class QueryParams(QueryParamsForEX):
    """Generic combined query parameters for vehicles."""

    pass


# ---------------------------------------------------------------------------
## Lock Generator
# ---------------------------------------------------------------------------
def construct_vehicle_reference_lock(vehicle_id: int) -> str:
    """
    Creates a Redis lock key for Vehicle snapshot creation and reference operations.

    Serializes access to Vehicle snapshot operations for the same
    vehicle_id, preventing concurrent creation or reference count
    updates of the same snapshot.

    Args:
        vehicle_id (int): Vehicle ID.
    """
    return f"lk_vehicle_:{vehicle_id}"


# ---------------------------------------------------------------------------
## Functions
# ---------------------------------------------------------------------------
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

    manufactured_on = normalize_timestamp(manufactured_on)
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
    vehicle_count = (
        session.query(Vehicle)
        .filter(Vehicle.company_id == form_param.company_id)
        .count()
    )
    if vehicle_count >= MAX_VEHICLES_PER_COMPANY:
        raise exceptions.LimitExceeded(Vehicle)

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


def update_vehicle(
    session: Session,
    id: int,
    form_param: UpdateForm,
    extra_filter_for_vehicle=None,
    app_id: AppID = None,
) -> Tuple[bool, dict]:
    """
    Updates an existing vehicle record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the vehicle to update.
        form_param (UpdateForm): Form data for updating the vehicle.
        extra_filter_for_vehicle (optional): Additional filter to apply when validating the vehicle ID.
        app_id (AppID, optional): Identifier of the application making the request. Used to determine allowed status transitions.

    Returns:
        Tuple[bool, dict]: A tuple containing a boolean indicating if updates were made and the updated vehicle data.
    """
    vehicle = validate_id(
        session, Vehicle, id, Vehicle.id, extra_filter=extra_filter_for_vehicle
    )
    validate_manufactured_on(form_param)
    update_data = form_param.model_dump(exclude_unset=True)

    if app_id == AppID.OPERATOR and "status" in update_data:
        allowed_vehicle_status_transitions = {
            VehicleStatus.ACTIVE: [VehicleStatus.MAINTENANCE],
            VehicleStatus.MAINTENANCE: [VehicleStatus.ACTIVE],
        }
        validate_state_transition(
            allowed_vehicle_status_transitions,
            vehicle.status,
            update_data.get("status"),
            Vehicle.status,
        )

    update_if_changed(vehicle, update_data)
    have_updates = session.is_modified(vehicle)
    if have_updates:
        vehicle.version += 1
        session.commit()
        session.refresh(vehicle)

    vehicle_data = jsonable_encoder(vehicle)
    return have_updates, vehicle_data


def delete_vehicle(session: Session, vehicle: Vehicle) -> dict:
    """
    Deletes a vehicle from the database.

    Args:
        session (Session): SQLAlchemy database session.
        vehicle (Vehicle): Vehicle to delete.

    Returns:
        dict: JSON-encoded representation of the deleted vehicle.
    """
    vehicle_data = jsonable_encoder(vehicle)
    session.delete(vehicle)
    session.commit()
    return vehicle_data


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
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidValue(Vehicle.manufactured_on),
    exceptions.LimitExceeded(Vehicle),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidValue(Vehicle.manufactured_on),
    exceptions.UnknownValue(Vehicle.id),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common descriptions
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new vehicle.")
    .add_line("Duplicate registration numbers are not allowed.")
    .add_line("Manufactured date cannot be in the future.")
    .add_line("By default, the vehicle status is set to CREATED.")
    .add_line(f"Maximum vehicles per company is limited to {MAX_VEHICLES_PER_COMPANY}.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing vehicle.")
    .add_line("Manufactured date cannot be in the future.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing vehicle.")
    .add_line("Returns 204 No Content even if the specified vehicle does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of vehicles.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VEHICLE,
    summary="Create vehicle",
    tags=["Vehicle"],
    response_model=VehicleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [*POST_EXCEPTIONS, exceptions.UnknownValue(Vehicle.company_id)]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.vehicle.create` permission.")
        .to_string()
    ),
)
async def create_vehicle_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_VEHICLE],
        )

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
    summary="Update vehicle",
    tags=["Vehicle"],
    response_model=VehicleSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.vehicle.update` permission.")
        .to_string()
    ),
)
async def update_vehicle_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_VEHICLE],
        )

        have_updates, vehicle_data = update_vehicle(
            session, id, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, vehicle_data)
        return vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_VEHICLE}/{{id}}",
    summary="Delete vehicle",
    tags=["Vehicle"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `company.vehicle.delete` permission."
        )
        .to_string()
    ),
)
async def delete_vehicle_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_VEHICLE],
        )

        vehicle = session.query(Vehicle).filter(Vehicle.id == id).first()
        if vehicle is not None:
            vehicle_data = delete_vehicle(session, vehicle)
            log_event(token, request_info, vehicle_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_VEHICLE,
    summary="Fetch vehicle",
    tags=["Vehicle"],
    response_model=List[VehicleSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vehicles_for_executive(
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
    summary="Create vehicle",
    tags=["Vehicle"],
    response_model=VehicleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.vehicle.create` permission.")
        .to_string()
    ),
)
async def create_vehicle_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_VEHICLE],
        )

        vehicle_data = create_vehicle(
            session,
            CreateForm(
                **form_param.model_dump(),
                company_id=token.company_id,
                status=VehicleStatus.CREATED,
            ),
        )
        log_event(token, request_info, vehicle_data)
        return vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_VEHICLE}/{{id}}",
    summary="Update vehicle",
    tags=["Vehicle"],
    response_model=VehicleSchema,
    responses=fuse_exception_responses(
        [
            *PATCH_EXCEPTIONS,
            exceptions.InvalidStateTransition(Vehicle.status),
        ]
    ),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.vehicle.update` permission.")
        .add_line("Status transitions are only allowed between ACTIVE and MAINTENANCE.")
        .to_string()
    ),
)
async def update_vehicle_for_operator(
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

        have_updates, vehicle_data = update_vehicle(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            extra_filter_for_vehicle=(Vehicle.company_id == token.company_id),
            app_id=request_info.app_id,
        )
        if have_updates:
            log_event(token, request_info, vehicle_data)
        return vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_VEHICLE}/{{id}}",
    summary="Delete vehicle",
    tags=["Vehicle"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in operator must have the `company.vehicle.delete` permission."
        )
        .to_string()
    ),
)
async def delete_vehicle_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_VEHICLE],
        )

        vehicle = (
            session.query(Vehicle)
            .filter(Vehicle.id == id, Vehicle.company_id == token.company_id)
            .first()
        )
        if vehicle is not None:
            vehicle_data = delete_vehicle(session, vehicle)
            log_event(token, request_info, vehicle_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_VEHICLE,
    summary="Fetch vehicle",
    tags=["Vehicle"],
    response_model=List[VehicleSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vehicles_for_operator(
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
    summary="Fetch vehicle",
    tags=["Vehicle"],
    response_model=List[VehicleSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vehicles_for_vendor(
    query_params: QueryParamsForVE = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        verify_token(session, VendorToken, access_token.credentials)

        return search_vehicle(
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
    URL_VEHICLE,
    summary="Fetch vehicle",
    tags=["Vehicle"],
    response_model=List[MaskedVehicleSchema],
    description=(
        GET_DESCRIPTION.copy()
        .add_line("Only masked data is returned.")
        .add_line("By default only active vehicles are returned.")
        .to_string()
    ),
)
async def fetch_vehicles_for_public(query_params: QueryParamsForPU = Depends()):
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
