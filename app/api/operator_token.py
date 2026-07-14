"""
Operator Token API router.

Provides endpoints for managing operator tokens:
    - POST (operator)
    - POST /refresh (operator)
    - POST /revoke (operator)
    - DELETE (operator, executive)
    - GET (operator, executive)
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Optional
from fastapi import APIRouter, Depends, Form, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions, schemas
from app.src.constants import (
    MAX_ACCESS_TOKEN_VALIDITY,
    MAX_OPERATOR_TOKENS,
    MAX_REFRESH_TOKEN_VALIDITY,
)
from app.src.db import (
    ExecutiveToken,
    Operator,
    OperatorToken,
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
    get_operator_roles,
    get_request_info,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.schemas import PatchForm
from app.src.urls import URL_OPERATOR_TOKEN
from app.src.validators import (
    authenticate_operator,
    authorize_executive,
    validate_and_revoke_refresh_token,
    verify_permission,
    verify_token,
)

route_operator = APIRouter()
route_executive = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """Form data for creating a new operator token."""

    company_id: int = Field(Form())
    platform_type: PlatformType = Field(
        Form(description=enum_str(PlatformType), default=PlatformType.OTHER)
    )
    client_details: str | None = Field(Form(max_length=1024, default=None))


class UpdateForm(PatchForm):
    """Form data for refreshing an operator token."""

    refresh_token: str = Field(Form())
    grant_type: GrantType = Field(
        Form(description=enum_str(GrantType), default=GrantType.REFRESH_TOKEN)
    )


class LogoutForm(BaseModel):
    """Form data for logging out with an operator token."""

    token: str = Field(Form(description="Access or refresh token"))


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"


class QueryParamsForOP(ClientDataFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for operator endpoints."""

    operator_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executive endpoints."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
def operator_token_to_dict(operator_token: OperatorToken) -> tuple[dict, dict]:
    """
    Convert an OperatorToken SQLAlchemy model instance to a dictionary.

    Args:
        operator_token (OperatorToken): OperatorToken model instance.

    Returns:
        tuple[dict, dict]:
            - dict: JSON-encoded representation of the operator token.
            - dict: Log data related to the operator token.
    """
    operator_token_data = jsonable_encoder(operator_token)
    operator_token_log_data = operator_token_data.copy()
    operator_token_log_data.pop(OperatorToken.access_token.name)
    operator_token_log_data.pop(OperatorToken.refresh_token.name)
    return operator_token_data, operator_token_log_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_operator_token(
    session: Session,
    form_param: CreateForm,
    operator: Operator,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new operator token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new operator token.
        operator (Operator): Operator for whom the token is being created.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created operator token data.
    """
    cleanup_old_tokens(
        session,
        OperatorToken,
        OperatorToken.operator_id == operator.id,
        MAX_OPERATOR_TOKENS - 1,
    )

    operator_token = OperatorToken(
        company_id=form_param.company_id,
        operator_id=operator.id,
        platform_type=form_param.platform_type,
        client_details=form_param.client_details,
    )
    session.add(operator_token)
    session.commit()
    session.refresh(operator_token)

    operator_token_data, operator_token_log_data = operator_token_to_dict(
        operator_token
    )
    log_event(operator_token, request_info, operator_token_log_data)
    return operator_token_data


def refresh_operator_token(
    session: Session,
    token: OperatorToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Refresh an operator token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        token (OperatorToken): Authenticated operator token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Refreshed operator token data.
    """
    token.is_revoked = True
    cleanup_old_tokens(
        session,
        OperatorToken,
        OperatorToken.operator_id == token.operator_id,
        MAX_OPERATOR_TOKENS - 1,
    )

    operator_token = OperatorToken(
        company_id=token.company_id,
        operator_id=token.operator_id,
        platform_type=token.platform_type,
        client_details=token.client_details,
    )
    session.add(operator_token)
    session.commit()
    session.refresh(operator_token)
    operator_token_data, operator_token_log_data = operator_token_to_dict(
        operator_token
    )
    log_event(token, request_info, operator_token_log_data)
    return operator_token_data


def revoke_operator_token(
    session: Session,
    form_param: LogoutForm,
    token: OperatorToken,
    request_info: schemas.RequestInfo,
) -> None:
    """
    Revoke an operator token in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (LogoutForm): Form data containing the token to revoke.
        token (OperatorToken): Authenticated operator token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    operator_token = (
        session.query(OperatorToken)
        .filter(OperatorToken.operator_id == token.operator_id)
        .filter(
            (OperatorToken.access_token == form_param.token)
            | (OperatorToken.refresh_token == form_param.token)
        )
        .filter(OperatorToken.is_revoked.is_(False))
        .first()
    )
    if operator_token is None:
        return

    operator_token.is_revoked = True
    session.commit()
    session.refresh(operator_token)
    _, operator_token_log_data = operator_token_to_dict(operator_token)
    log_event(token, request_info, operator_token_log_data)


def delete_operator_token(
    session: Session,
    operator_token: OperatorToken,
    token: OperatorToken | ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> None:
    """
    Delete an operator token from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        operator_token (OperatorToken): Operator token to be deleted.
        token (OperatorToken | ExecutiveToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    operator_token.is_revoked = True
    session.commit()
    session.refresh(operator_token)
    _, operator_token_log_data = operator_token_to_dict(operator_token)
    log_event(token, request_info, operator_token_log_data)


def search_operator_tokens(
    session: Session, query_params: QueryParams
) -> list[OperatorToken]:
    """
    Search for operator tokens based on provided query parameters.

    This function supports multiple filtering, ordering, and
    pagination capabilities to retrieve operator tokens that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.
        token (OperatorToken | None): Authenticated operator token for scoped access.
        has_permission (bool): Whether the requester can view an operator's tokens.

    Returns:
        list[OperatorToken]: List of operator tokens that match the search criteria.
    """
    query = session.query(OperatorToken).filter(OperatorToken.is_revoked.is_(False))
    if query_params.company_id is not None:
        query = query.filter(OperatorToken.company_id == query_params.company_id)
    if query_params.operator_id is not None:
        query = query.filter(OperatorToken.operator_id == query_params.operator_id)

    # Generalized filters
    query = apply_id_filters(query, OperatorToken, query_params)
    query = apply_created_on_filters(query, OperatorToken, query_params)
    query = apply_client_data_filters(query, OperatorToken, query_params)

    # Ordering and pagination
    ordering_attr = getattr(OperatorToken, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    operator_tokens = query.all()
    return operator_tokens


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InactiveAccount(),
    exceptions.InvalidCredentials(),
    exceptions.UnknownValue(Operator.company_id),
    exceptions.InvalidGrantType(),
]

REFRESH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.UnknownValue(OperatorToken.refresh_token),
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
    .add_head("Issue a new access token for an operator after validating credentials.")
    .add_line("Verify the username and password.")
    .add_line(
        "Ensure the operator account is in `active status` before allowing token creation."
    )
    .add_line(
        f"Maintain a limit of `{MAX_OPERATOR_TOKENS}` active tokens per operator to control token rotation."
    )
    .add_line("Generate a new Operator Token with a pair of access and refresh tokens.")
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
    .add_head("Refresh an operator's access token using a valid refresh token.")
    .add_line("Verify the provided refresh token exists in the database.")
    .add_line("Invalidate the current refresh token.")
    .add_line(
        "Generate a new `Operator Token` with a pair of access and refresh tokens."
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
    .add_head("Revoke or logout an operator token.")
    .add_line("Revokes the token (access or refresh) specified in the request body.")
    .add_line(
        "If the token is invalid, doesn't belong to the operator, or is already revoked, the operation is silently ignored."
    )
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Delete an operator token.")
    .add_line(
        "If the token ID is invalid or already revoked, the operation is silently ignored."
    )
)

GET_DESCRIPTION = Description().add_head("Fetch operator tokens.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_OPERATOR_TOKEN,
    summary="Fetch operator token",
    tags=["Operator Token"],
    response_model=list[MaskedOperatorTokenSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "If the logged-in executive has `company.operator.token.fetch` permission, all masked tokens are returned."
        )
        .to_string()
    ),
)
async def fetch_operator_tokens_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.FETCH_COMPANY_OPERATOR_TOKEN],
        )
        return search_operator_tokens(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_OPERATOR_TOKEN}/{{id}}",
    summary="Delete operator token",
    tags=["Operator Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.operator.token.delete` permission."
        )
        .to_string()
    ),
)
async def delete_operator_token_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_OPERATOR_TOKEN],
        )
        operator_token = get_by_id(session, OperatorToken, id)
        if operator_token is not None and not operator_token.is_revoked:
            delete_operator_token(session, operator_token, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_TOKEN,
    summary="Create operator token",
    tags=["Token"],
    response_model=OperatorTokenSchema,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_operator_token_for_operator(
    form_param: CreateForm = Depends(),
    credentials: OAuth2PasswordRequestForm = Depends(),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        operator = authenticate_operator(session, credentials, form_param)
        return create_operator_token(session, form_param, operator, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_operator.post(
    f"{URL_OPERATOR_TOKEN}/refresh",
    summary="Refresh operator token",
    tags=["Token"],
    response_model=OperatorTokenSchema,
    responses=fuse_exception_responses(REFRESH_EXCEPTIONS),
    description=(REFRESH_DESCRIPTION.to_string()),
)
async def refresh_operator_token_for_operator(
    form_param: UpdateForm = Depends(),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = validate_and_revoke_refresh_token(session, OperatorToken, form_param)
        return refresh_operator_token(session, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_operator.post(
    f"{URL_OPERATOR_TOKEN}/revoke",
    summary="Revoke operator token",
    tags=["Token"],
    responses=fuse_exception_responses(REVOKE_EXCEPTIONS),
    description=(REVOKE_DESCRIPTION.to_string()),
)
async def revoke_operator_token_for_operator(
    form_param: LogoutForm = Depends(),
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        revoke_operator_token(session, form_param, token, request_info)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_OPERATOR_TOKEN,
    summary="Fetch operator token",
    tags=["Token"],
    response_model=list[MaskedOperatorTokenSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "If the logged-in operator has `company.operator.token.fetch` permission, all masked tokens within the operator's company are returned."
        )
        .add_line(
            "If the logged-in operator does not have permission, only masked tokens for the logged-in operator are returned."
        )
        .add_line(
            "Trying to access tokens of other operators within the same company without permission will result in `NoPermission` error."
        )
        .to_string()
    ),
)
async def fetch_operator_tokens_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        has_permission = verify_permission(
            roles,
            OperatorPermissionPath.FETCH_COMPANY_OPERATOR_TOKEN,
            raise_exception=False,
        )

        if not has_permission:
            if (
                query_params.operator_id is not None
                and query_params.operator_id != token.operator_id
            ):
                raise exceptions.NoPermission()
            query_params.operator_id = token.operator_id
        return search_operator_tokens(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_OPERATOR_TOKEN}/{{id}}",
    summary="Delete operator token",
    tags=["Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "Operators can delete their own tokens without additional permissions."
        )
        .add_line(
            "To delete another operator's token in the same company, the `company.operator.token.delete` permission is required."
        )
        .to_string()
    ),
)
async def delete_operator_token_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        has_permission = verify_permission(
            roles,
            OperatorPermissionPath.DELETE_COMPANY_OPERATOR_TOKEN,
            raise_exception=False,
        )

        operator_token = get_by_id(
            session,
            OperatorToken,
            id,
            extra_filter=(OperatorToken.company_id == token.company_id),
        )
        if operator_token is not None and not operator_token.is_revoked:
            if not has_permission and operator_token.operator_id != token.operator_id:
                raise exceptions.NoPermission()

            delete_operator_token(
                session,
                operator_token,
                token,
                request_info,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
