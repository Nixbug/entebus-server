"""
Operator Token API Router for EnteBus.

Provides an endpoint for managing operator access tokens, including creation,
refresh, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import List, Optional
from fastapi import APIRouter, Depends, Form, Query, Response, status
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


class LogoutForm(BaseModel):
    """Form data for logging out with an operator token."""

    token: str = Field(Form(description="Access or refresh token"))


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"


class QueryParamsForOP(ClientDataFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for operator."""

    operator_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executive."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


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
    if query_params.company_id is not None:
        query = query.filter(OperatorToken.company_id == query_params.company_id)
    if query_params.operator_id is not None:
        query = query.filter(OperatorToken.operator_id == query_params.operator_id)

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
    summary="Create operator token",
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
async def create_operator_token_for_operator(
    form_param: CreateForm = Depends(),
    credentials: OAuth2PasswordRequestForm = Depends(),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        operator = authenticate_operator(session, credentials, form_param)

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
    summary="Refresh operator token",
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
async def refresh_operator_token_for_operator(
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
        log_event(token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.post(
    f"{URL_OPERATOR_TOKEN}/revoke",
    summary="Revoke operator token",
    tags=["Token"],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Revokes an access token or refresh token associated with the operator.**    
            - Operator must have a valid access token.    
            - Revokes the token (access or refresh) specified in the request body.    
            - If the token is invalid, doesn't belong to the operator, or is already revoked, the operation is silently ignored.    
        """
    ),
)
async def revoke_operator_token_for_operator(
    form_param: LogoutForm = Depends(),
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        token_to_revoke = (
            session.query(OperatorToken)
            .filter(OperatorToken.operator_id == token.operator_id)
            .filter(
                (OperatorToken.access_token == form_param.token)
                | (OperatorToken.refresh_token == form_param.token)
            )
            .filter(OperatorToken.is_revoked.is_(False))
            .first()
        )
        if token_to_revoke:
            token_to_revoke.is_revoked = True
            session.commit()
            session.refresh(token_to_revoke)

            token_log_data = jsonable_encoder(token_to_revoke)
            token_log_data.pop(OperatorToken.access_token.name)
            token_log_data.pop(OperatorToken.refresh_token.name)
            log_event(token, request_info, token_log_data)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_OPERATOR_TOKEN,
    summary="Fetch operator token",
    tags=["Token"],
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
            - If the logged-in operator has `company.operator.token.fetch` permission, all masked tokens within the operator's company are returned.    
            - If the logged-in operator does not have permission, only masked tokens for the logged-in operator are returned.    
            - Trying to access tokens of other operators within the same company without permission will result in `NoPermission` error.    
        """
    ),
)
async def fetch_operator_tokens_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        has_permission = verify_permission(
            roles, OperatorPermissionPath.FETCH_COMPANY_OPERATOR_TOKEN, False
        )

        if not has_permission:
            if query_params.operator_id not in (None, token.operator_id):
                raise exceptions.NoPermission()
            # Restrict to only the logged-in operator's tokens
            query_params.operator_id = token.operator_id

        return search_operator_tokens(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_OPERATOR_TOKEN}/{{id}}",
    summary="Delete operator token",
    tags=["Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an operator access token.**    
            - Operator must have a valid access token.    
            - Operators can delete their own tokens without additional permissions.    
            - To delete another operator's token in the same company, the 'company.operator.token.delete' permission is required.    
            - Trying to delete another operator's token without permission will result in a `NoPermission` error.    
            - If the token ID is invalid or already revoked, the operation is silently ignored.    
        """
    ),
)
async def delete_operator_token_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        has_permission = verify_permission(
            roles,
            OperatorPermissionPath.DELETE_COMPANY_OPERATOR_TOKEN,
            False,
        )

        token_to_delete = (
            session.query(OperatorToken)
            .filter(OperatorToken.id == id)
            .filter(OperatorToken.company_id == token.company_id)
            .filter(OperatorToken.is_revoked.is_(False))
            .first()
        )
        if token_to_delete is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if not has_permission and token_to_delete.operator_id != token.operator_id:
            raise exceptions.NoPermission()

        token_to_delete.is_revoked = True
        session.commit()
        session.refresh(token_to_delete)

        token_log_data = jsonable_encoder(token_to_delete)
        token_log_data.pop(OperatorToken.access_token.name)
        token_log_data.pop(OperatorToken.refresh_token.name)
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
    URL_OPERATOR_TOKEN,
    summary="Fetch operator token",
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
async def fetch_operator_tokens_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.FETCH_COMPANY_OPERATOR_TOKEN)

        return search_operator_tokens(session, QueryParams(**query_params.model_dump()))
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_OPERATOR_TOKEN}/{{id}}",
    summary="Delete operator token",
    tags=["Operator Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an operator access token.**    
            - Executive must have a valid access token.    
            - Executive must have 'company.operator.token.delete' permission.    
            - Executive can delete any operator's token.    
            - If the token ID is invalid or already revoked, the operation is silently ignored.    
        """
    ),
)
async def delete_operator_token_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.DELETE_COMPANY_OPERATOR_TOKEN)

        token_to_delete = (
            session.query(OperatorToken)
            .filter(OperatorToken.id == id)
            .filter(OperatorToken.is_revoked.is_(False))
            .first()
        )
        if token_to_delete is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        # Revoke token
        token_to_delete.is_revoked = True
        session.commit()
        session.refresh(token_to_delete)

        token_log_data = jsonable_encoder(token_to_delete)
        token_log_data.pop(OperatorToken.access_token.name)
        token_log_data.pop(OperatorToken.refresh_token.name)
        log_event(token, request_info, token_log_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
