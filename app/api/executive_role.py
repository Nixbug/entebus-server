"""
Executive Role API Router for EnteBus.

Provides endpoints for managing executive roles, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Query, Response, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import or_, String

from app.api.bearer import oauth2_executive
from app.src.db import ExecutiveRole, ExecutiveToken, SessionLocal
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
    NameFilter,
)
from app.src.permissions.executive import PermissionSchema, PermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_EXECUTIVE_ROLE
from app.src.openobserve import log_event
from app.src.validators import validate_id, verify_permission, verify_token
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    apply_name_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    update_if_changed,
)

route_executive = APIRouter()


## Output Schema
class ExecutiveRoleSchema(BaseModel):
    """Schema for executive role response."""

    id: int
    name: str
    permissions: PermissionSchema
    created_on: datetime
    updated_on: datetime | None


## Input Forms
class CreateForm(BaseModel):
    """Form data for creating a new executive role."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    permissions: PermissionSchema


class UpdateForm(BaseModel):
    """Form data for updating an executive role."""

    name: str = Field(default=None, min_length=1, max_length=32, pattern=NAME_PATTERN)
    permissions: PermissionSchema = Field(default=None)


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParams(
    UpdatedOnFilter, CreatedOnFilter, NameFilter, IDFilter, PaginationFilter
):
    """Query parameters for fetching executive roles."""

    search: str | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


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
    description=(
        """
            **Creates a new executive role.**    
            - Executive must have a valid access token.     
            - Logged-in executive must have `executive.role.create` permission.     
            - Duplicate names are not allowed.      
        """
    ),
)
async def create_role(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
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


@route_executive.patch(
    f"{URL_EXECUTIVE_ROLE}/{{id}}",
    tags=["Role"],
    response_model=ExecutiveRoleSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ExecutiveRole.id),
        ]
    ),
    description=(
        """
            **Updates an existing executive role.**    
            - Requires a valid access token.    
            - Logged-in executive must have `executive.role.update` permission.       
            - Duplicate names are not allowed.     
            - Empty PATCH requests are allowed and will result in no changes.   
        """
    ),
)
async def update_role(
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

        role = validate_id(session, ExecutiveRole, id, ExecutiveRole.id)
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


@route_executive.delete(
    f"{URL_EXECUTIVE_ROLE}/{{id}}",
    tags=["Role"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an existing executive role.**    
            - Requires a valid access token for authentication.     
            - The logged-in executive must have the `executive.role.delete` permission.     
            - Returns 204 No Content even if the specified role does not exist.     
        """
    ),
)
async def delete_role(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.DELETE_EXECUTIVE_ROLE)

        role = session.query(ExecutiveRole).filter(ExecutiveRole.id == id).first()
        if role is not None:
            role_data = jsonable_encoder(role)
            session.delete(role)
            session.commit()
            log_event(token, request_info, role_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_EXECUTIVE_ROLE,
    tags=["Role"],
    response_model=list[ExecutiveRoleSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches all executive roles.**    
            - Requires a valid access token for authentication.     
        """
    ),
)
async def fetch_role(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        query = session.query(ExecutiveRole)

        # Common search
        if query_params.search:
            search = f"%{query_params.search}%"
            query = query.filter(
                or_(
                    ExecutiveRole.id.cast(String).ilike(search),
                    ExecutiveRole.name.ilike(search),
                )
            )

        # Generalized filters
        query = apply_id_filters(query, ExecutiveRole, query_params)
        query = apply_created_on_filters(query, ExecutiveRole, query_params)
        query = apply_updated_on_filters(query, ExecutiveRole, query_params)
        query = apply_name_filters(query, ExecutiveRole, query_params)

        # Ordering and pagination
        ordering_attr = getattr(ExecutiveRole, query_params.order_by.value)
        ordering_func = (
            ordering_attr.asc
            if query_params.order_in == OrderIn.ASCENDING
            else ordering_attr.desc
        )
        query = query.order_by(ordering_func())
        query = query.offset(query_params.offset).limit(query_params.limit)

        roles = query.all()
        return roles
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
