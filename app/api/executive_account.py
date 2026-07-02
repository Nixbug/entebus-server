"""
Executive Account API router.

Provides endpoints for managing executive accounts:
    - POST (executive)
    - PATCH (executive)
    - DELETE (executive)
    - GET (executive)
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, cast
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, EmailStr, Field
from pydantic_extra_types.phone_numbers import PhoneNumber
from sqlalchemy import String, or_
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive
from app.src.buckets import EXECUTIVE_IMAGES
from app.src.db import Executive, ExecutiveImage, ExecutiveToken, get_db_session
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
from app.src import exceptions, schemas
from app.src.regex import PASSWORD_PATTERN, USERNAME_PATTERN
from app.src.urls import URL_EXECUTIVE_ACCOUNT
from app.src.openobserve import log_event
from app.src.validators import (
    validate_id,
    verify_token,
    authorize_executive,
    verify_permission,
)
from app.src.functions import (
    apply_account_filters,
    apply_created_on_filters,
    apply_id_filters,
    apply_status_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    update_if_changed,
    get_executive_roles,
)
from app.src.description import Description

route_executive = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
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

    password: str | None = Field(
        default=None, min_length=8, max_length=32, pattern=PASSWORD_PATTERN
    )
    gender: GenderType | None = Field(description=enum_str(GenderType), default=None)
    full_name: Annotated[str | None, "nullable"] = Field(
        min_length=1, max_length=32, default=None
    )
    designation: Annotated[str | None, "nullable"] = Field(
        min_length=1, max_length=32, default=None
    )
    phone_number: Annotated[PhoneNumber | None, "nullable"] = Field(
        max_length=32, default=None, description="Phone number in RFC 3966 format"
    )
    email_id: Annotated[EmailStr | None, "nullable"] = Field(
        max_length=256, default=None, description="Email in RFC 5322 format"
    )
    status: AccountStatus | None = Field(description=enum_str(AccountStatus), default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
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
    status_list: list[AccountStatus] | None = Field(
        Query(default=None, description=enum_str(AccountStatus))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_executive(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new executive account in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new executive account.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created executive account data.
    """
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

    executive_data = executive_to_dict(executive)
    log_event(token, request_info, executive_data)
    return executive_data


def update_executive(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Update an existing executive account in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the executive account to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Updated executive account data.
    """
    executive = validate_id(session, Executive, id, Executive.id)

    update_data = form_param.model_dump(exclude_unset=True)
    if "status" in update_data:
        if form_param.status == AccountStatus.SUSPENDED:
            session.query(ExecutiveToken).filter(
                ExecutiveToken.executive_id == id,
                ExecutiveToken.is_revoked.is_(False),
            ).update({ExecutiveToken.is_revoked: True})
        executive.status = form_param.status
        update_data.pop("status")

    update_if_changed(executive, update_data)
    if session.is_modified(executive):
        session.commit()
        session.refresh(executive)
        executive_data = executive_to_dict(executive)
        log_event(token, request_info, executive_data)
    else:
        executive_data = executive_to_dict(executive)
    return executive_data


def search_executives(session: Session, query_params: QueryParams) -> list[Executive]:
    """
    Search for executive accounts based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve executive accounts that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Executive]: List of executive accounts that match the search criteria.
    """
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


def delete_executive(
    session: Session,
    id: int,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
):
    """
    Delete an executive account from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the executive account to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    executive = session.query(Executive).filter(Executive.id == id).first()
    if executive is None:
        return

    executive_images = (
        session.query(ExecutiveImage)
        .filter(ExecutiveImage.executive_id == id)
        .all()
    )
    executive_data = executive_to_dict(executive)
    session.delete(executive)
    session.commit()

    # Delete executive images from object storage.
    for executive_image in executive_images:
        delete_file(EXECUTIVE_IMAGES, str(executive_image.id))

    log_event(token, request_info, executive_data)


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Executive.id),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new executive account.")
    .add_line("Duplicate usernames are not allowed.")
    .add_line("By default the user is created in active status.")
    .add_line("Logged-in executive must have the `executive.create` permission.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing executive account.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line("Executive can update their own account except status.")
    .add_line(
        "Logged-in executive must have `executive.update` permission to update other executives."
    )
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing executive account.")
    .add_line("Self-deletion is not allowed for safety reasons.")
    .add_line("Returns 204 No Content even if the specified account does not exist.")
    .add_line("Logged-in executive must have the `executive.delete` permission.")
    .add_line("All associated executive images will be deleted upon account deletion.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of executives.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_ACCOUNT,
    summary="Create executive account",
    tags=["Account"],
    response_model=ExecutiveSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_executive_account_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = cast(
            ExecutiveToken,
            authorize_executive(
                session, access_token, [PermissionPath.CREATE_EXECUTIVE]
            ),
        )
        return create_executive(session, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_EXECUTIVE_ACCOUNT}/{{id}}",
    summary="Update executive account",
    tags=["Account"],
    response_model=ExecutiveSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(PATCH_DESCRIPTION.to_string()),
)
async def update_executive_account_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = cast(ExecutiveToken, verify_token(session, ExecutiveToken, access_token))

        is_self_update = id == token.executive_id
        if not is_self_update:
            roles = get_executive_roles(session, token)
            verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE)
        if is_self_update and form_param.status is not None:
            raise exceptions.NoPermission()

        return update_executive(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_EXECUTIVE_ACCOUNT}/{{id}}",
    summary="Delete executive account",
    tags=["Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(DELETE_DESCRIPTION.to_string()),
)
async def delete_executive_account_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = cast(
            ExecutiveToken,
            authorize_executive(
                session, access_token, [PermissionPath.DELETE_EXECUTIVE]
            ),
        )

        if token.executive_id == id:
            raise exceptions.NoPermission()

        delete_executive(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_EXECUTIVE_ACCOUNT,
    summary="Fetch executive account",
    tags=["Account"],
    response_model=list[ExecutiveSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_executive_accounts_for_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return [executive_to_dict(executive) for executive in search_executives(session, query_params)]
    except Exception as e:
        exceptions.handle(e)
