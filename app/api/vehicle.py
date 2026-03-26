"""
Vehicle API Router for EnteBus.

Provides endpoints for managing vehicles, including creation,and update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion and retrieval are planned for future implementation.
"""

from ast import pattern
from enum import StrEnum
from typing import List, Tuple, Optional
from datetime import datetime
from fastapi import APIRouter, Query, status, Depends, Response
from fastapi.encoders import jsonable_encoder
from pydantic_extra_types.phone_numbers import PhoneNumber
from sqlalchemy import String, or_
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    ExecutiveRole,
    ExecutiveRole,
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
    Operator,
    OperatorImage,
    Vehicle,
)
from app.src.enums import (
    AccountStatus,
    GenderType,
    OperatorType,
    OrderIn,
    VehicleStatus,
)
from app.src.filters import (
    AccountDataFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.minio import delete_file
from app.src.constants import TMZ_PRIMARY
from app.src.buckets import OPERATOR_IMAGES
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.regex import VEHICLE_NUMBER_PATTERN, NAME_PATTERN
from app.src.urls import URL_OPERATOR_ACCOUNT, URL_VEHICLE
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token, validate_id
from app.src.functions import (
    apply_account_filters,
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    update_if_changed,
    apply_status_filters,
    apply_type_filters,
    fuse_exception_responses,
)

route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class MaskedVehicleSchema(BaseModel):
    """Schema for vehicle response for vendor without revealing all details."""

    id: int
    company_id: int
    registration_number: str
    name: str
    capacity: int
    updated_on: datetime | None
    created_on: datetime


class VehicleSchema(MaskedVehicleSchema):
    """Schema for vehicle response."""

    manufactured_on: datetime
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
    manufactured_on: datetime = Field()
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
    """Form data for updating an vehicle."""
    
    name: str = Field(default=None, min_length=1, max_length=32, pattern=NAME_PATTERN)
    capacity: int = Field(ge=1, le=120, default=None)
    manufactured_on: datetime = Field(default=None)
    insurance_upto: datetime | None = Field(default=None)
    pollution_upto: datetime | None = Field(default=None)
    fitness_upto: datetime | None = Field(default=None)
    road_tax_upto: datetime | None = Field(default=None)
    status: VehicleStatus = Field(
        description=enum_str(VehicleStatus), default=None
    )
    

## Functions
def validate_manufactured_on(form_param: CreateFormForOP | CreateFormForEX | UpdateForm):
    """
    Validate that the manufactured_on date is not in the future.
   
    Args:
        form_param (CreateFormForOP | CreateFormForEX | UpdateForm): The form data containing the manufactured_on field.

    Raises:
        exceptions.UnknownValue: If the manufactured_on date is in the future.
    """
    if form_param.manufactured_on is not None:
        form_param.manufactured_on = form_param.manufactured_on.replace(
            tzinfo=TMZ_PRIMARY
        )
        if form_param.manufactured_on > datetime.now(tz=TMZ_PRIMARY):
            raise exceptions.UnknownValue(Vehicle.manufactured_on)


def create_vehicle(session: Session,  form_param: CreateForm) -> dict:
    """
    Creates a new vehicle record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a vehicle.

    Returns:
        dict: The created vehicle data.
    """
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
    return vehicle_data

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
            exceptions.UnknownValue(Vehicle.manufactured_on),
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
        role = get_executive_roles(session, token)
        verify_permission(role, ExecutivePermissionPath.CREATE_COMPANY_VEHICLE)

        validate_manufactured_on(form_param)
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
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Vehicle.manufactured_on),
        ]
    ),
    description=(
        """
            **Updates an existing vehicle for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.vehicle.update` permission.    
            - Duplicate registration numbers are not allowed. 
            - Manufactured date cannot be in the future.     
            - Empty patch request are allowed and will result in no changes.   
        """
    ),
)
async def update_vehicle_executive(
    id : int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        role = get_executive_roles(session, token)
        verify_permission(role, ExecutivePermissionPath.UPDATE_COMPANY_VEHICLE)

        vehicle = validate_id(session, Vehicle, id, Vehicle.id)
        have_updates, vehicle_data = update_vehicle(session, vehicle, UpdateForm(**form_param.model_dump(exclude_unset=True)))
       
        if have_updates:
            log_event(token, request_info, vehicle_data)
        return vehicle_data
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
            exceptions.UnknownValue(Vehicle.manufactured_on),
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
        role = get_operator_roles(session, token)
        verify_permission(role, OperatorPermissionPath.CREATE_COMPANY_VEHICLE)

        validate_manufactured_on(form_param)
        Vehicle_data = create_vehicle(session, CreateForm(**form_param.model_dump(), company_id=token.company_id))
        log_event(token, request_info, Vehicle_data)
        return Vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_VEHICLE}/{{id}}",
    tags=["Vehicle"],
    response_model=VehicleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Vehicle.manufactured_on),
        ]
    ),
    description=(
        """
            **Updates an existing vehicle for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.vehicle.update` permission.    
            - Duplicate registration numbers are not allowed. 
            - Manufactured date cannot be in the future.     
            - Empty patch request are allowed and will result in no changes.   
        """
    ),
)
async def update_vehicle_operator(
    id : int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        role = get_operator_roles(session, token)
        verify_permission(role, OperatorPermissionPath.UPDATE_COMPANY_VEHICLE)

        vehicle = validate_id(session, Vehicle, id, Vehicle.id,(Vehicle.company_id == token.company_id))
        have_updates, vehicle_data = update_vehicle(session, vehicle, UpdateForm(**form_param.model_dump(exclude_unset=True)))
        if have_updates:
            log_event(token, request_info, vehicle_data)
        return vehicle_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
