"""
Fare API Router for EnteBus.

Provides endpoints for managing fares, including creation and update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion and retrieval are planned for future implementation.
"""

from datetime import datetime
from typing import List, Dict, Any
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    Company,
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
    Fare,
)
from app.src.enums import FareScope
from app.src.functions import (
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    enum_str,
    update_if_changed,
)
from app.src.openobserve import log_event
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_FARE
from app.src.validators import verify_token, verify_permission
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.validators import validate_fare_function, validate_id


route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class TicketTypesInAttribute(BaseModel):
    """Schema for ticket types in fare attributes."""

    id: int
    name: str


class FareAttributes(BaseModel):
    """Attributes for fare details."""

    df_version: int
    ticket_types: List[TicketTypesInAttribute]
    currency_type: str
    distance_unit: str
    extra: Dict[str, Any]


class FareSchema(BaseModel):
    """Schema for fare response."""

    id: int
    company_id: int | None
    version: int
    name: str
    attributes: FareAttributes
    function: str
    scope: FareScope
    updated_on: datetime | None
    created_on: datetime


## Input Forms
class CreateFormForOP(BaseModel):
    """Form data for creating a new fare for an operator."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    attributes: FareAttributes = Field()
    function: str = Field(min_length=1, max_length=32768)


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new fare for an executive."""

    company_id: int | None = Field(default=None)
    scope: FareScope = Field(description=enum_str(FareScope), default=FareScope.GLOBAL)


class UpdateForm(BaseModel):
    """Form data for updating a fare."""

    name: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=NAME_PATTERN
    )
    attributes: FareAttributes | None = Field(default=None)
    function: str | None = Field(default=None, min_length=1, max_length=32768)


## Functions
def update_fare(session: Session, fare: Fare, form_param: UpdateForm):
    """
    Updates an existing fare record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        fare (Fare): The existing fare record to be updated.
        form_param (UpdateForm): Form data for updating the fare.

    Returns:
        dict: The updated fare data.
    """
    update_data = form_param.model_dump(exclude_unset=True)
    if form_param.attributes is not None:
        attribute_data = form_param.attributes.model_dump()
        if attribute_data != fare.attributes:
            fare.attributes = attribute_data
        update_data.pop("attributes")
    update_if_changed(fare, update_data)
    validate_fare_function(fare.function, fare.attributes)
    have_updates = session.is_modified(fare)
    if have_updates:
        fare.version += 1
        session.commit()
        session.refresh(fare)

    fare_data = jsonable_encoder(fare)
    return have_updates, fare_data


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_FARE,
    tags=["Fare"],
    response_model=FareSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Fare.company_id),
            exceptions.UnexpectedParameter(Fare.company_id),
            exceptions.MissingParameter(Fare.company_id),
            exceptions.InvalidFareVersion(),
            exceptions.InvalidFareFunction(),
            exceptions.JSTimeLimitExceeded(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.UnknownTicketType(),
        ]
    ),
    description=(
        """
            **Creates a new fare for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.fare.create` permission.    
            - If scope is GLOBAL, company_id must be null. If scope is LOCAL, company_id must be provided.    
            - The fare function is validated against the provided attributes.    
            - The maximum allowed size for the fare function is 10 MB and maximum execution time is 1 second.    
        """
    ),
)
async def create_fare_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_FARE)

        if form_param.scope == FareScope.GLOBAL and form_param.company_id is not None:
            raise exceptions.UnexpectedParameter(Fare.company_id)
        if form_param.scope == FareScope.LOCAL and form_param.company_id is None:
            raise exceptions.MissingParameter(Fare.company_id)
        if form_param.company_id is not None:
            validate_id(session, Company, form_param.company_id, Fare.company_id)
        form_param.attributes = form_param.attributes.model_dump()
        validate_fare_function(form_param.function, form_param.attributes)
        fare = Fare(
            company_id=form_param.company_id,
            name=form_param.name,
            attributes=form_param.attributes,
            function=form_param.function,
            scope=form_param.scope,
        )
        session.add(fare)
        session.commit()
        session.refresh(fare)
        fare_data = jsonable_encoder(fare)

        log_event(token, request_info, fare_data)
        return fare_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_FARE}/{{id}}",
    tags=["Fare"],
    response_model=FareSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Fare.id),
            exceptions.InvalidFareVersion(),
            exceptions.InvalidFareFunction(),
            exceptions.JSTimeLimitExceeded(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.UnknownTicketType(),
        ]
    ),
    description=(
        """
            **Updates an existing fare for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.fare.update` permission.    
            - DF function and attributes are validated together.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_fare_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_FARE)

        fare = validate_id(session, Fare, id, Fare.id)

        have_updates, fare_data = update_fare(
            session, fare, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, fare_data)
        return fare_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_FARE,
    tags=["Fare"],
    response_model=FareSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidFareVersion(),
            exceptions.InvalidFareFunction(),
            exceptions.JSTimeLimitExceeded(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.UnknownTicketType(),
        ]
    ),
    description=(
        """
            **Creates a new fare for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.fare.create` permission.    
            - Operators can only create fares with LOCAL scope for their own company.    
            - The fare function is validated against the provided attributes.    
            - Enforces function size is 10 MB or less and execution time is 1 second or less.    
        """
    ),
)
async def create_fare_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_FARE)

        form_param.attributes = form_param.attributes.model_dump()
        validate_fare_function(form_param.function, form_param.attributes)
        fare = Fare(
            company_id=token.company_id,
            name=form_param.name,
            attributes=form_param.attributes,
            function=form_param.function,
            scope=FareScope.LOCAL,
        )
        session.add(fare)
        session.commit()
        session.refresh(fare)
        fare_data = jsonable_encoder(fare)

        log_event(token, request_info, fare_data)
        return fare_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_FARE}/{{id}}",
    tags=["Fare"],
    response_model=FareSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidFareVersion(),
            exceptions.InvalidFareFunction(),
            exceptions.JSTimeLimitExceeded(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.UnknownTicketType(),
            exceptions.UnknownValue(Fare.id),
        ]
    ),
    description=(
        """
            **Updates an existing fare for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.fare.update` permission.    
            - DF function and attributes are validated together.    
            - Only fares belonging to the operator's company can be updated.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_fare_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_FARE)

        fare = validate_id(
            session, Fare, id, Fare.id, (Fare.company_id == token.company_id)
        )

        have_updates, fare_data = update_fare(
            session, fare, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, fare_data)
        return fare_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
