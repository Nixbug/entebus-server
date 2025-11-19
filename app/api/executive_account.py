"""
Executive Account API Router for EnteBus.




Provides endpoints for managing executive accounts, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic_extra_types.phone_numbers import PhoneNumber
from pydantic import BaseModel, EmailStr, Field


from app.api.bearer import oauth2_executive
from app.src.db import Executive, ExecutiveToken, SessionLocal
from app.src.enums import GenderType
from app.src.permissions.executive import PermissionPath
from app.src import argon2, exceptions
from app.src.regex import NAME_PATTERN, PASSWORD_PATTERN, USERNAME_PATTERN
from app.src.urls import URL_EXECUTIVE_ACCOUNT
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    account_to_json,
)


route_executive = APIRouter()


## Output Schema
class ExecutiveSchema(BaseModel):
    """Schema for executive account response."""

    id: int
    username: str
    gender: int
    full_name: str | None
    designation: str | None
    phone_number: str | None
    email_id: str | None
    status: int
    updated_on: datetime | None
    created_on: datetime


## Input Forms
class CreateForm(BaseModel):
    """Form data for creating a new executive account."""

    username: str = Field(min_length=4, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=32, pattern=PASSWORD_PATTERN)
    gender: GenderType = Field(
        description=enum_str(GenderType), default=GenderType.OTHER
    )
    full_name: str | None = Field(
        min_length=1, max_length=32, default=None, pattern=NAME_PATTERN
    )
    designation: str | None = Field(min_length=1, max_length=32, default=None)
    phone_number: PhoneNumber | None = Field(
        max_length=32, default=None, description="Phone number in RFC3966 format"
    )
    email_id: EmailStr | None = Field(
        max_length=256, default=None, description="Email in RFC 5322 format"
    )


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_ACCOUNT,
    tags=["Account"],
    response_model=ExecutiveSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
)
async def create_account(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    """
    **Create a new executive account.**

    - Executive must have a valid access token.
    - Logged-in executive must have 'executive.create' permission.
    - Duplicate usernames are not allowed.
    - By default the user is created in active status.
    """
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.CREATE_EXECUTIVE)

        form_param.password = argon2.make_password(form_param.password)
        executive = Executive(
            username=form_param.username,
            password=form_param.password,
            gender=form_param.gender,
            full_name=form_param.full_name,
            designation=form_param.designation,
            phone_number=form_param.phone_number,
            email_id=form_param.email_id,
        )
        session.add(executive)
        session.commit()
        session.refresh(executive)

        executive_data = account_to_json(executive)
        log_event(token, request_info, executive_data)
        return executive_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
