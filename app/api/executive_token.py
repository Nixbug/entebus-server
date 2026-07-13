"""
Executive Token API router.

Provides endpoints for managing executive tokens:
    - POST (executive)
    - POST /refresh (executive)
    - POST /revoke (executive)
    - DELETE (executive)
    - GET (executive)
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Optional
from fastapi import APIRouter, Depends, Form, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive
from app.src.db import Executive, ExecutiveToken, get_db_session
from app.src import exceptions, schemas
from app.src.schemas import PatchForm
from app.src.description import Description
from app.src.enums import PlatformType, GrantType, OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    ClientDataFilter,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath
from app.src.urls import URL_EXECUTIVE_TOKEN
from app.src.constants import (
    MAX_ACCESS_TOKEN_VALIDITY,
    MAX_EXECUTIVE_TOKENS,
    MAX_REFRESH_TOKEN_VALIDITY,
)
from app.src.validators import (
    verify_permission,
    verify_token,
    authenticate_executive,
    validate_and_revoke_refresh_token,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_client_data_filters,
    cleanup_old_tokens,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    get_executive_roles,
)

route_executive = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class MaskedExecutiveTokenSchema(BaseModel):
    """Schema for executive token response without revealing the tokens."""

    id: int
    executive_id: int
    expires_in: int
    refresh_before: datetime
    platform_type: int
    client_details: Optional[str]
    created_on: datetime


class ExecutiveTokenSchema(MaskedExecutiveTokenSchema):
    """Schema for executive token response including the tokens."""

    access_token: str
    refresh_token: str
    token_type: Optional[str] = "bearer"


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """Form data for creating a new executive token."""

    platform_type: PlatformType = Field(
        Form(description=enum_str(PlatformType), default=PlatformType.OTHER)
    )
    client_details: str | None = Field(Form(max_length=1024, default=None))


class UpdateForm(PatchForm):
    """Form data for refreshing an executive token."""

    refresh_token: str = Field(Form())
    grant_type: GrantType = Field(
        Form(description=enum_str(GrantType), default=GrantType.REFRESH_TOKEN)
    )


class LogoutForm(BaseModel):
    """Form data for logging out with an executive token."""

    token: str = Field(Form(description="Access or refresh token"))


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"


class QueryParams(ClientDataFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for executive token endpoints."""

    executive_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
def executive_token_to_dict(executive_token: ExecutiveToken) -> tuple[dict, dict]:
    """
    Convert an ExecutiveToken SQLAlchemy model instance to a dictionary.

    Args:
        executive_token (ExecutiveToken): ExecutiveToken model instance.

    Returns:
        Tuple[dict, dict]:
            - dict: JSON-encoded representation of the executive token.
            - dict: Log data related to the executive token.
    """
    executive_token_data = jsonable_encoder(executive_token)
    executive_token_log_data = executive_token_data.copy()
    executive_token_log_data.pop(ExecutiveToken.access_token.name)
    executive_token_log_data.pop(ExecutiveToken.refresh_token.name)
    return executive_token_data, executive_token_log_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_executive_token(
    session: Session,
    form_param: CreateForm,
    executive: Executive,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new executive token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new executive token.
        executive (Executive): Executive for whom the token is being created.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created executive token data.
    """
    cleanup_old_tokens(
        session,
        ExecutiveToken,
        ExecutiveToken.executive_id == executive.id,
        MAX_EXECUTIVE_TOKENS - 1,
    )

    executive_token = ExecutiveToken(
        executive_id=executive.id,
        platform_type=form_param.platform_type,
        client_details=form_param.client_details,
    )
    session.add(executive_token)
    session.commit()
    session.refresh(executive_token)

    executive_token_data, executive_token_log_data = executive_token_to_dict(
        executive_token
    )
    log_event(executive_token, request_info, executive_token_log_data)
    return executive_token_data


def refresh_executive_token(
    session: Session,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Refresh an executive token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Refreshed executive token data.
    """
    token.is_revoked = True
    cleanup_old_tokens(
        session,
        ExecutiveToken,
        ExecutiveToken.executive_id == token.executive_id,
        MAX_EXECUTIVE_TOKENS - 1,
    )

    executive_token = ExecutiveToken(
        executive_id=token.executive_id,
        platform_type=token.platform_type,
        client_details=token.client_details,
    )
    session.add(executive_token)
    session.commit()
    session.refresh(executive_token)
    executive_token_data, executive_token_log_data = executive_token_to_dict(
        executive_token
    )
    log_event(token, request_info, executive_token_log_data)
    return executive_token_data


def revoke_executive_token(
    session: Session,
    form_param: LogoutForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> None:
    """
    Revoke an executive token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (LogoutForm): Form data containing the token to revoke.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    executive_token = (
        session.query(ExecutiveToken)
        .filter(ExecutiveToken.executive_id == token.executive_id)
        .filter(
            (ExecutiveToken.access_token == form_param.token)
            | (ExecutiveToken.refresh_token == form_param.token)
        )
        .filter(ExecutiveToken.is_revoked.is_(False))
        .first()
    )
    if executive_token is None:
        return

    executive_token.is_revoked = True
    session.commit()
    session.refresh(executive_token)
    _, executive_token_log_data = executive_token_to_dict(executive_token)
    log_event(token, request_info, executive_token_log_data)


def delete_executive_token(
    session: Session,
    executive_token: ExecutiveToken,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> None:
    """
    Delete an executive token from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        executive_token (ExecutiveToken): Executive token to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    executive_token.is_revoked = True
    session.commit()
    session.refresh(executive_token)
    _, executive_token_log_data = executive_token_to_dict(executive_token)
    log_event(token, request_info, executive_token_log_data)


def search_executive_tokens(
    session: Session,
    query_params: QueryParams,
) -> list[ExecutiveToken]:
    """
    Search for executive tokens based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve executive tokens that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.
        token (ExecutiveToken): Authenticated executive token.
        has_permission (bool): Whether the executive has permission to view other executives' tokens.

    Returns:
        list[ExecutiveToken]: List of executive tokens that match the search criteria.
    """
    query = session.query(ExecutiveToken).filter(ExecutiveToken.is_revoked.is_(False))

    # Generalized filters
    query = apply_id_filters(query, ExecutiveToken, query_params)
    query = apply_created_on_filters(query, ExecutiveToken, query_params)
    query = apply_client_data_filters(query, ExecutiveToken, query_params)

    # Ordering and pagination
    ordering_attr = getattr(ExecutiveToken, OrderBy(query_params.order_by).value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    executive_tokens = query.all()
    return executive_tokens


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InactiveAccount(),
    exceptions.InvalidCredentials(),
    exceptions.InvalidGrantType(),
]

REFRESH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.UnknownValue(ExecutiveToken.refresh_token),
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
    .add_head("Issue a new access token for an executive after validating credentials.")
    .add_line("Verify the username and password.")
    .add_line(
        "Ensure the executive account is in `active status` before allowing token creation."
    )
    .add_line(
        f"Maintain a limit of `{MAX_EXECUTIVE_TOKENS}` active tokens per executive to control token rotation."
    )
    .add_line(
        "Generate a new Executive Token with a pair of access and refresh tokens."
    )
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
    .add_head("Refresh an executive's access token using a valid refresh token.")
    .add_line("Verify the provided refresh token exists in the database.")
    .add_line("Invalidate the current refresh token.")
    .add_line(
        "Generate a new `Executive Token` with a pair of access and refresh tokens."
    )
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
    .add_head("Revoke or logout an executive token.")
    .add_line("Executive must have a valid access token.")
    .add_line("Revokes the token (access or refresh) specified in the request body.")
    .add_line(
        "If the token is invalid, doesn't belong to the executive, or is already revoked, the operation is silently ignored."
    )
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Delete an executive token.")
    .add_line("Executive must have a valid access token.")
    .add_line("Executives can delete their own tokens without additional permissions.")
    .add_line(
        "To delete another executive's token, the `executive.token.delete` permission is required."
    )
    .add_line(
        "If the token ID is invalid or already revoked, the operation is silently ignored."
    )
)

GET_DESCRIPTION = (
    Description()
    .add_head("Fetch executive tokens.")
    .add_line("Executive must have a valid access token.")
    .add_line("Fetch executive tokens with permission-based filtering.")
    .add_line(
        "If the logged-in executive has `executive.token.fetch` permission, all masked tokens are returned."
    )
    .add_line(
        "If the logged-in executive does not have permission, only masked tokens for the logged-in executive are returned."
    )
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_TOKEN,
    summary="Create executive token",
    tags=["Token"],
    response_model=ExecutiveTokenSchema,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=POST_DESCRIPTION.to_string(),
)
async def create_executive_token_for_executive(
    form_param: CreateForm = Depends(),
    credentials: OAuth2PasswordRequestForm = Depends(),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        executive = authenticate_executive(session, credentials)
        return create_executive_token(session, form_param, executive, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.post(
    f"{URL_EXECUTIVE_TOKEN}/refresh",
    summary="Refresh executive token",
    tags=["Token"],
    response_model=ExecutiveTokenSchema,
    responses=fuse_exception_responses(REFRESH_EXCEPTIONS),
    description=REFRESH_DESCRIPTION.to_string(),
)
async def refresh_executive_token_for_executive(
    form_param: UpdateForm = Depends(),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = validate_and_revoke_refresh_token(session, ExecutiveToken, form_param)
        return refresh_executive_token(session, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.post(
    f"{URL_EXECUTIVE_TOKEN}/revoke",
    summary="Revoke executive token",
    tags=["Token"],
    responses=fuse_exception_responses(REVOKE_EXCEPTIONS),
    description=REVOKE_DESCRIPTION.to_string(),
)
async def revoke_executive_token_for_executive(
    form_param: LogoutForm = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, ExecutiveToken, access_token)
        revoke_executive_token(session, form_param, token, request_info)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_EXECUTIVE_TOKEN}/{{id}}",
    summary="Delete executive token",
    tags=["Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=DELETE_DESCRIPTION.to_string(),
)
async def delete_executive_token_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        has_permission = verify_permission(
            roles,
            PermissionPath.DELETE_EXECUTIVE_TOKEN,
            False,
        )

        executive_token = get_by_id(session, ExecutiveToken, id)
        if executive_token is not None and not executive_token.is_revoked:
            if (
                not has_permission
                and executive_token.executive_id != token.executive_id
            ):
                raise exceptions.NoPermission()

            delete_executive_token(session, executive_token, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_EXECUTIVE_TOKEN,
    summary="Fetch executive token",
    tags=["Token"],
    response_model=list[MaskedExecutiveTokenSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_executive_tokens_for_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        has_permission = verify_permission(
            roles,
            PermissionPath.FETCH_EXECUTIVE_TOKEN,
            False,
        )
        if not has_permission:
            if (
                query_params.executive_id is not None
                and query_params.executive_id != token.executive_id
            ):
                raise exceptions.NoPermission()
            query_params.executive_id = token.executive_id
        return search_executive_tokens(session, query_params)
    except Exception as e:
        exceptions.handle(e)
