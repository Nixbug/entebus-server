from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, status, Form
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.src.constants import MAX_EXECUTIVE_TOKENS, MAX_TOKEN_VALIDITY
from app.src.db import Executive, ExecutiveToken, SessionLocal
from app.src import argon2, exceptions, getters
from app.src.enums import AccountStatus, PlatformType
from app.src.loggers import log_event
from app.src.functions import enum_str, fuse_exception_responses
from app.src.urls import URL_EXECUTIVE_TOKEN

route_executive = APIRouter()


## Output Schema
class MaskedExecutiveTokenSchema(BaseModel):
    id: int
    executive_id: int
    expires_in: int
    platform_type: int
    client_details: Optional[str]
    updated_on: Optional[datetime]
    created_on: datetime


class ExecutiveTokenSchema(MaskedExecutiveTokenSchema):
    access_token: str
    token_type: Optional[str] = "bearer"


## Input Forms
class CreateForm(BaseModel):
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
    description="""
    Issues a new access token for an executive after validating credentials.    
    If the credentials are valid and the executive account is active, a new token is generated and returned.    
    Limits active tokens using MAX_EXECUTIVE_TOKENS (token rotation).   
    Sets expiration with expires_in=MAX_TOKEN_VALIDITY (in seconds).    
    Logs the authentication event for audit tracking.
    """,
)
async def create_token(
    fParam: CreateForm = Depends(),
    request_info=Depends(getters.request_info),
):
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
