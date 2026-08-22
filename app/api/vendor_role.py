"""
Vendor Role API router.

Provides endpoints for managing vendor roles:
    - POST (executive, vendor)
    - PATCH (executive, vendor)
    - DELETE (executive, vendor)
    - GET (executive, vendor)
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import String, or_
from sqlalchemy.sql import ColumnElement
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_vendor, oauth2_executive
from app.src import exceptions, schemas
from app.src.constants import MAX_VENDOR_ROLE
from app.src.db import (
    ExecutiveToken,
    VendorRole,
    VendorToken,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    NameFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_name_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    update_if_changed,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.vendor import (
    PermissionPath as VendorPermissionPath,
    PermissionSchema,
)
from app.src.regex import NAME_PATTERN
from app.src.schemas import PatchForm
from app.src.urls import URL_VENDOR_ROLE
from app.src.validators import (
    authorize_executive,
    authorize_vendor,
    validate_id,
    verify_token,
)

route_executive = APIRouter()
route_vendor = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class VendorRoleSchema(BaseModel):
    """Schema for vendor role response."""

    id: int
    business_id: int
    name: str
    permissions: PermissionSchema
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForVE(BaseModel):
    """Form data for creating a new vendor role for a vendor."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    permissions: PermissionSchema


class CreateFormForEX(CreateFormForVE):
    """Form data for creating a new vendor role for an executive."""

    business_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new vendor role."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a vendor role."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=NAME_PATTERN,
    )
    permissions: PermissionSchema | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForVE(
    UpdatedOnFilter, CreatedOnFilter, NameFilter, IDFilter, PaginationFilter
):
    """Query parameters for vendors."""

    search: str | None = Field(Query(default=None))
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
## Core Functions
# ---------------------------------------------------------------------------
def create_vendor_role(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Creates a new vendor role with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new vendor role.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created vendor role data.
    """
    role_count = (
        session.query(VendorRole)
        .filter(VendorRole.business_id == form_param.business_id)
        .count()
    )
    if role_count >= MAX_VENDOR_ROLE:
        raise exceptions.LimitExceeded(VendorRole)

    vendor_role = VendorRole(
        business_id=form_param.business_id,
        name=form_param.name,
        permissions=form_param.permissions.model_dump(),
    )
    session.add(vendor_role)
    session.commit()
    session.refresh(vendor_role)

    vendor_role_data = jsonable_encoder(vendor_role)
    log_event(token, request_info, vendor_role_data)
    return vendor_role_data


def update_vendor_role(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
    role_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Updates a vendor role with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the vendor role to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        role_filter: Additional filter to apply when validating role ID.

    Returns:
        dict: Updated vendor role data.
    """
    vendor_role = validate_id(
        session,
        VendorRole,
        id,
        VendorRole.id,
        extra_filter=role_filter,
    )

    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(vendor_role, update_data)
    if session.is_modified(vendor_role):
        session.commit()
        session.refresh(vendor_role)
        vendor_role_data = jsonable_encoder(vendor_role)
        log_event(token, request_info, vendor_role_data)
    else:
        vendor_role_data = jsonable_encoder(vendor_role)
    return vendor_role_data


def delete_vendor_role(
    session: Session,
    id: int,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
    role_filter: ColumnElement[bool] | None = None,
) -> None:
    """
    Deletes a vendor role from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the vendor role to delete.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        role_filter: Additional filter to apply when fetching role.
    """
    vendor_role = get_by_id(session, VendorRole, id, extra_filter=role_filter)
    if vendor_role is None:
        return

    vendor_role_data = jsonable_encoder(vendor_role)
    session.delete(vendor_role)
    session.commit()
    log_event(token, request_info, vendor_role_data)


def search_vendor_roles(
    session: Session, query_params: QueryParams
) -> list[VendorRole]:
    """
    Searches for vendor roles based on the provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve vendor roles that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[VendorRole]: List of vendor roles that match the search criteria.
    """
    query = session.query(VendorRole)
    if query_params.business_id is not None:
        query = query.filter(VendorRole.business_id == query_params.business_id)

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                VendorRole.id.cast(String).ilike(search),
                VendorRole.name.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, VendorRole, query_params)
    query = apply_created_on_filters(query, VendorRole, query_params)
    query = apply_updated_on_filters(query, VendorRole, query_params)
    query = apply_name_filters(query, VendorRole, query_params)

    # Ordering and pagination
    ordering_attr = getattr(VendorRole, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    vendor_roles = query.all()
    return vendor_roles


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.LimitExceeded(VendorRole),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(VendorRole.id),
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
    .add_head("Creates a new vendor role.")
    .add_line("Duplicate names are not allowed.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing vendor role.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing vendor role.")
    .add_line("Returns 204 No Content even if the specified role does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of vendor roles.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VENDOR_ROLE,
    summary="Create vendor role",
    tags=["Vendor Role"],
    response_model=VendorRoleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `business.vendor.role.create` permission."
        )
        .to_string()
    ),
)
async def create_vendor_role_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_BUSINESS_VENDOR_ROLE],
        )
        return create_vendor_role(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_VENDOR_ROLE}/{{id}}",
    summary="Update vendor role",
    tags=["Vendor Role"],
    response_model=VendorRoleSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `business.vendor.role.update` permission."
        )
        .to_string()
    ),
)
async def update_vendor_role_for_executive(
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
            [ExecutivePermissionPath.UPDATE_BUSINESS_VENDOR_ROLE],
        )
        return update_vendor_role(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_VENDOR_ROLE}/{{id}}",
    summary="Delete vendor role",
    tags=["Vendor Role"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `business.vendor.role.delete` permission."
        )
        .to_string()
    ),
)
async def delete_vendor_role_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_BUSINESS_VENDOR_ROLE],
        )
        delete_vendor_role(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_VENDOR_ROLE,
    summary="Fetch vendor role",
    tags=["Vendor Role"],
    response_model=list[VendorRoleSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vendor_roles_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_vendor_roles(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.post(
    URL_VENDOR_ROLE,
    summary="Create vendor role",
    tags=["Role"],
    response_model=VendorRoleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in vendor must have `business.vendor.role.create` permission."
        )
        .to_string()
    ),
)
async def create_vendor_role_for_vendor(
    form_param: CreateFormForVE,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_vendor(
            session,
            access_token.credentials,
            [VendorPermissionPath.CREATE_BUSINESS_VENDOR_ROLE],
        )
        return create_vendor_role(
            session,
            CreateForm(**form_param.model_dump(), business_id=token.business_id),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.patch(
    f"{URL_VENDOR_ROLE}/{{id}}",
    summary="Update vendor role",
    tags=["Role"],
    response_model=VendorRoleSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in vendor must have `business.vendor.role.update` permission."
        )
        .add_line("Vendors can update roles within their own business.")
        .to_string()
    ),
)
async def update_vendor_role_for_vendor(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_vendor(
            session,
            access_token.credentials,
            [VendorPermissionPath.UPDATE_BUSINESS_VENDOR_ROLE],
        )
        return update_vendor_role(
            session,
            id,
            form_param,
            token,
            request_info,
            role_filter=(VendorRole.business_id == token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.delete(
    f"{URL_VENDOR_ROLE}/{{id}}",
    summary="Delete vendor role",
    tags=["Role"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in vendor must have the `business.vendor.role.delete` permission."
        )
        .to_string()
    ),
)
async def delete_vendor_role_for_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_vendor(
            session,
            access_token.credentials,
            [VendorPermissionPath.DELETE_BUSINESS_VENDOR_ROLE],
        )
        delete_vendor_role(
            session,
            id,
            token,
            request_info,
            role_filter=(VendorRole.business_id == token.business_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_vendor.get(
    URL_VENDOR_ROLE,
    summary="Fetch vendor role",
    tags=["Role"],
    response_model=list[VendorRoleSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vendor_roles_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        return search_vendor_roles(
            session,
            QueryParams(**query_params.model_dump(), business_id=token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)
