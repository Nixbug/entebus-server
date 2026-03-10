"""
Executive Role Map API Router for EnteBus.

Provides endpoints for managing executive role mappings, including creation, updating.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import oauth2_executive
from app.src.db import ExecutiveRoleMap, ExecutiveToken, SessionLocal
from app.src.urls import URL_EXECUTIVE_ROLE_MAP
from app.src.permissions.executive import PermissionPath
from app.src import exceptions
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    update_if_changed,
)


route_executive = APIRouter()


## Output Schema
class ExecutiveRoleMapSchema(BaseModel):
    """Schema for executive role mapping response."""

    id: int
    role_id: int
    executive_id: int
    created_on: datetime
    updated_on: datetime | None


## Input Forms
class CreateForm(BaseModel):
    """Form data for creating a new executive role mapping."""

    role_id: int = Field()
    executive_id: int = Field()


class UpdateForm(BaseModel):
    """Form data for updating an existing executive role mapping."""

    role_id: int = Field()


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_ROLE_MAP,
    tags=["Role Map"],
    response_model=ExecutiveRoleMapSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new executive role mapping.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `executive.role.update` permission.    
            - Duplicate mappings are not allowed.    
        """
    ),
)
async def create_role_map(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE_ROLE)

        role_map = ExecutiveRoleMap(
            role_id=form_param.role_id, executive_id=form_param.executive_id
        )
        session.add(role_map)
        session.commit()
        session.refresh(role_map)

        role_map_data = jsonable_encoder(role_map)
        log_event(token, request_info, role_map_data)
        return role_map_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_EXECUTIVE_ROLE_MAP}/{{id}}",
    tags=["Role Map"],
    response_model=ExecutiveRoleMapSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ExecutiveRoleMap.id),
        ]
    ),
     description=(
        """
            **Updates an existing executive role mapping.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `executive.role.update` permission.    
            - Duplicate mappings are not allowed.    
        """
    ),
)
async def update_role_map(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE_ROLE)

        role_map = (
            session.query(ExecutiveRoleMap).filter(ExecutiveRoleMap.id == id).first()
        )
        if role_map is None:
            raise exceptions.UnknownValue(ExecutiveRoleMap.id)
        update_data = form_param.model_dump(exclude_unset=True)
        update_if_changed(role_map, update_data)
        haveUpdates = session.is_modified(role_map)
        if haveUpdates:
            session.commit()
            session.refresh(role_map)

        role_map_data = jsonable_encoder(role_map)
        if haveUpdates:
            log_event(token, request_info, role_map_data)
        return role_map_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()

