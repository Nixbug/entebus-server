"""
Vendor Token API router.

Provides endpoints for managing vendor tokens:
    - POST (vendor)
    - POST /refresh (vendor)
    - POST /revoke (vendor)
    - DELETE (vendor, executive)
    - GET (vendor, executive)
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Optional
from fastapi import APIRouter, Depends, Form, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_vendor, oauth2_executive
from app.src import exceptions, schemas
from app.src.constants import (
    MAX_ACCESS_TOKEN_VALIDITY,
    MAX_REFRESH_TOKEN_VALIDITY,
    MAX_VENDOR_TOKENS,
)
from app.src.db import (
    ExecutiveToken,
    Vendor,
    VendorToken,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import GrantType, OrderIn, PlatformType
from app.src.filters import (
    ClientDataFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
)
from app.src.functions import (
    apply_client_data_filters,
    apply_created_on_filters,
    apply_id_filters,
    cleanup_old_tokens,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    get_vendor_roles,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src.schemas import PatchForm
from app.src.urls import URL_VENDOR_TOKEN
from app.src.validators import (
    authenticate_vendor,
    authorize_executive,
    validate_and_revoke_refresh_token,
    verify_permission,
    verify_token,
)

route_vendor = APIRouter()
route_executive = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class MaskedVendorTokenSchema(BaseModel):
    """Schema for vendor token response without revealing the tokens."""

    id: int
    vendor_id: int
    business_id: int
    expires_in: int
    refresh_before: datetime
    platform_type: int
    client_details: Optional[str]
    created_on: datetime


class VendorTokenSchema(MaskedVendorTokenSchema):
    """Schema for vendor token response including the tokens."""

    access_token: str
    refresh_token: str
    token_type: Optional[str] = "bearer"


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """Form data for creating a new vendor token."""

    business_id: int = Field(Form())
    platform_type: PlatformType = Field(
        Form(description=enum_str(PlatformType), default=PlatformType.OTHER)
    )
    client_details: str | None = Field(Form(max_length=1024, default=None))


class UpdateForm(PatchForm):
    """Form data for refreshing a vendor token."""

    refresh_token: str = Field(Form())
    grant_type: GrantType = Field(
        Form(description=enum_str(GrantType), default=GrantType.REFRESH_TOKEN)
    )


class LogoutForm(BaseModel):
    """Form data for logging out with a vendor token."""

    token: str = Field(Form(description="Access or refresh token"))


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"


class QueryParamsForVE(ClientDataFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for vendor endpoints."""

    vendor_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForVE):
    """Query parameters for executive endpoints."""

    business_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
def vendor_token_to_dict(vendor_token: VendorToken) -> tuple[dict, dict]:
    """
    Convert a VendorToken SQLAlchemy model instance to a dictionary.

    Args:
        vendor_token (VendorToken): VendorToken model instance.

    Returns:
        tuple[dict, dict]:
            - dict: JSON-encoded representation of the vendor token.
            - dict: Log data related to the vendor token.
    """
    vendor_token_data = jsonable_encoder(vendor_token)
    vendor_token_log_data = vendor_token_data.copy()
    vendor_token_log_data.pop(VendorToken.access_token.name)
    vendor_token_log_data.pop(VendorToken.refresh_token.name)
    return vendor_token_data, vendor_token_log_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_vendor_token(
    session: Session,
    form_param: CreateForm,
    vendor: Vendor,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new vendor token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new vendor token.
        vendor (Vendor): Vendor for whom the token is being created.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created vendor token data.
    """
    cleanup_old_tokens(
        session,
        VendorToken,
        VendorToken.vendor_id == vendor.id,
        MAX_VENDOR_TOKENS - 1,
    )

    vendor_token = VendorToken(
        business_id=form_param.business_id,
        vendor_id=vendor.id,
        platform_type=form_param.platform_type,
        client_details=form_param.client_details,
    )
    session.add(vendor_token)
    session.commit()
    session.refresh(vendor_token)

    vendor_token_data, vendor_token_log_data = vendor_token_to_dict(vendor_token)
    log_event(vendor_token, request_info, vendor_token_log_data)
    return vendor_token_data


def refresh_vendor_token(
    session: Session,
    token: VendorToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Refresh a vendor token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        token (VendorToken): Authenticated vendor token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Refreshed vendor token data.
    """
    token.is_revoked = True
    cleanup_old_tokens(
        session,
        VendorToken,
        VendorToken.vendor_id == token.vendor_id,
        MAX_VENDOR_TOKENS - 1,
    )

    vendor_token = VendorToken(
        business_id=token.business_id,
        vendor_id=token.vendor_id,
        platform_type=token.platform_type,
        client_details=token.client_details,
    )
    session.add(vendor_token)
    session.commit()
    session.refresh(vendor_token)

    vendor_token_data, vendor_token_log_data = vendor_token_to_dict(vendor_token)
    log_event(token, request_info, vendor_token_log_data)
    return vendor_token_data


def revoke_vendor_token(
    session: Session,
    form_param: LogoutForm,
    token: VendorToken,
    request_info: schemas.RequestInfo,
) -> None:
    """
    Revoke a vendor token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (LogoutForm): Form data containing the token to revoke.
        token (VendorToken): Authenticated vendor token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    vendor_token = (
        session.query(VendorToken)
        .filter(VendorToken.vendor_id == token.vendor_id)
        .filter(
            (VendorToken.access_token == form_param.token)
            | (VendorToken.refresh_token == form_param.token)
        )
        .filter(VendorToken.is_revoked.is_(False))
        .first()
    )
    if vendor_token is None:
        return

    vendor_token.is_revoked = True
    session.commit()
    session.refresh(vendor_token)
    _, vendor_token_log_data = vendor_token_to_dict(vendor_token)
    log_event(token, request_info, vendor_token_log_data)


def delete_vendor_token(
    session: Session,
    vendor_token: VendorToken,
    token: VendorToken | ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> None:
    """
    Delete a vendor token from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        vendor_token (VendorToken): Vendor token to be deleted.
        token (VendorToken | ExecutiveToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    vendor_token.is_revoked = True
    session.commit()
    session.refresh(vendor_token)
    _, vendor_token_log_data = vendor_token_to_dict(vendor_token)
    log_event(token, request_info, vendor_token_log_data)


def search_vendor_tokens(
    session: Session, query_params: QueryParams
) -> list[VendorToken]:
    """
    Search for vendor tokens based on provided query parameters.

    This function supports multiple filtering, ordering, and
    pagination capabilities to retrieve vendor tokens that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[VendorToken]: List of vendor tokens that match the search criteria.
    """
    query = session.query(VendorToken).filter(VendorToken.is_revoked.is_(False))
    if query_params.business_id is not None:
        query = query.filter(VendorToken.business_id == query_params.business_id)
    if query_params.vendor_id is not None:
        query = query.filter(VendorToken.vendor_id == query_params.vendor_id)

    # Generalized filters
    query = apply_id_filters(query, VendorToken, query_params)
    query = apply_created_on_filters(query, VendorToken, query_params)
    query = apply_client_data_filters(query, VendorToken, query_params)

    # Ordering and pagination
    ordering_attr = getattr(VendorToken, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    vendor_tokens = query.all()
    return vendor_tokens


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InactiveAccount(),
    exceptions.InvalidCredentials(),
    exceptions.UnknownValue(Vendor.business_id),
    exceptions.InvalidGrantType(),
]

REFRESH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.UnknownValue(VendorToken.refresh_token),
    exceptions.InvalidGrantType(),
]

REVOKE_EXCEPTIONS = [
    exceptions.InvalidToken(),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]


# ---------------------------------------------------------------------------
## Common descriptions
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Issue a new access token for a vendor after validating credentials.")
    .add_line("Verify the username and password.")
    .add_line(
        "Ensure the vendor account is in `active status` before allowing token creation."
    )
    .add_line(
        f"Maintain a limit of `{MAX_VENDOR_TOKENS}` active tokens per vendor to control token rotation."
    )
    .add_line("Generate a new Vendor Token with a pair of access and refresh tokens.")
    .add_line(
        f"The `expires_in` indicates the number of seconds until the access token expires, the maximum allowed access token validity is `{(timedelta(seconds=MAX_ACCESS_TOKEN_VALIDITY)).seconds // 60}` minutes."
    )
    .add_line(
        f"The `refresh_before` indicates the datetime when the refresh token expires, the maximum allowed refresh token validity is `{(timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY)).days}` days."
    )
    .add_line(
        "A new access token can be generated by using the refresh token before it expires."
    )
)

REFRESH_DESCRIPTION = (
    Description()
    .add_head("Refresh a vendor's access token using a valid refresh token.")
    .add_line("Verify the provided refresh token exists in the database.")
    .add_line("Invalidate the current refresh token.")
    .add_line("Generate a new `Vendor Token` with a pair of access and refresh tokens.")
    .add_line(
        f"The `expires_in` indicates the number of seconds until the access token expires, the maximum allowed access token validity is `{(timedelta(seconds=MAX_ACCESS_TOKEN_VALIDITY)).seconds // 60}` minutes."
    )
    .add_line(
        f"The `refresh_before` indicates the datetime when the refresh token expires, the maximum allowed refresh token validity is `{(timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY)).days}` days."
    )
    .add_line(
        "A new access token can be generated by using the refresh token before it expires."
    )
)

REVOKE_DESCRIPTION = (
    Description()
    .add_head("Revoke or logout a vendor token.")
    .add_line("Revokes the token (access or refresh) specified in the request body.")
    .add_line(
        "If the token is invalid, doesn't belong to the vendor, or is already revoked, the operation is silently ignored."
    )
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Delete a vendor token.")
    .add_line(
        "If the token ID is invalid or already revoked, the operation is silently ignored."
    )
)

GET_DESCRIPTION = Description().add_head("Fetch vendor tokens.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_VENDOR_TOKEN,
    summary="Fetch vendor token",
    tags=["Vendor Token"],
    response_model=list[MaskedVendorTokenSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "If the logged-in executive has `business.vendor.token.fetch` permission, all masked tokens are returned."
        )
        .to_string()
    ),
)
async def fetch_vendor_tokens_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.FETCH_BUSINESS_VENDOR_TOKEN],
        )
        return search_vendor_tokens(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_VENDOR_TOKEN}/{{id}}",
    summary="Delete vendor token",
    tags=["Vendor Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `business.vendor.token.delete` permission."
        )
        .to_string()
    ),
)
async def delete_vendor_token_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_BUSINESS_VENDOR_TOKEN],
        )
        vendor_token = get_by_id(session, VendorToken, id)
        if vendor_token is not None and not vendor_token.is_revoked:
            delete_vendor_token(session, vendor_token, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.post(
    URL_VENDOR_TOKEN,
    summary="Create vendor token",
    tags=["Token"],
    response_model=VendorTokenSchema,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=POST_DESCRIPTION.to_string(),
)
async def create_vendor_token_for_vendor(
    form_param: CreateForm = Depends(),
    credentials: OAuth2PasswordRequestForm = Depends(),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        vendor = authenticate_vendor(session, credentials, form_param)
        return create_vendor_token(session, form_param, vendor, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_vendor.post(
    f"{URL_VENDOR_TOKEN}/refresh",
    summary="Refresh vendor token",
    tags=["Token"],
    response_model=VendorTokenSchema,
    responses=fuse_exception_responses(REFRESH_EXCEPTIONS),
    description=REFRESH_DESCRIPTION.to_string(),
)
async def refresh_vendor_token_for_vendor(
    form_param: UpdateForm = Depends(),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = validate_and_revoke_refresh_token(session, VendorToken, form_param)
        return refresh_vendor_token(session, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_vendor.post(
    f"{URL_VENDOR_TOKEN}/revoke",
    summary="Revoke vendor token",
    tags=["Token"],
    responses=fuse_exception_responses(REVOKE_EXCEPTIONS),
    description=REVOKE_DESCRIPTION.to_string(),
)
async def revoke_vendor_token_for_vendor(
    form_param: LogoutForm = Depends(),
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        revoke_vendor_token(session, form_param, token, request_info)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        exceptions.handle(e)


@route_vendor.get(
    URL_VENDOR_TOKEN,
    summary="Fetch vendor tokens",
    tags=["Token"],
    response_model=list[MaskedVendorTokenSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.copy()
    .add_line(
        "If the logged-in vendor has `business.vendor.token.fetch` permission, all masked tokens within the vendor's business are returned."
    )
    .add_line(
        "If the logged-in vendor does not have permission, only masked tokens for the logged-in vendor are returned."
    )
    .add_line(
        "Trying to access tokens of other vendors within the same business without permission will result in `NoPermission` error."
    )
    .to_string(),
)
async def fetch_vendor_tokens_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        roles = get_vendor_roles(session, token)
        has_permission = verify_permission(
            roles,
            VendorPermissionPath.FETCH_BUSINESS_VENDOR_TOKEN,
            raise_exception=False,
        )

        if not has_permission:
            if (
                query_params.vendor_id is not None
                and query_params.vendor_id != token.vendor_id
            ):
                raise exceptions.NoPermission()
            query_params.vendor_id = token.vendor_id
        return search_vendor_tokens(
            session,
            QueryParams(**query_params.model_dump(), business_id=token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.delete(
    f"{URL_VENDOR_TOKEN}/{{id}}",
    summary="Delete vendor token",
    tags=["Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=DELETE_DESCRIPTION.copy()
    .add_line("Vendors can delete their own tokens without additional permissions.")
    .add_line(
        "To delete another vendor's token in the same business, the `business.vendor.token.delete` permission is required."
    )
    .add_line(
        "Trying to delete another vendor's token without permission will result in a `NoPermission` error."
    )
    .add_line(
        "If the token ID is invalid or already revoked, the operation is silently ignored."
    )
    .to_string(),
)
async def delete_vendor_token_for_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        roles = get_vendor_roles(session, token)
        has_permission = verify_permission(
            roles,
            VendorPermissionPath.DELETE_BUSINESS_VENDOR_TOKEN,
            raise_exception=False,
        )

        vendor_token = get_by_id(
            session,
            VendorToken,
            id,
            extra_filter=(VendorToken.business_id == token.business_id),
        )
        if vendor_token is not None and not vendor_token.is_revoked:
            if not has_permission and vendor_token.vendor_id != token.vendor_id:
                raise exceptions.NoPermission()
            delete_vendor_token(
                session,
                vendor_token,
                token,
                request_info,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
