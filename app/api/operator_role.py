"""
Operator Role API Router for EnteBus.

Provides endpoints for managing operator roles, including creation and update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import oauth2_executive, bearer_operator
from app.src import exceptions
from app.src.db import (
    ExecutiveToken,
    OperatorRole,
    OperatorToken,
    SessionLocal,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import (
    PermissionPath as OperatorPermissionPath,
    PermissionSchema,
)
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_OPERATOR_ROLE
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    get_operator_roles,
    update_if_changed,
)

route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class OperatorRoleSchema(BaseModel):
    """Schema for operator role response."""

    id: int
    company_id: int
    name: str
    permissions: PermissionSchema
    created_on: datetime
    updated_on: datetime | None


## Input Forms
class CreateFormForOP(BaseModel):
    """Form data for creating a new operator role for an operator."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    permissions: PermissionSchema


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new operator role for an executive."""

    company_id: int = Field()


class UpdateForm(BaseModel):
    """Form data for updating an operator role."""

    name: str = Field(default=None, min_length=1, max_length=32, pattern=NAME_PATTERN)
    permissions: PermissionSchema = Field(default=None)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_OPERATOR_ROLE,
    tags=["Operator Role"],
    response_model=OperatorRoleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new operator role.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.operator.role.create` permission.    
            - Duplicate names are not allowed.    
        """
    ),
)
async def create_role_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_OPERATOR_ROLE)

        form_param.permissions = form_param.permissions.model_dump()
        role = OperatorRole(
            company_id=form_param.company_id,
            name=form_param.name,
            permissions=form_param.permissions,
        )
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


@route_executive.patch(
    f"{URL_OPERATOR_ROLE}/{{id}}",
    tags=["Operator Role"],
    response_model=OperatorRoleSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(OperatorRole.id),
        ]
    ),
    description=(
        """
            **Updates an existing operator role.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.operator.role.update` permission.    
            - Duplicate names are not allowed.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_role_executive(
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

        role = session.query(OperatorRole).filter(OperatorRole.id == id).first()
        if role is None:
            raise exceptions.UnknownValue(OperatorRole.id)
        update_data = form_param.model_dump(exclude_unset=True)
        update_if_changed(role, update_data)
        have_updates = session.is_modified(role)
        if have_updates:
            session.commit()
            session.refresh(role)

        role_data = jsonable_encoder(role)
        if have_updates:
            log_event(token, request_info, role_data)
        return role_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_ROLE,
    tags=["Role"],
    response_model=OperatorRoleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new operator role.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.operator.role.create` permission.    
            - Duplicate names are not allowed.    
        """
    ),
)
async def create_role_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_OPERATOR_ROLE)

        form_param.permissions = form_param.permissions.model_dump()
        role = OperatorRole(
            company_id=token.company_id,
            name=form_param.name,
            permissions=form_param.permissions,
        )
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


@route_operator.patch(
    f"{URL_OPERATOR_ROLE}/{{id}}",
    tags=["Role"],
    response_model=OperatorRoleSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(OperatorRole.id),
        ]
    ),
    description=(
        """
            **Updates an existing operator role.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.operator.role.update` permission.    
            - Duplicate names are not allowed.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_role_operator(
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

        role = (
            session.query(OperatorRole)
            .filter(OperatorRole.id == id, OperatorRole.company_id == token.company_id)
            .first()
        )
        if role is None:
            raise exceptions.UnknownValue(OperatorRole.id)
        update_data = form_param.model_dump(exclude_unset=True)
        update_if_changed(role, update_data)
        have_updates = session.is_modified(role)
        if have_updates:
            session.commit()
            session.refresh(role)

        role_data = jsonable_encoder(role)
        if have_updates:
            log_event(token, request_info, role_data)
        return role_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
