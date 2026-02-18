"""
Operator Token API Router for EnteBus.

Provides an endpoint for managing operator access tokens, including creation,
refresh, and retrieval. Uses Pydantic schemas for input validation
and structured output. Endpoints for deletion are planned for future implementation.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import List, Optional
from fastapi import APIRouter, Depends, Form, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.db import ExecutiveToken, Operator, OperatorToken, SessionLocal
from app.src import exceptions
from app.src.enums import GrantType, OrderIn, PlatformType
from app.src.filters import (
    ClientDataFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
)
from app.src.openobserve import log_event
from app.src.urls import URL_OPERATOR_TOKEN
from app.src.constants import (
    MAX_ACCESS_TOKEN_VALIDITY,
    MAX_OPERATOR_TOKENS,
    MAX_REFRESH_TOKEN_VALIDITY,
)
from app.src.validators import (
    authenticate_operator,
    validate_and_revoke_refresh_token,
    verify_token,
    verify_permission,
)
from app.src.functions import (
    apply_client_data_filters,
    apply_created_on_filters,
    apply_id_filters,
    cleanup_old_tokens,
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_request_info,
    get_operator_roles,
)

route_operator = APIRouter()
route_executive = APIRouter()


# Output Schema
class MaskedOperatorTokenSchema(BaseModel):
    """Schema for operator token response without revealing the tokens."""

    id: int
    operator_id: int
    company_id: int
    expires_in: int
    refresh_before: datetime
    platform_type: int
    client_details: Optional[str]
    created_on: datetime


class OperatorTokenSchema(MaskedOperatorTokenSchema):
    """Schema for operator token response including the tokens."""

    access_token: str
    refresh_token: str
    token_type: Optional[str] = "bearer"


# Input Schema.
class CreateForm(BaseModel):
    """Form data for creating a new operator token."""

    company_id: int = Field(Form())
    platform_type: PlatformType = Field(
        Form(description=enum_str(PlatformType), default=PlatformType.OTHER)
    )
    client_details: str | None = Field(Form(max_length=1024, default=None))


class UpdateForm(BaseModel):
    """Form data for refreshing an operator token."""

    refresh_token: str = Field(Form())
    grant_type: GrantType = Field(
        Form(description=enum_str(GrantType), default=GrantType.REFRESH_TOKEN)
    )


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"


class QueryParams(ClientDataFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for operator token endpoints."""

    operator_id: int | None = Field(Query(default=None))
    company_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


## Functions
def search_operator_tokens(
    session: Session, query_params: QueryParams
) -> List[OperatorToken]:
    """
     Search for operator tokens based on provided query parameters.

    This function supports multiple filtering, ordering, and
    pagination capabilities to retrieve operator tokens that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[OperatorToken]: List of operator tokens that match the search criteria.
    """
    query = session.query(OperatorToken).filter(OperatorToken.is_revoked == False)

    if query_params.operator_id is not None:
        query = query.filter(OperatorToken.operator_id == query_params.operator_id)
    if query_params.company_id is not None:
        query = query.filter(OperatorToken.company_id == query_params.company_id)

    # generalized helpers
    query = apply_id_filters(query, OperatorToken, query_params)
    query = apply_created_on_filters(query, OperatorToken, query_params)
    query = apply_client_data_filters(query, OperatorToken, query_params)

    # ordering and pagination
    ordering_attr = getattr(OperatorToken, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    return query.all()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_TOKEN,
    tags=["Token"],
    response_model=OperatorTokenSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InactiveAccount(),
            exceptions.InvalidCredentials(),
            exceptions.UnknownValue(Operator.company_id),
            exceptions.InvalidGrantType(),
        ]
    ),
    description=(
        f"""
            **Issue a new access token for an operator after validating credentials.**     
            - Verify the username and password.     
            - Ensure the operator account is in `active status` before allowing token creation.        
            - Maintain a limit of `{MAX_OPERATOR_TOKENS}` active tokens per operator to control token rotation.               
            - Generate a new Operator Token with a pair of access and refresh tokens.         
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
        operator = authenticate_operator(session, Operator, credentials, form_param)

        # Remove excess tokens
        cleanup_old_tokens(
            session,
            OperatorToken,
            OperatorToken.operator_id == operator.id,
            MAX_OPERATOR_TOKENS - 1,
        )

        # Create new token
        token = OperatorToken(
            company_id=form_param.company_id,
            operator_id=operator.id,
            platform_type=form_param.platform_type,
            client_details=form_param.client_details,
        )
        session.add(token)
        session.commit()
        session.refresh(token)

        token_data = jsonable_encoder(token)
        token_log_data = token_data.copy()
        token_log_data.pop(OperatorToken.access_token.name)
        token_log_data.pop(OperatorToken.refresh_token.name)
        log_event(token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.post(
    f"{URL_OPERATOR_TOKEN}/refresh",
    tags=["Token"],
    response_model=OperatorTokenSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.UnknownValue(OperatorToken.refresh_token),
            exceptions.InvalidGrantType(),
        ]
    ),
    description=(
        f"""
            **Refresh an operator's access token using a valid refresh token.**        
            - Verify the provided refresh token exists in the database.     
            - Invalidate the current refresh token.             
            - Generate a new `Operator Token` with a pair of access and refresh tokens.        
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
        token = validate_and_revoke_refresh_token(session, OperatorToken, form_param)
        # Create new token
        refresh_token = OperatorToken(
            operator_id=token.operator_id,
            company_id=token.company_id,
            platform_type=token.platform_type,
            client_details=token.client_details,
        )
        session.add(refresh_token)
        session.flush()

        # Remove excess tokens
        cleanup_old_tokens(
            session,
            OperatorToken,
            OperatorToken.operator_id == token.operator_id,
            MAX_OPERATOR_TOKENS,
        )
        session.commit()
        session.refresh(refresh_token)

        token_data = jsonable_encoder(refresh_token)
        token_log_data = token_data.copy()
        token_log_data.pop(OperatorToken.access_token.name)
        token_log_data.pop(OperatorToken.refresh_token.name)
        log_event(refresh_token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_OPERATOR_TOKEN,
    tags=["Token"],
    response_model=List[MaskedOperatorTokenSchema],
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
        ]
    ),
    description=(
        """
            **Fetch operator tokens with permission-based filtering.**     
            - If the logged-in operator has `company.operator.token.fetch` permission, all masked tokens are returned.    
            - If the logged-in operator does not have permission, only masked tokens for the logged-in operator are returned.    
        """
    ),
)
async def fetch_tokens_operator(
    query_params: QueryParams = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        has_permission = verify_permission(
            roles, OperatorPermissionPath.FETCH_COMPANY_OPERATOR_TOKEN, False
        )
        query_params.company_id = token.company_id
        if has_permission is False:
            query_params.operator_id = token.operator_id

        return search_operator_tokens(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_OPERATOR_TOKEN,
    tags=["Operator Token"],
    response_model=List[MaskedOperatorTokenSchema],
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
        ]
    ),
    description=(
        """
            **Fetch operator tokens with permission-based filtering.**     
            - If the logged-in executive has `company.operator.token.fetch` permission, all masked tokens are returned.    
            - If the logged-in executive does not have permission, they cannot access this endpoint.
        """
    ),
)
async def fetch_tokens_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        has_permission = verify_permission(
            roles, ExecutivePermissionPath.FETCH_COMPANY_OPERATOR_TOKEN, False
        )

        if has_permission is False:
            raise exceptions.NoPermission()

        return search_operator_tokens(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
