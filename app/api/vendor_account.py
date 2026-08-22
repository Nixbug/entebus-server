"""
Vendor Account API router.

Provides endpoints for managing vendor accounts:
    - POST (executive, vendor)
    - PATCH (executive, vendor)
    - DELETE (executive, vendor)
    - GET (executive, vendor)
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, EmailStr, Field
from pydantic_extra_types.phone_numbers import PhoneNumber
from sqlalchemy import String, or_
from sqlalchemy.sql import ColumnElement
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_vendor, oauth2_executive
from app.src import exceptions, schemas
from app.src.buckets import VENDOR_IMAGES
from app.src.constants import MAX_VENDORS_PER_COMPANY
from app.src.db import (
    ExecutiveToken,
    Vendor,
    VendorImage,
    VendorToken,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import AccountStatus, GenderType, OrderIn, VendorType
from app.src.filters import (
    AccountDataFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.functions import (
    apply_account_filters,
    apply_created_on_filters,
    apply_id_filters,
    apply_status_filters,
    apply_type_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    get_vendor_roles,
    update_if_changed,
)
from app.src.minio import delete_file
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src.regex import PASSWORD_PATTERN, USERNAME_PATTERN
from app.src.schemas import PatchForm
from app.src.urls import URL_VENDOR_ACCOUNT
from app.src.validators import (
    authorize_executive,
    authorize_vendor,
    validate_id,
    verify_permission,
    verify_token,
)

route_executive = APIRouter()
route_vendor = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class VendorSchema(BaseModel):
    """Schema for vendor account response."""

    id: int
    business_id: int
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


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForVE(BaseModel):
    """Form data for creating a new vendor account for a vendor."""

    username: str = Field(min_length=4, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=32, pattern=PASSWORD_PATTERN)
    gender: GenderType = Field(
        description=enum_str(GenderType),
        default=GenderType.OTHER,
    )
    description: str | None = Field(min_length=1, max_length=1024, default=None)
    type: VendorType = Field(
        description=enum_str(VendorType),
        default=VendorType.NORMAL,
    )
    full_name: str | None = Field(min_length=1, max_length=32, default=None)
    status: AccountStatus = Field(
        description=enum_str(AccountStatus),
        default=AccountStatus.ACTIVE,
    )
    phone_number: PhoneNumber | None = Field(
        max_length=32,
        default=None,
        description="Phone number in RFC 3966 format",
    )
    email_id: EmailStr | None = Field(
        max_length=256,
        default=None,
        description="Email in RFC 5322 format",
    )


class CreateFormForEX(CreateFormForVE):
    """Form data for creating a new vendor account for an executive."""

    business_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new vendor account."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a vendor account."""

    password: str | None = Field(
        default=None,
        min_length=8,
        max_length=32,
        pattern=PASSWORD_PATTERN,
    )
    gender: GenderType | None = Field(description=enum_str(GenderType), default=None)
    description: Annotated[str | None, "nullable"] = Field(
        min_length=1,
        max_length=1024,
        default=None,
    )
    type: VendorType | None = Field(description=enum_str(VendorType), default=None)
    full_name: Annotated[str | None, "nullable"] = Field(
        min_length=1,
        max_length=32,
        default=None,
    )
    status: AccountStatus | None = Field(
        description=enum_str(AccountStatus),
        default=None,
    )
    phone_number: Annotated[PhoneNumber | None, "nullable"] = Field(
        max_length=32,
        default=None,
        description="Phone number in RFC 3966 format",
    )
    email_id: Annotated[EmailStr | None, "nullable"] = Field(
        max_length=256,
        default=None,
        description="Email in RFC 5322 format",
    )


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForVE(
    AccountDataFilter, UpdatedOnFilter, CreatedOnFilter, IDFilter, PaginationFilter
):
    """Query parameters for vendors."""

    search: str | None = Field(Query(default=None))
    description: str | None = Field(Query(default=None))
    type_list: list[VendorType] | None = Field(
        Query(default=None, description=enum_str(VendorType))
    )
    status_list: list[AccountStatus] | None = Field(
        Query(default=None, description=enum_str(AccountStatus))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForVE):
    """Query parameters for executives."""

    business_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
def vendor_account_to_dict(vendor: Vendor) -> dict:
    """
    Convert a Vendor account to a dictionary representation.

    Args:
        vendor (Vendor): The vendor object to convert.

    Returns:
        dict: A dictionary representation of the vendor object without password.
    """
    vendor_account_dict = jsonable_encoder(vendor, exclude={Vendor.password.name})
    return vendor_account_dict


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_vendor(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new vendor account in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new vendor account.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created vendor account data.
    """
    vendor_count = (
        session.query(Vendor)
        .filter(Vendor.business_id == form_param.business_id)
        .count()
    )
    if vendor_count >= MAX_VENDORS_PER_COMPANY:
        raise exceptions.LimitExceeded(Vendor)

    vendor = Vendor(
        business_id=form_param.business_id,
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
    session.add(vendor)
    session.commit()
    session.refresh(vendor)

    vendor_account_data = vendor_account_to_dict(vendor)
    log_event(token, request_info, vendor_account_data)
    return vendor_account_data


def update_vendor(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
    vendor_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Update an existing vendor account in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the vendor account to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        vendor_filter: Additional filter for validating vendor ownership.

    Returns:
        dict: Updated vendor account data.
    """
    vendor = validate_id(
        session,
        Vendor,
        id,
        Vendor.id,
        extra_filter=vendor_filter,
    )

    update_data = form_param.model_dump(exclude_unset=True)
    if "status" in update_data:
        if vendor.status != update_data["status"]:
            if update_data["status"] == AccountStatus.SUSPENDED:
                session.query(VendorToken).filter(
                    VendorToken.vendor_id == id,
                    VendorToken.is_revoked.is_(False),
                ).update({VendorToken.is_revoked: True})
            vendor.status = update_data["status"]
        update_data.pop("status")

    update_if_changed(vendor, update_data)
    if session.is_modified(vendor):
        session.commit()
        session.refresh(vendor)
        vendor_account_data = vendor_account_to_dict(vendor)
        log_event(token, request_info, vendor_account_data)
    else:
        vendor_account_data = vendor_account_to_dict(vendor)
    return vendor_account_data


def search_vendors(session: Session, query_params: QueryParams) -> list[Vendor]:
    """
    Search for vendor accounts based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve vendors that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Vendor]: List of vendors that match the search criteria.
    """
    query = session.query(Vendor)
    if query_params.business_id is not None:
        query = query.filter(Vendor.business_id == query_params.business_id)
    if query_params.description is not None:
        query = query.filter(Vendor.description.ilike(f"%{query_params.description}%"))

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Vendor.id.cast(String).ilike(search),
                Vendor.username.ilike(search),
                Vendor.full_name.ilike(search),
                Vendor.description.ilike(search),
                Vendor.phone_number.ilike(search),
                Vendor.email_id.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Vendor, query_params)
    query = apply_created_on_filters(query, Vendor, query_params)
    query = apply_updated_on_filters(query, Vendor, query_params)
    query = apply_account_filters(query, Vendor, query_params)
    query = apply_status_filters(query, Vendor, query_params)
    query = apply_type_filters(query, Vendor, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Vendor, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    vendors = query.all()
    return vendors


def delete_vendor(
    session: Session,
    id: int,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
    vendor_filter: ColumnElement[bool] | None = None,
):
    """
    Delete a vendor account from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the vendor account to delete.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        vendor_filter: Additional filter for vendor ownership.
    """
    vendor = get_by_id(session, Vendor, id, extra_filter=vendor_filter)
    if vendor is None:
        return

    vendor_images = (
        session.query(VendorImage).filter(VendorImage.vendor_id == vendor.id).all()
    )
    vendor_account_data = vendor_account_to_dict(vendor)
    session.delete(vendor)
    session.commit()

    # Delete vendor images from object storage.
    for vendor_image in vendor_images:
        delete_file(VENDOR_IMAGES, str(vendor_image.id))

    log_event(token, request_info, vendor_account_data)


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.LimitExceeded(Vendor),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Vendor.id),
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
    .add_head("Creates a new vendor account.")
    .add_line("Duplicate usernames are not allowed.")
    .add_line("By default the user is created in active status.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing vendor account.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing vendor account.")
    .add_line("Returns 204 No Content even if the specified account does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of vendors.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VENDOR_ACCOUNT,
    summary="Create vendor account",
    tags=["Vendor Account"],
    response_model=VendorSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `business.vendor.create` permission.")
        .to_string()
    ),
)
async def create_vendor_account_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_BUSINESS_VENDOR],
        )
        return create_vendor(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_VENDOR_ACCOUNT}/{{id}}",
    summary="Update vendor account",
    tags=["Vendor Account"],
    response_model=VendorSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `business.vendor.update` permission.")
        .to_string()
    ),
)
async def update_vendor_account_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_BUSINESS_VENDOR],
        )
        return update_vendor(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_VENDOR_ACCOUNT,
    summary="Fetch vendor account",
    tags=["Vendor Account"],
    response_model=list[VendorSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vendor_accounts_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_vendors(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_VENDOR_ACCOUNT}/{{id}}",
    summary="Delete vendor account",
    tags=["Vendor Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `business.vendor.delete` permission."
        )
        .to_string()
    ),
)
async def delete_vendor_account_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_BUSINESS_VENDOR],
        )
        delete_vendor(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.post(
    URL_VENDOR_ACCOUNT,
    summary="Create vendor account",
    tags=["Account"],
    response_model=VendorSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in vendor must have `business.vendor.create` permission.")
        .to_string()
    ),
)
async def create_vendor_account_for_vendor(
    form_param: CreateFormForVE,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_vendor(
            session,
            access_token.credentials,
            [VendorPermissionPath.CREATE_BUSINESS_VENDOR],
        )
        return create_vendor(
            session,
            CreateForm(**form_param.model_dump(), business_id=token.business_id),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.patch(
    f"{URL_VENDOR_ACCOUNT}/{{id}}",
    summary="Update vendor account",
    tags=["Account"],
    response_model=VendorSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in vendor must have `business.vendor.update` permission to update other vendors."
        )
        .add_line("Vendors can update their own account except status.")
        .to_string()
    ),
)
async def update_vendor_account_for_vendor(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)

        is_self_update = id == token.vendor_id
        if not is_self_update:
            roles = get_vendor_roles(session, token)
            verify_permission(roles, VendorPermissionPath.UPDATE_BUSINESS_VENDOR)
        if is_self_update and form_param.status is not None:
            raise exceptions.NoPermission()

        return update_vendor(
            session,
            id,
            form_param,
            token,
            request_info,
            vendor_filter=(Vendor.business_id == token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.get(
    URL_VENDOR_ACCOUNT,
    summary="Fetch vendor account",
    tags=["Account"],
    response_model=list[VendorSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vendor_accounts_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        return search_vendors(
            session,
            QueryParams(**query_params.model_dump(), business_id=token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.delete(
    f"{URL_VENDOR_ACCOUNT}/{{id}}",
    summary="Delete vendor account",
    tags=["Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in vendor must have the `business.vendor.delete` permission."
        )
        .add_line("Self-deletion is not allowed for vendors.")
        .to_string()
    ),
)
async def delete_vendor_account_for_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_vendor(
            session,
            access_token.credentials,
            [VendorPermissionPath.DELETE_BUSINESS_VENDOR],
        )
        if token.vendor_id == id:
            raise exceptions.NoPermission()

        delete_vendor(
            session,
            id,
            token,
            request_info,
            vendor_filter=(Vendor.business_id == token.business_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
