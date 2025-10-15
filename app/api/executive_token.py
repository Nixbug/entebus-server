"""
Executive Token API Router for EnteBus.

Provides endpoints for managing executive access tokens, including creation,
refresh, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime, timedelta, timezone
from secrets import token_hex
from typing import Optional
from fastapi import APIRouter, Depends, status, Form
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import bearer_executive
from app.src.constants import (
    MAX_EXECUTIVE_TOKENS,
    ACCESS_TOKEN_VALIDITY,
    REFRESH_TOKEN_VALIDITY,
)
from app.src.db import Executive, ExecutiveToken, SessionLocal
from app.src import argon2, exceptions
from app.src.enums import AccountStatus, PlatformType
from app.src.openobserve import log_event
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_request_info,
    validate_executive_token,
)
from app.src.urls import URL_EXECUTIVE_TOKEN

route_executive = APIRouter()


## Output Schema
class MaskedExecutiveTokenSchema(BaseModel):
    """
    Schema for executive token response without revealing the access token.

    Attributes:
        id (int): Token ID.
        executive_id (int): ID of the executive owning the token.
        expires_in (int): Access token validity duration in seconds.
        expires_at (datetime): Refresh token expiration timestamp.
        platform_type (int): Platform type enum value.
        client_details (Optional[str]): Optional details about the client.
        is_revoked (bool): Flag indicating if the token is revoked.
        updated_on (Optional[datetime]): Last updated timestamp.
        created_on (datetime): Token creation timestamp.
    """

    id: int
    executive_id: int
    expires_in: int
    expires_at: datetime
    platform_type: int
    client_details: Optional[str]
    is_revoked: bool
    updated_on: Optional[datetime]
    created_on: datetime


class ExecutiveTokenSchema(MaskedExecutiveTokenSchema):
    """
    Schema for executive token response including the access token.

    Attributes:
        access_token (str): The generated access token.
        refresh_token (str): The generated refresh token.
        token_type (Optional[str]): Type of the token (default: "bearer").
    """

    access_token: str
    refresh_token: str
    token_type: Optional[str] = "bearer"


## Input Forms
class CreateForm(BaseModel):
    """
    Form data for creating a new executive token.

    Attributes:
        username (str): Username of the executive (max 32 chars).
        password (str): Password of the executive (max 32 chars).
        platform_type (PlatformType): Platform type of the request.
        client_details (Optional[str]): Optional client details (max 1024 chars).
    """

    username: str = Field(Form(max_length=32))
    password: str = Field(Form(max_length=32))
    platform_type: PlatformType = Field(
        Form(description=enum_str(PlatformType), default=PlatformType.OTHER)
    )
    client_details: str | None = Field(Form(max_length=1024, default=None))


class UpdateForm(BaseModel):
    """
    Form data for updating an existing executive token.

    Attributes:
        id (Optional[int]): ID of the token to update (default: None).
    """

    id: int | None = Field(Form(default=None))


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
    fParam: CreateForm = Depends(),
    request_info=Depends(get_request_info),
):
    """
    **Issue a new access token for an executive after validating credentials.**

    - **Credential Validation**
        - Verify the `username` exists in the database.
        - Verify the `password` using a secure hash check (argon2).
    - Ensure the executive account is in `active status` before allowing token creation.
    - **Token Rotation & Limit Enforcement**
        - If the total number of tokens exceeds `MAX_EXECUTIVE_TOKENS`:
            - Preferentially remove the oldest `revoked token`.
            - If no revoked tokens exist, remove the `oldest active token`.
        - Maintain a limit on the number of active tokens based on `MAX_EXECUTIVE_TOKENS` to control token rotation.
    - **Token Creation**
        - Generate a new `Executive Token` with a pair of access and refresh tokens.
        - The access token's expiration duration is stored in `expires_on`(in `ACCESS_TOKEN_VALIDITY` - seconds as integer)
        - The refresh token's expiration timestamp is stored in `expires_at` (in `REFRESH_TOKEN_VALIDITY` - seconds as datetime)
        - A new access token can be generated by using the refresh token before it expires.
        - All token timestamps are recorded in UTC to ensure consistency across systems.
        - Additional metadata such as `platform_type` and `client_details`.
    - Logs the authentication event for auditing purposes while excluding sensitive fields like `access_token` and `refresh_token` for security.
    """
    try:
        session = SessionLocal()
        executive = (
            session.query(Executive)
            .filter(Executive.username == fParam.username)
            .first()
        )
        if executive is None:
            raise exceptions.InvalidCredentials()

        if not argon2.check_password(fParam.password, executive.password):
            raise exceptions.InvalidCredentials()
        if executive.status != AccountStatus.ACTIVE:
            raise exceptions.InactiveAccount()

        # Remove excess tokens from DB
        tokens = (
            session.query(ExecutiveToken)
            .filter(ExecutiveToken.executive_id == executive.id)
            .order_by(ExecutiveToken.created_on.asc())
            .all()
        )
        if len(tokens) >= MAX_EXECUTIVE_TOKENS:
            revoked_token = (
                session.query(ExecutiveToken)
                .filter(
                    ExecutiveToken.executive_id == executive.id,
                    ExecutiveToken.is_revoked.is_(True),
                )
                .order_by(ExecutiveToken.created_on.asc())
                .first()
            )
            if revoked_token:
                # Delete revoked token first
                session.delete(revoked_token)
            else:
                # If no revoked tokens, delete the oldest active token
                oldest_token = tokens[0]
                session.delete(oldest_token)
            session.flush()

        # Create a new token
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=REFRESH_TOKEN_VALIDITY
        )
        token = ExecutiveToken(
            executive_id=executive.id,
            expires_in=ACCESS_TOKEN_VALIDITY,
            expires_at=expires_at,
            platform_type=fParam.platform_type,
            client_details=fParam.client_details,
        )
        session.add(token)
        session.commit()
        session.refresh(token)

        token_data = jsonable_encoder(token)
        token_log_data = token_data.copy()
        token_log_data.pop("access_token")
        token_log_data.pop("refresh_token")
        log_event(token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    URL_EXECUTIVE_TOKEN,
    tags=["Token"],
    response_model=ExecutiveTokenSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ExecutiveToken.id),
        ]
    ),
)
async def refresh_token(
    fParam: UpdateForm = Depends(),
    bearer=Depends(bearer_executive),
    request_info=Depends(get_request_info),
):
    """
    **Refresh an executive access token and extend its validity.**

    - Validates the current token.
    - If `id` is not provided, refreshes the current active token.
    - If `id` is provided:
        - Verifies that the token exists in the database.
        - Ensures it matches the current token's `access_token` to prevent unauthorized refresh attempts.
    - Extends token validity by adding `MAX_TOKEN_VALIDITY` seconds to both `expires_in` and `expires_at`.
    - Rotates the `access_token`, immediately invalidating the old one.
    - Logs the token refresh event for auditing, excluding the new access token from the log for security.
    """
    try:
        session = SessionLocal()
        token = validate_executive_token(bearer.credentials, session)

        if fParam.id is None:
            updatable_token = token
        else:
            updatable_token = (
                session.query(ExecutiveToken)
                .filter(ExecutiveToken.id == fParam.id)
                .first()
            )
            if updatable_token is None:
                raise exceptions.UnknownValue(ExecutiveToken.id)
            if updatable_token.access_token != token.access_token:
                raise exceptions.NoPermission()

        updatable_token.expires_in += MAX_TOKEN_VALIDITY
        updatable_token.expires_at += timedelta(seconds=MAX_TOKEN_VALIDITY)
        updatable_token.access_token = token_hex(32)
        session.commit()
        session.refresh(updatable_token)

        token_data = jsonable_encoder(updatable_token)
        token_log_data = token_data.copy()
        token_log_data.pop("access_token")
        log_event(token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    URL_EXECUTIVE_TOKEN,
    tags=["Token"],
)
async def delete_token():
    pass


@route_executive.get(
    URL_EXECUTIVE_TOKEN,
    tags=["Token"],
)
async def fetch_token():
    pass
