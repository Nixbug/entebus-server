"""
Executive Token API Router for EnteBus.

Provides endpoints for managing executive access tokens, including creation,
regeneration, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime, timedelta, timezone
from secrets import token_hex
from typing import Optional
from fastapi import APIRouter, Depends, status, Form
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import bearer_executive
from app.src.constants import MAX_EXECUTIVE_TOKENS, MAX_TOKEN_VALIDITY
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
        expires_in (int): Token validity duration in seconds.
        platform_type (int): Platform type enum value.
        client_details (Optional[str]): Optional details about the client.
        created_on (datetime): Token creation timestamp.
    """

    id: int
    executive_id: int
    expires_in: int
    platform_type: int
    client_details: Optional[str]
    created_on: datetime


class ExecutiveTokenSchema(MaskedExecutiveTokenSchema):
    """
    Schema for executive token response including the access token.

    Attributes:
        access_token (str): The generated access token.
        token_type (Optional[str]): Type of the token (default: "bearer").
    """

    access_token: str
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

    - Verify the `username` exists in the database.
    - Verify the `password` using a secure hash check (argon2).
    - Ensure the executive account is in `active status`.
    - Limits active tokens using `MAX_EXECUTIVE_TOKENS` (token rotation).
    - Sets expiration with expires_in=`MAX_TOKEN_VALIDITY` (in seconds).
    - The expiration timestamp `expires_at` is calculated and stored in utc.
    - Log the authentication event for auditing, excluding the access token itself for security.
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
            .order_by(ExecutiveToken.created_on.desc())
            .all()
        )
        if len(tokens) >= MAX_EXECUTIVE_TOKENS:
            token = tokens[MAX_EXECUTIVE_TOKENS - 1]
            session.delete(token)
            session.flush()

        # Create a new token
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=MAX_TOKEN_VALIDITY)
        token = ExecutiveToken(
            executive_id=executive.id,
            expires_in=MAX_TOKEN_VALIDITY,
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
        log_event(token, request_info, token_log_data)
        return token_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.post(
    URL_EXECUTIVE_TOKEN+"/refresh",
    tags=["Token"],
    response_model=ExecutiveTokenSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ExecutiveToken.id),
        ]
    ),
)
async def regenerate_token(
    fParam: UpdateForm = Depends(),
    bearer=Depends(bearer_executive),
    request_info=Depends(get_request_info),
):
    """
    **Regenerate an executive access token after validating the current one or a specific one.**

    - Validates the current token.
    - If `id` is provided:
        - Verifies that the token exists in the database.
        - Ensures it matches the current token's `access_token` to prevent unauthorized regeneration attempts.
    - If `id` is not provided:
        - Uses the currently authenticated token.
    - Deletes the old token record to prevent reuse.
    - Creates a **new token record** with new `access_token`, `expires_in`, and `expires_at`.
    - Logs the token related event for auditing, excluding the new access token for security.
    """
    try:
        session = SessionLocal()
        token = validate_executive_token(bearer.credentials, session)

        # Determine which token is being regenerated
        if fParam.id is None:
            current_token = token
        else:
            current_token = (
                session.query(ExecutiveToken)
                .filter(ExecutiveToken.id == fParam.id)
                .first()
            )
            if current_token is None:
                raise exceptions.UnknownValue(ExecutiveToken.id)
            if current_token.access_token != token.access_token:
                raise exceptions.NoPermission()

        # Remove the current token (old record)
        session.delete(current_token)
        session.flush()

        # Create a new token record
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=MAX_TOKEN_VALIDITY)
        regenerate_token = ExecutiveToken(
            executive_id=token.executive_id,
            expires_in=MAX_TOKEN_VALIDITY,
            expires_at=expires_at,
            platform_type=token.platform_type,
            client_details=token.client_details,
        )
        session.add(regenerate_token)
        session.commit()
        session.refresh(regenerate_token)

        # Log without sensitive fields
        token_data = jsonable_encoder(regenerate_token)
        token_log_data = token_data.copy()
        token_log_data.pop("access_token", None)
        log_event(regenerate_token, request_info, token_log_data)
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
