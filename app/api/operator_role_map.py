"""
Operator Role Map API Router for EnteBus.

Provides endpoints for managing operator role mappings, including update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for creation, deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    ExecutiveToken,
    Operator,
    OperatorRole,
    OperatorRoleMap,
    OperatorToken,
    SessionLocal,
)
from app.src.urls import URL_OPERATOR_ROLE_MAP
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    fuse_exception_responses,
    get_executive_roles,
    get_request_info,
    get_operator_roles,
)

route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class OperatorRoleMapSchema(BaseModel):
    """Schema for operator role mapping response."""

    id: int
    company_id: int
    role_id: int
    operator_id: int
    created_on: datetime
    updated_on: datetime | None


## Input Forms
class UpdateForm(BaseModel):
    """Form data for updating an operator role mapping."""

    role_id: int | None = Field(default=None)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.patch(
    f"{URL_OPERATOR_ROLE_MAP}/{{id}}",
    tags=["Operator Role Map"],
    response_model=OperatorRoleMapSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(OperatorRoleMap.id),
            exceptions.UnknownValue(OperatorRoleMap.role_id),
        ]
    ),
    description=(
        """
            **Updates an existing operator role mapping.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.operator.role.update` permission.    
            - Duplicate mappings are not allowed.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_role_map_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR_ROLE)

        role_map = (
            session.query(OperatorRoleMap).filter(OperatorRoleMap.id == id).first()
        )
        if role_map is None:
            raise exceptions.UnknownValue(OperatorRoleMap.id)

        if form_param.role_id is not None:
            role = (
                session.query(OperatorRole)
                .filter(OperatorRole.id == form_param.role_id)
                .first()
            )
            if role is None:
                raise exceptions.UnknownValue(OperatorRoleMap.role_id)
            if role.company_id != role_map.company_id:
                raise exceptions.InvalidAssociation(
                    OperatorRoleMap.role_id, OperatorRoleMap.operator_id
                )
            role_map.role_id = role.id

        session.commit()
        session.refresh(role_map)

        role_map_data = jsonable_encoder(role_map)
        log_event(token, request_info, role_map_data)
        return role_map_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.patch(
    f"{URL_OPERATOR_ROLE_MAP}/{{id}}",
    tags=["Role Map"],
    response_model=OperatorRoleMapSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(OperatorRoleMap.id),
            exceptions.UnknownValue(OperatorRoleMap.role_id),
        ]
    ),
    description=(
        """
            **Updates an existing operator role mapping.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.operator.role.update` permission.    
            - Duplicate mappings are not allowed.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_role_map_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR_ROLE)

        role_map = (
            session.query(OperatorRoleMap)
            .filter(
                OperatorRoleMap.id == id, OperatorRoleMap.company_id == token.company_id
            )
            .first()
        )
        if role_map is None:
            raise exceptions.UnknownValue(OperatorRoleMap.id)

        if form_param.role_id is not None:
            role = (
                session.query(OperatorRole)
                .filter(
                    OperatorRole.id == form_param.role_id,
                    OperatorRole.company_id == token.company_id,
                )
                .first()
            )
            if role is None:
                raise exceptions.UnknownValue(OperatorRoleMap.role_id)
            role_map.role_id = role.id

        session.commit()
        session.refresh(role_map)

        role_map_data = jsonable_encoder(role_map)
        log_event(token, request_info, role_map_data)
        return role_map_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
