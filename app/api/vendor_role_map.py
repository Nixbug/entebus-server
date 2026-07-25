"""
Vendor Role Map API router.

Provides endpoints for managing vendor role maps:
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
from sqlalchemy.sql import ColumnElement
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_vendor, oauth2_executive
from app.src import exceptions, schemas
from app.src.db import (
    ExecutiveToken,
    Vendor,
    VendorRole,
    VendorRoleMap,
    VendorToken,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, UpdatedOnFilter
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src.schemas import PatchForm
from app.src.urls import URL_VENDOR_ROLE_MAP
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
class VendorRoleMapSchema(BaseModel):
    """Schema for vendor role mapping response."""

    id: int
    business_id: int
    role_id: int
    vendor_id: int
    created_on: datetime
    updated_on: datetime | None


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForVE(BaseModel):
    """Form data for creating a new vendor role mapping for a vendor."""

    role_id: int = Field()
    vendor_id: int = Field()


class CreateFormForEX(CreateFormForVE):
    """Form data for creating a new vendor role mapping for an executive."""

    business_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new vendor role mapping."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a vendor role mapping."""

    role_id: int | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForVE(PaginationFilter, IDFilter, CreatedOnFilter, UpdatedOnFilter):
    """Query parameters for vendors."""

    role_id: int | None = Field(Query(default=None))
    vendor_id: int | None = Field(Query(default=None))
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
def create_vendor_role_map(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
    vendor_filter: ColumnElement[bool] | None = None,
    role_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Creates a new vendor role mapping with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new vendor role mapping.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        vendor_filter: Additional filter for validating vendor ownership.
        role_filter: Additional filter for validating role ownership.

    Returns:
        dict: Created vendor role mapping data.
    """
    vendor = validate_id(
        session,
        Vendor,
        form_param.vendor_id,
        VendorRoleMap.vendor_id,
        extra_filter=vendor_filter,
    )
    vendor_role = validate_id(
        session,
        VendorRole,
        form_param.role_id,
        VendorRoleMap.role_id,
        extra_filter=role_filter,
    )

    vendor_role_map = VendorRoleMap(
        business_id=form_param.business_id,
        role_id=vendor_role.id,
        vendor_id=vendor.id,
    )
    session.add(vendor_role_map)
    session.commit()
    session.refresh(vendor_role_map)

    vendor_role_map_data = jsonable_encoder(vendor_role_map)
    log_event(token, request_info, vendor_role_map_data)
    return vendor_role_map_data


def update_vendor_role_map(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
    role_map_filter: ColumnElement[bool] | None = None,
    role_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Updates a vendor role mapping with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the vendor role mapping to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        role_map_filter: Additional filter for validating role map ownership.
        role_filter: Additional filter for validating role ownership.

    Returns:
        dict: Updated vendor role mapping data.
    """
    vendor_role_map = validate_id(
        session,
        VendorRoleMap,
        id,
        VendorRoleMap.id,
        extra_filter=role_map_filter,
    )

    if isinstance(token, ExecutiveToken):
        role_filter = VendorRole.business_id == vendor_role_map.business_id

    update_data = form_param.model_dump(exclude_unset=True)
    if "role_id" in update_data:
        if vendor_role_map.role_id != update_data["role_id"]:
            vendor_role = validate_id(
                session,
                VendorRole,
                update_data["role_id"],
                VendorRoleMap.role_id,
                extra_filter=role_filter,
            )
            vendor_role_map.role_id = vendor_role.id
        update_data.pop("role_id")

    if session.is_modified(vendor_role_map):
        session.commit()
        session.refresh(vendor_role_map)
        vendor_role_map_data = jsonable_encoder(vendor_role_map)
        log_event(token, request_info, vendor_role_map_data)
    else:
        vendor_role_map_data = jsonable_encoder(vendor_role_map)
    return vendor_role_map_data


def delete_vendor_role_map(
    session: Session,
    id: int,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
    role_map_filter: ColumnElement[bool] | None = None,
) -> None:
    """
    Deletes a vendor role mapping from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the vendor role mapping to delete.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        role_map_filter: Additional filter for role map ownership.
    """
    vendor_role_map = get_by_id(
        session, VendorRoleMap, id, extra_filter=role_map_filter
    )
    if vendor_role_map is None:
        return

    vendor_role_map_data = jsonable_encoder(vendor_role_map)
    session.delete(vendor_role_map)
    session.commit()
    log_event(token, request_info, vendor_role_map_data)


def search_vendor_role_maps(
    session: Session, query_params: QueryParams
) -> list[VendorRoleMap]:
    """
    Searches for vendor role mappings based on the provided query parameters.

    This function supports multiple filtering, ordering, and pagination capabilities
    to retrieve vendor role mappings that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[VendorRoleMap]: List of vendor role mappings that match the search criteria.
    """
    query = session.query(VendorRoleMap)
    if query_params.business_id is not None:
        query = query.filter(VendorRoleMap.business_id == query_params.business_id)
    if query_params.role_id is not None:
        query = query.filter(VendorRoleMap.role_id == query_params.role_id)
    if query_params.vendor_id is not None:
        query = query.filter(VendorRoleMap.vendor_id == query_params.vendor_id)

    # Generalized filters
    query = apply_id_filters(query, VendorRoleMap, query_params)
    query = apply_created_on_filters(query, VendorRoleMap, query_params)
    query = apply_updated_on_filters(query, VendorRoleMap, query_params)

    # Ordering and pagination
    ordering_attr = getattr(VendorRoleMap, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    vendor_role_maps = query.all()
    return vendor_role_maps


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(VendorRoleMap.vendor_id),
    exceptions.UnknownValue(VendorRoleMap.role_id),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(VendorRoleMap.id),
    exceptions.UnknownValue(VendorRoleMap.role_id),
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
    .add_head("Creates a new vendor role mapping.")
    .add_line("Duplicate mappings are not allowed.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing vendor role mapping.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line("Duplicate mappings are not allowed.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing vendor role mapping.")
    .add_line(
        "Returns 204 No Content even if the specified role mapping does not exist."
    )
)

GET_DESCRIPTION = Description().add_head("Fetches a list of vendor role mappings.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VENDOR_ROLE_MAP,
    summary="Create vendor role map",
    tags=["Vendor Role Map"],
    response_model=VendorRoleMapSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `business.vendor.role.update` permission."
        )
        .add_line(
            "`business_id` is required and used to validate vendor and role ownership."
        )
        .to_string()
    ),
)
async def create_vendor_role_map_for_executive(
    form_param: CreateFormForEX,
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
        return create_vendor_role_map(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
            vendor_filter=(Vendor.business_id == form_param.business_id),
            role_filter=(VendorRole.business_id == form_param.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_VENDOR_ROLE_MAP}/{{id}}",
    summary="Update vendor role map",
    tags=["Vendor Role Map"],
    response_model=VendorRoleMapSchema,
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
async def update_vendor_role_map_for_executive(
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
        return update_vendor_role_map(
            session,
            id,
            form_param,
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_VENDOR_ROLE_MAP}/{{id}}",
    summary="Delete vendor role map",
    tags=["Vendor Role Map"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `business.vendor.role.update` permission."
        )
        .to_string()
    ),
)
async def delete_vendor_role_map_for_executive(
    id: int,
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
        delete_vendor_role_map(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_VENDOR_ROLE_MAP,
    summary="Fetch vendor role map",
    tags=["Vendor Role Map"],
    response_model=list[VendorRoleMapSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vendor_role_maps_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_vendor_role_maps(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.post(
    URL_VENDOR_ROLE_MAP,
    summary="Create vendor role map",
    tags=["Role Map"],
    response_model=VendorRoleMapSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in vendor must have `business.vendor.role.update` permission."
        )
        .to_string()
    ),
)
async def create_vendor_role_map_for_vendor(
    form_param: CreateFormForVE,
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
        return create_vendor_role_map(
            session,
            CreateForm(**form_param.model_dump(), business_id=token.business_id),
            token,
            request_info,
            vendor_filter=(Vendor.business_id == token.business_id),
            role_filter=(VendorRole.business_id == token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.patch(
    f"{URL_VENDOR_ROLE_MAP}/{{id}}",
    summary="Update vendor role map",
    tags=["Role Map"],
    response_model=VendorRoleMapSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in vendor must have `business.vendor.role.update` permission."
        )
        .to_string()
    ),
)
async def update_vendor_role_map_for_vendor(
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
        return update_vendor_role_map(
            session,
            id,
            form_param,
            token,
            request_info,
            role_map_filter=(VendorRoleMap.business_id == token.business_id),
            role_filter=(VendorRole.business_id == token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.delete(
    f"{URL_VENDOR_ROLE_MAP}/{{id}}",
    summary="Delete vendor role map",
    tags=["Role Map"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in vendor must have the `business.vendor.role.update` permission."
        )
        .to_string()
    ),
)
async def delete_vendor_role_map_for_vendor(
    id: int,
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
        delete_vendor_role_map(
            session,
            id,
            token,
            request_info,
            role_map_filter=(VendorRoleMap.business_id == token.business_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_vendor.get(
    URL_VENDOR_ROLE_MAP,
    summary="Fetch vendor role map",
    tags=["Role Map"],
    response_model=list[VendorRoleMapSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vendor_role_maps_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        return search_vendor_role_maps(
            session,
            QueryParams(**query_params.model_dump(), business_id=token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)
