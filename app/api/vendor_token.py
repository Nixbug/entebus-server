"""
Vendor Token API Router for EnteBus.

Provides an endpoint for managing vendor access tokens, including creation, and retrieval.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion and refresh are planned for future implementation.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Optional, List
from fastapi import APIRouter, Depends, Form, Response, status, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.bearer import bearer_vendor, oauth2_executive
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.db import Vendor, VendorToken, ExecutiveToken, SessionLocal
from app.src import exceptions
from app.src.enums import PlatformType, GrantType, OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    ClientDataFilter,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src.urls import URL_VENDOR_TOKEN
from app.src.constants import (
    MAX_ACCESS_TOKEN_VALIDITY,
    MAX_VENDOR_TOKENS,
    MAX_REFRESH_TOKEN_VALIDITY,
)
from app.src.validators import (
    authenticate_vendor,
    validate_and_revoke_refresh_token,
    verify_token,
    verify_permission,
    verify_permission,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_client_data_filters,
    cleanup_old_tokens,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    get_vendor_roles,
    get_executive_roles,
)

route_vendor = APIRouter()
route_executive = APIRouter()


# Output Schema
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


# Input Schema.
class CreateForm(BaseModel):
    """Form data for creating a new vendor token."""

    business_id: int = Field(Form())
    platform_type: PlatformType = Field(
        Form(description=enum_str(PlatformType), default=PlatformType.OTHER)
    )
    client_details: str | None = Field(Form(max_length=1024, default=None))


class UpdateForm(BaseModel):
    """Form data for refreshing a vendor token."""

    refresh_token: str = Field(Form())
    grant_type: GrantType = Field(
        Form(description=enum_str(GrantType), default=GrantType.REFRESH_TOKEN)
    )


class LogoutForm(BaseModel):
    """Form data for logging out with a vendor token."""

    token: str = Field(Form(description="Access or refresh token"))


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"


class QueryParamsForVE(ClientDataFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for vendor."""

    vendor_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForVE):
    """Query parameters for executive."""

    business_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """Query parameters for vendor and executive."""

    pass


## Functions
def search_vendor_tokens(
    session: Session, query_params: QueryParams
) -> List[VendorToken]:
    """
    Search for vendor tokens based on provided query parameters.

    This function supports multiple filtering, ordering, and
    pagination capabilities to retrieve vendor tokens that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[VendorToken]: List of vendor tokens that match the search criteria.
    """
    query = session.query(VendorToken).filter(VendorToken.is_revoked == False)
    if query_params.business_id is not None:
        query = query.filter(VendorToken.business_id == query_params.business_id)
    if query_params.vendor_id is not None:
        query = query.filter(VendorToken.vendor_id == query_params.vendor_id)

    # generalized helpers
    query = apply_id_filters(query, VendorToken, query_params)
    query = apply_created_on_filters(query, VendorToken, query_params)
    query = apply_client_data_filters(query, VendorToken, query_params)

    # ordering and pagination
    ordering_attr = getattr(VendorToken, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    return query.all()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.post(
    URL_VENDOR_TOKEN,
    tags=["Token"],
    response_model=VendorTokenSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InactiveAccount(),
            exceptions.InvalidCredentials(),
            exceptions.UnknownValue(Vendor.business_id),
            exceptions.InvalidGrantType(),
        ]
    ),
    description=(
        f"""
            **Issue a new access token for a vendor after validating credentials.**     
            - Verify the username and password.     
            - Ensure the vendor account is in `active status` before allowing token creation.        
            - Maintain a limit of `{MAX_VENDOR_TOKENS}` active tokens per vendor to control token rotation.               
            - Generate a new Vendor Token with a pair of access and refresh tokens.         
            - The `expires_in` indicates the number of seconds until the access token expires, the maximum allowed access token validity is `{(timedelta(seconds=MAX_ACCESS_TOKEN_VALIDITY)).seconds // 60}` minutes.   
            - The `refresh_before` indicates the datetime when the refresh token expires, the maximum allowed refresh token validity is `{(timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY)).days}` days.   
            - A new access token can be generated by using the refresh token before it expires.     
        """
    ),
)
async def create_token(
    form_param: CreateForm = Depends(),
    credentials: OAuth2PasswordRequestForm = Depends(),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        vendor = authenticate_vendor(session, credentials, form_param)

        # Remove excess tokens
        cleanup_old_tokens(
            session,
            VendorToken,
            VendorToken.vendor_id == vendor.id,
            MAX_VENDOR_TOKENS - 1,
        )

        # Create new token
        token = VendorToken(
            business_id=form_param.business_id,
            vendor_id=vendor.id,
            platform_type=form_param.platform_type,
            client_details=form_param.client_details,
        )
        session.add(token)
        session.commit()
        session.refresh(token)

        token_data = jsonable_encoder(token)
        token_log_data = token_data.copy()
        token_log_data.pop(VendorToken.access_token.name)
        token_log_data.pop(VendorToken.refresh_token.name)
        log_event(token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.post(
    f"{URL_VENDOR_TOKEN}/refresh",
    tags=["Token"],
    response_model=VendorTokenSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.UnknownValue(VendorToken.refresh_token),
            exceptions.InvalidGrantType(),
        ]
    ),
    description=(
        f"""
            **Refresh a vendor's access token using a valid refresh token.**        
            - Verify the provided refresh token exists in the database.     
            - Invalidate the current refresh token.             
            - Generate a new `Vendor Token` with a pair of access and refresh tokens.        
            - The `expires_in` indicates the number of seconds until the access token expires, the maximum allowed access token validity is `{(timedelta(seconds=MAX_ACCESS_TOKEN_VALIDITY)).seconds // 60}` minutes.   
            - The `refresh_before` indicates the datetime when the refresh token expires, the maximum allowed refresh token validity is `{(timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY)).days}` days.  
            - A new access token can be generated by using the refresh token before it expires.    
        """
    ),
)
async def refresh_token(
    form_param: UpdateForm = Depends(),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        # Validate and revoke the old refresh token
        token = validate_and_revoke_refresh_token(session, VendorToken, form_param)
        # Create new token
        refresh_token = VendorToken(
            vendor_id=token.vendor_id,
            business_id=token.business_id,
            platform_type=token.platform_type,
            client_details=token.client_details,
        )
        session.add(refresh_token)
        session.flush()

        # Remove excess tokens
        cleanup_old_tokens(
            session,
            VendorToken,
            VendorToken.vendor_id == token.vendor_id,
            MAX_VENDOR_TOKENS,
        )
        session.commit()
        session.refresh(refresh_token)

        token_data = jsonable_encoder(refresh_token)
        token_log_data = token_data.copy()
        token_log_data.pop(VendorToken.access_token.name)
        token_log_data.pop(VendorToken.refresh_token.name)
        log_event(token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.post(
    f"{URL_VENDOR_TOKEN}/revoke",
    tags=["Token"],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Revokes an access token or refresh token associated with the vendor.**     
            - Vendor must have a valid access token.     
            - Revokes the token (access or refresh) specified in the request body.      
            - If the token is invalid, doesn't belong to the vendor, or is already revoked, the operation is silently ignored.       
        """
    ),
)
async def revoke_token(
    form_param: LogoutForm = Depends(),
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        token_to_revoke = (
            session.query(VendorToken)
            .filter(VendorToken.vendor_id == token.vendor_id)
            .filter(
                (VendorToken.access_token == form_param.token)
                | (VendorToken.refresh_token == form_param.token)
            )
            .filter(VendorToken.is_revoked.is_(False))
            .first()
        )
        if token_to_revoke:
            token_to_revoke.is_revoked = True
            session.commit()
            session.refresh(token_to_revoke)

            token_log_data = jsonable_encoder(token_to_revoke)
            token_log_data.pop(VendorToken.access_token.name)
            token_log_data.pop(VendorToken.refresh_token.name)
            log_event(token, request_info, token_log_data)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.get(
    URL_VENDOR_TOKEN,
    tags=["Token"],
    response_model=list[MaskedVendorTokenSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Fetch vendor tokens with permission-based filtering.**    
            - If the logged-in vendor has `business.vendor.token.fetch` permission, all masked tokens within the vendor's business are returned.    
            - If the logged-in vendor does not have permission, only masked tokens for the logged-in vendor are returned.   
            - Trying to access tokens of other vendors within the same business without permission will result in `NoPermission` error.     
        """
    ),
)
async def fetch_tokens_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)
        roles = get_vendor_roles(session, token)
        has_permission = verify_permission(
            roles, VendorPermissionPath.FETCH_BUSINESS_VENDOR_TOKEN, False
        )

        if not has_permission:
            if query_params.vendor_id not in (None, token.vendor_id):
                raise exceptions.NoPermission()
            # Restrict to only the logged-in vendor's tokens
            query_params.vendor_id = token.vendor_id

        return search_vendor_tokens(
            session,
            QueryParams(**query_params.model_dump(), business_id=token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.delete(
    f"{URL_VENDOR_TOKEN}/{{id}}",
    tags=["Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes a vendor access token.**    
            - Vendor must have a valid access token.    
            - Vendors can delete their own tokens without additional permissions.    
            - To delete another vendor's token in the same business, the 'business.vendor.token.delete' permission is required,    
            - Trying to delete another vendor's token without permission will result in a `NoPermission` error.   
            - If the token ID is invalid or already revoked, the operation is silently ignored.   
        """
    ),
)
async def delete_token_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)
        roles = get_vendor_roles(session, token)
        has_permission = verify_permission(
            roles, VendorPermissionPath.DELETE_BUSINESS_VENDOR_TOKEN, False
        )

        token_to_delete = (
            session.query(VendorToken)
            .filter(VendorToken.id == id)
            .filter(VendorToken.business_id == token.business_id)
            .filter(VendorToken.is_revoked.is_(False))
            .first()
        )
        if token_to_delete is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if not has_permission and token_to_delete.vendor_id != token.vendor_id:
            raise exceptions.NoPermission()

        token_to_delete.is_revoked = True
        session.commit()
        session.refresh(token_to_delete)

        token_log_data = jsonable_encoder(token_to_delete)
        token_log_data.pop(VendorToken.access_token.name)
        token_log_data.pop(VendorToken.refresh_token.name)
        log_event(token, request_info, token_log_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_VENDOR_TOKEN,
    tags=["Vendor Token"],
    response_model=list[MaskedVendorTokenSchema],
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
        ]
    ),
    description=(
        """
            **Fetch vendor tokens with permission-based filtering.**     
            - If the logged-in executive has `business.vendor.token.fetch` permission, all masked tokens are returned.    
            - If the logged-in executive does not have permission, they cannot access this endpoint.     
        """
    ),
)
async def fetch_tokens_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.FETCH_BUSINESS_VENDOR_TOKEN)

        return search_vendor_tokens(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_VENDOR_TOKEN}/{{id}}",
    tags=["Vendor Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes a vendor access token.**    
            - Executive must have a valid access token.    
            - Executive must have 'business.vendor.token.delete' permission.    
            - Executive can delete any vendor's token.    
            - If the token ID is invalid or already revoked, the operation is silently ignored.    
        """
    ),
)
async def delete_token_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.DELETE_BUSINESS_VENDOR_TOKEN)

        token_to_delete = (
            session.query(VendorToken)
            .filter(VendorToken.id == id)
            .filter(VendorToken.is_revoked.is_(False))
            .first()
        )
        if token_to_delete is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        # Revoke token
        token_to_delete.is_revoked = True
        session.commit()
        session.refresh(token_to_delete)

        token_log_data = jsonable_encoder(token_to_delete)
        token_log_data.pop(VendorToken.access_token.name)
        token_log_data.pop(VendorToken.refresh_token.name)
        log_event(token, request_info, token_log_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
