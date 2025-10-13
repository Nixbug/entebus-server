"""
Executive Token API Router

This module defines the FastAPI endpoints and associated schemas for managing
executive access tokens. It provides functionality for creating, refreshing,
deleting, and fetching executive tokens while enforcing authentication,
account status checks, and token rotation limits.

Key Features:
    - Logging: Authentication events are logged for auditing purposes.
    - Input Validation: Uses Pydantic models and FastAPI Form/Schema validation.
    - Output Schemas: Returns masked or full token information as needed.

Schemas:
    - MaskedExecutiveTokenSchema: Token metadata excluding the access token.
    - ExecutiveTokenSchema: Full token metadata including the access token.
    - CreateForm: Input form for creating a new token.
    - UpdateForm: Input form for updating an existing token.
    - DeleteForm: Input form for deleting a token.
    - QueryForm: Input form for fetching token details.

Endpoints:
    - POST /executive/token: Create a new token for an executive.
    - PATCH /executive/token: Refresh an existing token.
    - DELETE /executive/token: Delete a token.
    - GET /executive/token: Fetch token details.

Dependencies:
    - argon2: Password hashing and verification.
    - SessionLocal: SQLAlchemy session factory.
    - enums: AccountStatus and PlatformType.
    - loggers: Event logging.
    - constants: MAX_EXECUTIVE_TOKENS, MAX_TOKEN_VALIDITY.
    - exceptions: Custom API exceptions.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, status, Form
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.src.constants import MAX_EXECUTIVE_TOKENS, MAX_TOKEN_VALIDITY
from app.src.db import Executive, ExecutiveToken, SessionLocal
from app.src import argon2, exceptions
from app.src.enums import AccountStatus, PlatformType
from app.src.openobserve import log_event
from app.src.functions import enum_str, fuse_exception_responses, get_request_info
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
        updated_on (Optional[datetime]): Last updated timestamp.
        created_on (datetime): Token creation timestamp.
    """

    id: int
    executive_id: int
    expires_in: int
    platform_type: int
    client_details: Optional[str]
    updated_on: Optional[datetime]
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


@route_executive.patch(
    URL_EXECUTIVE_TOKEN,
    tags=["Token"],
)
async def refresh_token():
    pass


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
