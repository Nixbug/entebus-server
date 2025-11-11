"""
Executive Role API Router for EnteBus.

Provides endpoints for managing executive roles, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Body, Response, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import bearer_executive
from app.src.db import ExecutiveRole, ExecutiveToken, SessionLocal
from app.src.permissions.executive import PermissionSchema, PermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_EXECUTIVE_ROLE
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
)

route_executive = APIRouter()


## Output Schema
class ExecutiveRoleSchema(BaseModel):
    """Schema for executive role response."""

    id: int
    name: str
    permissions: PermissionSchema
    created_on: datetime
    updated_on: Optional[datetime]


## Input Forms
class CreateForm(BaseModel):
    """Form data for creating a new executive role."""

    name: str = Field(Body(min_length=1, max_length=32, pattern=NAME_PATTERN))
    permissions: PermissionSchema


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_ROLE,
    tags=["Role"],
    response_model=ExecutiveRoleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
)
async def create_role(
    form_param: CreateForm = Depends(),
    bearer=Depends(bearer_executive),
    request_info=Depends(get_request_info),
):
    """
    **Create a new executive role.**

    - Executive must have a valid access token.
    - Logged-in executive must have 'executive.role.create' permission.
    - Duplicate names are not allowed.
    """
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, bearer.credentials)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.CREATE_EXECUTIVE_ROLE)

        form_param.permissions = form_param.permissions.model_dump()
        role = ExecutiveRole(name=form_param.name, permissions=form_param.permissions)
        session.add(role)
        session.commit()
        session.refresh(role)

        role_data = jsonable_encoder(role)
        log_event(token, request_info, role_data)
        return role_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    URL_EXECUTIVE_ROLE + "/{id}",
    tags=["Role"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
        ]
    ),
)
async def delete_role(
    id: int,
    bearer=Depends(bearer_executive),
    request_info=Depends(get_request_info),
):
    """
    **Deletes an existing executive role.**

    - Requires a valid access token for authentication.
    - The logged-in executive must have the `executive.role.delete` permission.
    """
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, bearer.credentials)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.DELETE_EXECUTIVE_ROLE)

        role = session.query(ExecutiveRole).filter(ExecutiveRole.id == id).first()
        if role is not None:
            session.delete(role)
            session.commit()
            log_event(token, request_info, jsonable_encoder(role))

        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
