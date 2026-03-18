"""
Executive Role Map API Router for EnteBus.

Provides endpoints for managing executive role mappings, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from fastapi import APIRouter, Response, status, Depends, Query
from enum import StrEnum
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import oauth2_executive
from app.src.db import ExecutiveRoleMap, ExecutiveToken, SessionLocal
from app.src.enums import OrderIn
from app.src.filters import IDFilter, PaginationFilter, UpdatedOnFilter, CreatedOnFilter
from app.src.urls import URL_EXECUTIVE_ROLE_MAP
from app.src.permissions.executive import PermissionPath
from app.src import exceptions
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    update_if_changed,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
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
    """Form data for updating an executive role mapping."""

    role_id: int | None = Field(default=None)


# Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParams(UpdatedOnFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for fetching executive role maps."""

    role_id: int | None = Field(Query(default=None))
    executive_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


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
            - Empty PATCH requests are allowed and will result in no changes.    
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
        have_updates = session.is_modified(role_map)
        if have_updates:
            session.commit()
            session.refresh(role_map)

        role_map_data = jsonable_encoder(role_map)
        if have_updates:
            log_event(token, request_info, role_map_data)
        return role_map_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_EXECUTIVE_ROLE_MAP}/{{id}}",
    tags=["Role Map"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an existing executive role mapping.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have the `executive.role.update` permission.    
            - Returns 204 No Content even if the specified role mapping does not exist.    
        """
    ),
)
async def delete_role_map(
    id: int,
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
        if role_map is not None:
            role_map_data = jsonable_encoder(role_map)
            session.delete(role_map)
            session.commit()
            log_event(token, request_info, role_map_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_EXECUTIVE_ROLE_MAP,
    tags=["Role Map"],
    response_model=list[ExecutiveRoleMapSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches all executive role mappings.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_role_map(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        query = session.query(ExecutiveRoleMap)
        if query_params.role_id is not None:
            query = query.filter(ExecutiveRoleMap.role_id == query_params.role_id)
        if query_params.executive_id is not None:
            query = query.filter(
                ExecutiveRoleMap.executive_id == query_params.executive_id
            )

        # Generalized filters
        query = apply_id_filters(query, ExecutiveRoleMap, query_params)
        query = apply_created_on_filters(query, ExecutiveRoleMap, query_params)
        query = apply_updated_on_filters(query, ExecutiveRoleMap, query_params)

        # Ordering and pagination
        ordering_attr = getattr(ExecutiveRoleMap, query_params.order_by.value)
        ordering_func = (
            ordering_attr.asc
            if query_params.order_in == OrderIn.ASCENDING
            else ordering_attr.desc
        )
        query = query.order_by(ordering_func())
        query = query.offset(query_params.offset).limit(query_params.limit)

        role_maps = query.all()
        return role_maps
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
