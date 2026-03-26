"""
Vendor Account API Router for EnteBus.

Provides endpoints for managing vendor accounts, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from enum import StrEnum
from typing import List, Tuple
from datetime import datetime
from fastapi import APIRouter, Query, status, Depends, Response
from fastapi.encoders import jsonable_encoder
from pydantic_extra_types.phone_numbers import PhoneNumber
from sqlalchemy import String, or_
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_vendor
from app.src.db import (
    ExecutiveToken,
    VendorToken,
    SessionLocal,
    Vendor,
    VendorImage,
)
from app.src.enums import AccountStatus, GenderType, VendorType, OrderIn
from app.src.filters import (
    AccountDataFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.minio import delete_file
from app.src.buckets import VENDOR_IMAGES
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src import exceptions
from app.src.regex import PASSWORD_PATTERN, USERNAME_PATTERN
from app.src.urls import URL_VENDOR_ACCOUNT
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
    get_vendor_roles,
    get_request_info,
    update_if_changed,
    apply_status_filters,
    apply_type_filters,
)

route_executive = APIRouter()
route_vendor = APIRouter()


## Output Schema
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


## Input Forms
class CreateFormForVD(BaseModel):
    """Form data for creating a new vendor account for a vendor."""

    username: str = Field(min_length=4, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=32, pattern=PASSWORD_PATTERN)
    gender: GenderType = Field(
        description=enum_str(GenderType), default=GenderType.OTHER
    )
    description: str | None = Field(min_length=1, max_length=32, default=None)
    type: VendorType = Field(
        description=enum_str(VendorType),
        default=VendorType.NORMAL,
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


class CreateFormForEX(CreateFormForVD):
    """Form data for creating a new vendor account for an executive."""

    business_id: int = Field()


class UpdateForm(BaseModel):
    """Form data for updating a vendor account."""

    password: str = Field(
        default=None, min_length=8, max_length=32, pattern=PASSWORD_PATTERN
    )
    gender: GenderType = Field(description=enum_str(GenderType), default=None)
    description: str | None = Field(min_length=1, max_length=32, default=None)
    type: VendorType = Field(
        description=enum_str(VendorType),
        default=None,
    )
    full_name: str | None = Field(min_length=1, max_length=32, default=None)
    status: AccountStatus = Field(description=enum_str(AccountStatus), default=None)
    phone_number: PhoneNumber | None = Field(
        max_length=32, default=None, description="Phone number in RFC 3966 format"
    )
    email_id: EmailStr | None = Field(
        max_length=256, default=None, description="Email in RFC 5322 format"
    )


# Query parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForVD(
    AccountDataFilter,
    UpdatedOnFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
):
    """Query parameters for vendors."""

    search: str | None = Field(Query(default=None))
    description: str | None = Field(Query(default=None))
    type_list: List[VendorType] | None = Field(
        Query(default=None, description=enum_str(VendorType))
    )
    status_list: List[AccountStatus] | None = Field(
        Query(default=None, description=enum_str(AccountStatus))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForVD):
    """Query parameters for executives."""

    business_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


## Functions
def update_vendor(
    session: Session, vendor: Vendor, form_param: UpdateForm
) -> Tuple[bool, dict]:
    """
    Updates a vendor account with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        vendor (Vendor): Vendor to update.
        form_param (UpdateForm): Form data for updating the vendor.

    Returns:
    Tuple[bool, dict]:
            - bool: True if the vendor was modified and the changes were committed.
            - dict: JSON-encoded representation of the updated vendor.
    """
    update_data = form_param.model_dump(exclude_unset=True)
    tokens_revoked = False
    if form_param.status == AccountStatus.SUSPENDED:
        tokens_revoked = (
            session.query(VendorToken)
            .filter(
                VendorToken.vendor_id == vendor.id,
                VendorToken.is_revoked.is_(False),
            )
            .update({VendorToken.is_revoked: True})
            > 0
        )

    update_if_changed(vendor, update_data)
    have_updates = session.is_modified(vendor) or tokens_revoked
    if have_updates:
        session.commit()
        session.refresh(vendor)

    vendor_data = jsonable_encoder(vendor, exclude={Vendor.password.name})
    return have_updates, vendor_data


def search_vendor(session: Session, query_params: QueryParams) -> List[Vendor]:
    """
    Search for Vendors based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve vendors that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Vendor]: List of Vendors that match the search criteria.
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


def delete_vendor(session: Session, vendor: Vendor) -> dict:
    """
    Delete a Vendor and its associated image.

    Args:
        session (Session): SQLAlchemy database session.
        vendor (Vendor): Vendor to delete.

    Returns:
        dict: deleted vendor data for logging purposes.
    """
    vendor_image = (
        session.query(VendorImage).filter(VendorImage.vendor_id == vendor.id).first()
    )
    vendor_data = jsonable_encoder(vendor, exclude={Vendor.password.name})
    session.delete(vendor)
    session.commit()

    if vendor_image is not None:
        delete_file(VENDOR_IMAGES, str(vendor_image.id))
    return vendor_data


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VENDOR_ACCOUNT,
    tags=["Vendor Account"],
    response_model=VendorSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new vendor account.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `business.vendor.create` permission.    
            - Duplicate usernames within the same business are not allowed.    
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
        verify_permission(roles, ExecutivePermissionPath.CREATE_BUSINESS_VENDOR)

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

        vendor_data = jsonable_encoder(vendor, exclude={Vendor.password.name})
        log_event(token, request_info, vendor_data)
        return vendor_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_VENDOR_ACCOUNT}/{{id}}",
    tags=["Vendor Account"],
    response_model=VendorSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Vendor.id),
        ]
    ),
    description=(
        """
            **Updates an existing vendor account.**    
            - Requires a valid access token.    
            - Logged-in executive must have `business.vendor.update` permission to update vendors.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_account_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_BUSINESS_VENDOR)

        vendor = validate_id(session, Vendor, id, Vendor.id)
        have_updates, vendor_data = update_vendor(session, vendor, form_param)
        if have_updates:
            log_event(token, request_info, vendor_data)
        return vendor_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_VENDOR_ACCOUNT,
    tags=["Vendor Account"],
    response_model=List[VendorSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vendors.**    
            - Requires a valid access token for authentication.    
            - Common search supports searching by id, username, full_name, description, phone_number, and email_id.    
        """
    ),
)
async def fetch_account_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_vendor(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_VENDOR_ACCOUNT}/{{id}}",
    tags=["Vendor Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an existing vendor account.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have the `business.vendor.delete` permission.    
            - Returns 204 No Content even if the specified account does not exist.    
        """
    ),
)
async def delete_account_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.DELETE_BUSINESS_VENDOR)

        vendor = session.query(Vendor).filter(Vendor.id == id).first()
        if vendor is not None:
            vendor_data = delete_vendor(session, vendor)
            log_event(token, request_info, vendor_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.post(
    URL_VENDOR_ACCOUNT,
    tags=["Account"],
    response_model=VendorSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new vendor account.**    
            - Vendor must have a valid access token.    
            - Logged-in vendor must have `business.vendor.create` permission.    
            - Duplicate usernames within the same business are not allowed.    
            - By default the user is created in active status.    
        """
    ),
)
async def create_account_vendor(
    form_param: CreateFormForVD,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)
        roles = get_vendor_roles(session, token)
        verify_permission(roles, VendorPermissionPath.CREATE_BUSINESS_VENDOR)

        vendor = Vendor(
            business_id=token.business_id,
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

        vendor_data = jsonable_encoder(vendor, exclude={Vendor.password.name})
        log_event(token, request_info, vendor_data)
        return vendor_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.patch(
    f"{URL_VENDOR_ACCOUNT}/{{id}}",
    tags=["Account"],
    response_model=VendorSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Vendor.id),
        ]
    ),
    description=(
        """
            **Updates an existing vendor account.**    
            - Requires a valid access token.    
            - Logged-in vendor must have `business.vendor.update` permission to update other vendors.    
            - Vendor can update their own account except status.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_account_vendor(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        is_self_update = id == token.vendor_id
        if not is_self_update:
            roles = get_vendor_roles(session, token)
            verify_permission(roles, VendorPermissionPath.UPDATE_BUSINESS_VENDOR)

        vendor = validate_id(
            session,
            Vendor,
            id,
            Vendor.id,
            extra_filter=(Vendor.business_id == token.business_id),
        )
        if is_self_update and form_param.status is not None:
            raise exceptions.NoPermission()

        have_updates, vendor_data = update_vendor(session, vendor, form_param)
        if have_updates:
            log_event(token, request_info, vendor_data)
        return vendor_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.get(
    URL_VENDOR_ACCOUNT,
    tags=["Account"],
    response_model=List[VendorSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vendors.**    
            - Requires a valid access token for authentication.    
            - Only vendors belonging to the same business as the logged-in vendor will be returned.    
            - Common search supports searching by id, username, full_name, description, phone_number, and email_id.    
        """
    ),
)
async def fetch_account_vendor(
    query_params: QueryParamsForVD = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        return search_vendor(
            session,
            QueryParams(**query_params.model_dump(), business_id=token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.delete(
    f"{URL_VENDOR_ACCOUNT}/{{id}}",
    tags=["Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an existing vendor account.**    
            - Requires a valid access token for authentication.    
            - The logged-in vendor must have the `business.vendor.delete` permission.    
            - Self-deletion is not allowed for safety reasons.    
            - Returns 204 No Content even if the specified account does not exist.    
        """
    ),
)
async def delete_account_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)
        roles = get_vendor_roles(session, token)
        verify_permission(roles, VendorPermissionPath.DELETE_BUSINESS_VENDOR)

        if token.vendor_id == id:
            raise exceptions.NoPermission()
        vendor = (
            session.query(Vendor)
            .filter(Vendor.id == id, Vendor.business_id == token.business_id)
            .first()
        )
        if vendor is not None:
            vendor_data = delete_vendor(session, vendor)
            log_event(token, request_info, vendor_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
