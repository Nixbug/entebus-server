"""
Executive Account API Router for EnteBus.

Provides endpoints for managing executive accounts, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from typing import List
from fastapi import APIRouter, Query, Response, status, Depends
from pydantic_extra_types.phone_numbers import PhoneNumber
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import String, or_

from app.api.bearer import oauth2_executive
from app.src.buckets import EXECUTIVE_IMAGES
from app.src.db import Executive, ExecutiveImage, ExecutiveToken, SessionLocal
from app.src.enums import AccountStatus, GenderType, OrderIn
from app.src.filters import (
    AccountDataFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.minio import delete_file
from app.src.permissions.executive import PermissionPath
from app.src import argon2, exceptions
from app.src.regex import PASSWORD_PATTERN, USERNAME_PATTERN
from app.src.urls import URL_EXECUTIVE_ACCOUNT
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    apply_account_filters,
    apply_created_on_filters,
    apply_id_filters,
    apply_status_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    orm_to_json,
    update_if_changed,
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
    full_name: str | None = Field(min_length=1, max_length=32, default=None)
    designation: str | None = Field(min_length=1, max_length=32, default=None)
    phone_number: PhoneNumber | None = Field(
        max_length=32, default=None, description="Phone number in RFC 3966 format"
    )
    email_id: EmailStr | None = Field(
        max_length=256, default=None, description="Email in RFC 5322 format"
    )


class UpdateForm(BaseModel):
    """Form data for updating an executive account."""

    password: str = Field(
        default=None, min_length=8, max_length=32, pattern=PASSWORD_PATTERN
    )
    gender: GenderType = Field(description=enum_str(GenderType), default=None)
    full_name: str | None = Field(min_length=1, max_length=32, default=None)
    designation: str | None = Field(min_length=1, max_length=32, default=None)
    phone_number: PhoneNumber | None = Field(
        max_length=32, default=None, description="Phone number in RFC 3966 format"
    )
    email_id: EmailStr | None = Field(
        max_length=256, default=None, description="Email in RFC 5322 format"
    )
    status: AccountStatus = Field(description=enum_str(AccountStatus), default=None)


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParams(
    AccountDataFilter,
    UpdatedOnFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
):
    """Query parameters for fetching executive accounts."""

    search: str | None = Field(Query(default=None))
    designation: str | None = Field(Query(default=None))
    status_list: List[AccountStatus] | None = Field(
        Query(default=None, description=enum_str(AccountStatus))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
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

        _, executive_data = orm_to_json(executive, [Executive.password.key])
        log_event(token, request_info, executive_data)
        return executive_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_EXECUTIVE_ACCOUNT}/{{id}}",
    tags=["Account"],
    response_model=ExecutiveSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Executive.id),
        ]
    ),
)
async def update_account(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    """
    **Update an existing executive account.**

    - Requires a valid access token.
    - Logged-in executive must have `executive.update` permission to update other executives.
    - Executive can update their own account except status.
    - Empty PATCH requests are allowed and will result in no changes.
    """
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)

        executive = session.query(Executive).filter(Executive.id == id).first()
        if executive is None:
            raise exceptions.UnknownValue(Executive.id)
        update_data = form_param.model_dump(exclude_unset=True)
        is_self_update = executive.id == token.executive_id
        if not is_self_update:
            roles = get_executive_roles(session, token)
            verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE)
        if is_self_update and Executive.status.key in update_data:
            raise exceptions.NoPermission()

        # Revoking all the tokens for a suspended executive
        tokens_revoked = False
        if form_param.status == AccountStatus.SUSPENDED:
            tokens_revoked = (
                session.query(ExecutiveToken)
                .filter(
                    ExecutiveToken.executive_id == id,
                    ExecutiveToken.is_revoked.is_(False),
                )
                .update({ExecutiveToken.is_revoked: True})
                > 0
            )
        update_if_changed(executive, update_data)
        have_updates = session.is_modified(executive) or tokens_revoked
        if have_updates:
            session.commit()
            session.refresh(executive)

        _, executive_data = orm_to_json(executive, [Executive.password.key])
        if have_updates:
            log_event(token, request_info, executive_data)
        return executive_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_EXECUTIVE_ACCOUNT}/{{id}}",
    tags=["Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
)
async def delete_account(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    """
    **Deletes an existing executive account.**

    - Requires a valid access token for authentication.
    - The logged-in executive must have the `executive.delete` permission.
    - Self-deletion is not allowed for safety reasons.
    - Returns `204 No Content` even if the specified account does not exist.
    """
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.DELETE_EXECUTIVE)

        if token.executive_id == id:
            raise exceptions.NoPermission()
        executive = session.query(Executive).filter(Executive.id == id).first()
        if executive is not None:
            executive_image = (
                session.query(ExecutiveImage)
                .filter(ExecutiveImage.executive_id == id)
                .first()
            )
            session.delete(executive)
            session.commit()
            # Delete executive image
            if executive_image is not None:
                delete_file(EXECUTIVE_IMAGES, str(executive_image.id))

            _, executive_data = orm_to_json(executive, [Executive.password.key])
            log_event(token, request_info, executive_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_EXECUTIVE_ACCOUNT,
    tags=["Account"],
    response_model=list[ExecutiveSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
)
async def fetch_account(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    """
    **Fetch executive account.**

    - Requires a valid access token for authentication.
    - Common search supports searching by id, username, full_name, designation, phone_number, and email_id.
    """
    session = SessionLocal()
    try:
        verify_token(session, ExecutiveToken, access_token)

        query = session.query(Executive)

        if query_params.designation is not None:
            query = query.filter(
                Executive.designation.ilike(f"%{query_params.designation}%")
            )
        # Common search
        if query_params.search:
            search = f"%{query_params.search}%"
            query = query.filter(
                or_(
                    Executive.id.cast(String).ilike(search),
                    Executive.username.ilike(search),
                    Executive.full_name.ilike(search),
                    Executive.designation.ilike(search),
                    Executive.phone_number.ilike(search),
                    Executive.email_id.ilike(search),
                )
            )
        # Generalized filters
        query = apply_id_filters(query, Executive, query_params)
        query = apply_created_on_filters(query, Executive, query_params)
        query = apply_updated_on_filters(query, Executive, query_params)
        query = apply_account_filters(query, Executive, query_params)
        query = apply_status_filters(query, Executive, query_params)

        # Ordering and pagination
        ordering_attr = getattr(Executive, query_params.order_by.value)
        ordering_func = (
            ordering_attr.asc
            if query_params.order_in == OrderIn.ASCENDING
            else ordering_attr.desc
        )
        query = query.order_by(ordering_func())
        query = query.offset(query_params.offset).limit(query_params.limit)

        executives = query.all()
        return executives
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
