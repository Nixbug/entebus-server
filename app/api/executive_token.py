"""
Executive Token API Router for EnteBus.

Provides endpoints for managing executive access tokens, including creation,
refresh, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import IntEnum
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Response, status, Form
from pydantic import BaseModel, Field

from app.api.bearer import bearer_executive
from app.src.db import Executive, ExecutiveToken, SessionLocal
from app.src import exceptions
from app.src.enums import PlatformType, GrantType
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath
from app.src.urls import URL_EXECUTIVE_TOKEN
from app.src.constants import MAX_EXECUTIVE_TOKENS
from app.src.validators import (
    verify_permission,
    verify_token,
    authenticate_user,
    validate_and_revoke_refresh_token,
)
from app.src.functions import (
    cleanup_old_tokens,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    token_to_json,
    get_executive_roles,
)

route_executive = APIRouter()


## Output Schema
class MaskedExecutiveTokenSchema(BaseModel):
    """Schema for executive token response without revealing the tokens."""

    id: int
    executive_id: int
    expires_in: int
    refresh_before: datetime
    platform_type: int
    client_details: Optional[str]
    updated_on: Optional[datetime]
    created_on: datetime


class ExecutiveTokenSchema(MaskedExecutiveTokenSchema):
    """Schema for executive token response including the tokens."""

    access_token: str
    refresh_token: str
    token_type: Optional[str] = "bearer"


## Input Forms
class CreateForm(BaseModel):
    """Form data for creating a new executive token."""

    username: str = Field(Form(max_length=32))
    password: str = Field(Form(max_length=32))
    platform_type: PlatformType = Field(
        Form(description=enum_str(PlatformType), default=PlatformType.OTHER)
    )
    client_details: str | None = Field(Form(max_length=1024, default=None))
    grant_type: GrantType = Field(
        Form(description=enum_str(GrantType), default=GrantType.PASSWORD)
    )


class UpdateForm(BaseModel):
    """Form data for refreshing an executive token."""

    refresh_token: str = Field(Form())
    grant_type: GrantType = Field(
        Form(description=enum_str(GrantType), default=GrantType.REFRESH_TOKEN)
    )


class DeleteForm(BaseModel):
    """Form data for deleting an executive token."""

    id: int | None = Field(Form(default=None))


## Query Parameters
class OrderIn(IntEnum):
    """Enum for ordering results."""

    ASC = 1
    DESC = 2


class OrderBy(IntEnum):
    """Enum for ordering results."""

    id = 1
    updated_on = 2
    created_on = 3


class QueryParams(BaseModel):
    """Query parameters for executive token endpoints."""

    executive_id: int | None = Field(Query(default=None))
    platform_type: PlatformType | None = Field(
        Query(default=None, description=enum_str(PlatformType))
    )
    client_details: str | None = Field(Query(default=None))
    # id based
    id: int | None = Field(Query(default=None))
    id_ge: int | None = Field(Query(default=None))
    id_le: int | None = Field(Query(default=None))
    id_list: List[int] | None = Field(Query(default=None))
    # updated_on based
    updated_on_ge: datetime | None = Field(Query(default=None))
    updated_on_le: datetime | None = Field(Query(default=None))
    # created_on based
    created_on_ge: datetime | None = Field(Query(default=None))
    created_on_le: datetime | None = Field(Query(default=None))
    # Ordering
    order_by: OrderBy = Field(Query(default=OrderBy.id, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESC, description=enum_str(OrderIn))
    )
    # Pagination
    offset: int = Field(Query(default=0, ge=0))
    limit: int = Field(Query(default=20, gt=0, le=100))


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_TOKEN,
    tags=["Token"],
    response_model=ExecutiveTokenSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InactiveAccount(), exceptions.InvalidCredentials()]
    ),
)
async def create_token(
    form_param: CreateForm = Depends(),
    request_info=Depends(get_request_info),
):
    """
    **Issue a new access token for an executive after validating credentials.**

    - Verify the `username` and `password`.
    - Ensure the executive account is in `active status` before allowing token creation.
    - Maintain a limit on the number of active tokens based on `MAX_EXECUTIVE_TOKENS` to control token rotation.
    - **Token Creation**
        - Generate a new `Executive Token` with a pair of access and refresh tokens.
        - The `expires_in` indicates the number of seconds until the access token expires (based on MAX_ACCESS_TOKEN_VALIDITY).
        - The `refresh_before` indicates the datetime when the refresh token expires (based on MAX_REFRESH_TOKEN_VALIDITY).
        - A new access token can be generated by using the refresh token before it expires.
    """
    try:
        session = SessionLocal()
        executive = authenticate_user(session, Executive, form_param)

        # Remove excess tokens
        cleanup_old_tokens(
            session,
            ExecutiveToken,
            ExecutiveToken.executive_id == executive.id,
            MAX_EXECUTIVE_TOKENS - 1,
        )

        # Create new token
        token = ExecutiveToken(
            executive_id=executive.id,
            platform_type=form_param.platform_type,
            client_details=form_param.client_details,
        )
        session.add(token)
        session.commit()
        session.refresh(token)

        token_data, token_log_data = token_to_json(token)
        log_event(token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.post(
    URL_EXECUTIVE_TOKEN + "/refresh",
    tags=["Token"],
    response_model=ExecutiveTokenSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.UnknownValue(ExecutiveToken.refresh_token),
        ]
    ),
)
async def refresh_token(
    form_param: UpdateForm = Depends(),
    request_info=Depends(get_request_info),
):
    """
    **Refresh an executive's access token using a valid refresh token.**

    - Verify the provided refresh token exists in the database.
    - Invalidate the current refresh token.
    - **Token Creation**
        - Generate a new `Executive Token` with a pair of access and refresh tokens.
        - The `expires_in` indicates the number of seconds until the access token expires (based on MAX_ACCESS_TOKEN_VALIDITY).
        - The `refresh_before` indicates the datetime when the refresh token expires (based on MAX_REFRESH_TOKEN_VALIDITY).
        - A new access token can be generated by using the refresh token before it expires.
    """
    try:
        session = SessionLocal()
        # Validate and revoke the old refresh token
        token = validate_and_revoke_refresh_token(session, ExecutiveToken, form_param)
        # Create new token
        refresh_token = ExecutiveToken(
            executive_id=token.executive_id,
            platform_type=token.platform_type,
            client_details=token.client_details,
        )
        session.add(refresh_token)
        session.flush()

        # Remove excess tokens
        cleanup_old_tokens(
            session,
            ExecutiveToken,
            ExecutiveToken.executive_id == token.executive_id,
            MAX_EXECUTIVE_TOKENS,
        )
        session.commit()
        session.refresh(refresh_token)

        token_data, token_log_data = token_to_json(refresh_token)
        log_event(refresh_token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    URL_EXECUTIVE_TOKEN,
    tags=["Token"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
        ]
    ),
)
async def delete_token(
    form_param: DeleteForm = Depends(),
    bearer=Depends(bearer_executive),
    request_info=Depends(get_request_info),
):
    """
    **Revokes an access token associated with an executive account.**

    - Verify that the provided access token exists and is valid.
    - If no `id` is provided, the currently used token will be revoked.
    - If an `id` is provided, the specified token will be revoked after validating user permissions 'executive.token.delete'.
    - If the token id is invalid or already revoked, the operation is silently ignored.
    """

    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, bearer.credentials)
        if form_param.id is None:
            token_to_delete = token
        else:
            token_to_delete = (
                session.query(ExecutiveToken)
                .filter(ExecutiveToken.id == form_param.id)
                .filter(ExecutiveToken.is_revoked == False)
                .first()
            )
            if token_to_delete is None:
                return Response(status_code=status.HTTP_204_NO_CONTENT)

            is_self_delete = token_to_delete.id == token.id

            if not is_self_delete:
                roles = get_executive_roles(session, token.executive_id)
                verify_permission(
                    roles,
                    PermissionPath.DELETE_EXECUTIVE_TOKEN,
                )

        # Revoke the chosen token
        token_to_delete.is_revoked = True
        session.commit()

        _, token_log_data = token_to_json(token_to_delete)
        log_event(token_to_delete, request_info, token_log_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_EXECUTIVE_TOKEN,
    tags=["Token"],
    response_model=list[MaskedExecutiveTokenSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
)
async def fetch_token(
    query_params: QueryParams = Depends(),
    bearer=Depends(bearer_executive),
):
    """
    **Fetch executive tokens with permission-based filtering.**

    - If the logged-in executive has `executive.token.fetch` permission, all masked tokens are returned.
    - If the logged-in executive does not have permission, only masked tokens for the logged-in executive are returned.
    - Supports filtering by ID, ID ranges, lists, creation date, and update date.
    - Allows sorting by ID, creation date, or update date in ascending or descending order.
    - Supports pagination using `offset` and `limit` parameters.
    """
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, bearer.credentials)
        query = session.query(ExecutiveToken).filter(ExecutiveToken.is_revoked == False)
        roles = get_executive_roles(session, token.executive_id)
        has_permission = verify_permission(
            roles,
            PermissionPath.FETCH_EXECUTIVE_TOKEN,
            False,
        )

        if query_params.executive_id is not None:
            query = query.filter(
                ExecutiveToken.executive_id == query_params.executive_id
            )
        if has_permission is False:
            query = query.filter(ExecutiveToken.executive_id == token.executive_id)
        if query_params.platform_type is not None:
            query = query.filter(
                ExecutiveToken.platform_type == query_params.platform_type
            )
        if query_params.client_details is not None:
            query = query.filter(
                ExecutiveToken.client_details.ilike(f"%{query_params.client_details}%")
            )
        if query_params.id is not None:
            query = query.filter(ExecutiveToken.id == query_params.id)
        if query_params.id_ge is not None:
            query = query.filter(ExecutiveToken.id >= query_params.id_ge)
        if query_params.id_le is not None:
            query = query.filter(ExecutiveToken.id <= query_params.id_le)
        if query_params.id_list is not None:
            query = query.filter(ExecutiveToken.id.in_(query_params.id_list))
        if query_params.updated_on_ge is not None:
            query = query.filter(
                ExecutiveToken.updated_on >= query_params.updated_on_ge
            )
        if query_params.updated_on_le is not None:
            query = query.filter(
                ExecutiveToken.updated_on <= query_params.updated_on_le
            )
        if query_params.created_on_ge is not None:
            query = query.filter(
                ExecutiveToken.created_on >= query_params.created_on_ge
            )
        if query_params.created_on_le is not None:
            query = query.filter(
                ExecutiveToken.created_on <= query_params.created_on_le
            )
        # Ordering
        ordering_attr = getattr(ExecutiveToken, OrderBy(query_params.order_by).name)
        if query_params.order_in == OrderIn.ASC:
            query = query.order_by(ordering_attr.asc())
        else:
            query = query.order_by(ordering_attr.desc())
        # Pagination
        query = query.offset(query_params.offset).limit(query_params.limit)

        tokens = query.all()
        return tokens
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
