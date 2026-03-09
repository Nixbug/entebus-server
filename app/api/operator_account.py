"""
Operator Account API Router for EnteBus.

Provides endpoints for managing operator accounts, including creation,
Uses Pydantic schemas for input validation and structured output.
Endpoints for update, deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, Form, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic_extra_types.phone_numbers import PhoneNumber
from pydantic import BaseModel, EmailStr, Field

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import ExecutiveToken, OperatorToken, SessionLocal, Operator
from app.src.enums import AccountStatus, GenderType, OperatorType
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.regex import PASSWORD_PATTERN, USERNAME_PATTERN
from app.src.urls import URL_OPERATOR_ACCOUNT
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
)


route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class OperatorSchema(BaseModel):
    """Schema for operator account response."""

    id: int
    company_id: int
    username: str
    gender: int
    description: str | None
    type: int
    full_name: str | None
    status: int
    phone_number: str | None
    email_id: str | None
    updated_on: datetime | None
    created_on: datetime


## Input Forms
class CreateFormForOP(BaseModel):
    """Form data for creating a new operator account for an operator."""

    username: str = Field(min_length=4, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=32, pattern=PASSWORD_PATTERN)
    gender: GenderType = Field(
        description=enum_str(GenderType), default=GenderType.OTHER
    )
    description: str | None = Field(min_length=1, max_length=32, default=None)
    type: OperatorType = Field(
        description=enum_str(OperatorType),
        default=None,
    )
    full_name: str | None = Field(min_length=1, max_length=32, default=None)
    status: AccountStatus = Field(
        description=enum_str(AccountStatus), default=AccountStatus.ACTIVE
    )
    phone_number: PhoneNumber | None = Field(
        max_length=32, default=None, description="Phone number in RFC 3966 format"
    )
    email_id: EmailStr | None = Field(
        max_length=256, default=None, description="Email in RFC 5322 format"
    )


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new operator account for an executive."""
    company_id: int = Field()


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_OPERATOR_ACCOUNT,
    tags=["Operator Account"],
    response_model=OperatorSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new operator account.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.operator.create` permission.    
            - Duplicate usernames are not allowed.    
            - By default the user is created in active status.     
        """
    ),
)
async def create_account_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_OPERATOR)

        operator = Operator(
            company_id=form_param.company_id,
            username=form_param.username,
            password=form_param.password,
            gender=form_param.gender,
            description=form_param.description,
            type=form_param.type,
            full_name=form_param.full_name,
            status=form_param.status,
            phone_number=form_param.phone_number,
            email_id=form_param.email_id,
        )
        session.add(operator)
        session.commit()
        session.refresh(operator)

        operator_data = jsonable_encoder(operator, exclude={Operator.password.name})
        log_event(token, request_info, operator_data)
        return operator_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_ACCOUNT,
    tags=["Operator Account"],
    response_model=OperatorSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new operator account.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.operator.create` permission.    
            - Duplicate usernames are not allowed.    
            - By default the user is created in active status.    
        """
    ),
)
async def create_account_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_OPERATOR)

        operator = Operator(
            company_id=token.company_id,
            username=form_param.username,
            password=form_param.password,
            gender=form_param.gender,
            description=form_param.description,
            type=form_param.type,
            full_name=form_param.full_name,
            status=form_param.status,
            phone_number=form_param.phone_number,
            email_id=form_param.email_id,
        )
        session.add(operator)
        session.commit()
        session.refresh(operator)

        operator_data = jsonable_encoder(operator, exclude={Operator.password.name})
        log_event(token, request_info, operator_data)
        return operator_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
