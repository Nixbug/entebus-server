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
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive
from app.src.db import (
    Executive,
    ExecutiveRoleMap,
    ExecutiveToken,
    SessionLocal,
    ExecutiveRole,
)
from app.src.enums import OrderIn
from app.src.filters import IDFilter, PaginationFilter, UpdatedOnFilter, CreatedOnFilter
from app.src.urls import URL_EXECUTIVE_ROLE_MAP
from app.src.permissions.executive import PermissionPath
from app.src import exceptions
from app.src.openobserve import log_event
from app.src.validators import validate_id, verify_token, authorize_executive
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_request_info,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
)
from app.src.description import Description

route_executive = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class ExecutiveRoleMapSchema(BaseModel):
    """Schema for executive role mapping response."""

    id: int
    role_id: int
    executive_id: int
    created_on: datetime
    updated_on: datetime | None


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """Form data for creating a new executive role mapping."""

    role_id: int = Field()
    executive_id: int = Field()


class UpdateForm(BaseModel):
    """Form data for updating an executive role mapping."""

    role_id: int = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
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
## Core Functions
# ---------------------------------------------------------------------------
def create_executive_role_map(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new executive role mapping with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new executive role mapping.

    Returns:
        dict: Created executive role mapping data.
    """
    validate_id(
        session, Executive, form_param.executive_id, ExecutiveRoleMap.executive_id
    )
    validate_id(session, ExecutiveRole, form_param.role_id, ExecutiveRoleMap.role_id)

    role_map = ExecutiveRoleMap(
        role_id=form_param.role_id, executive_id=form_param.executive_id
    )
    session.add(role_map)
    session.commit()
    session.refresh(role_map)
    return jsonable_encoder(role_map)


def update_executive_role_map(
    session: Session, id: int, form_param: UpdateForm
) -> tuple[bool, dict]:
    """
    Updates an Executive role mapping with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the executive role mapping to update.
        form_param (UpdateForm): Form data containing fields to update.

    Returns:
        Tuple[bool, dict]:
            - bool: True if the executive role mapping was modified and the changes were committed.
            - dict: JSON-encoded representation of the updated executive role mapping.
    """
    role_map = validate_id(session, ExecutiveRoleMap, id, ExecutiveRoleMap.id)

    update_data = form_param.model_dump(exclude_unset=True)
    if "role_id" in update_data:
        if role_map.role_id != form_param.role_id:
            validate_id(
                session, ExecutiveRole, form_param.role_id, ExecutiveRoleMap.role_id
            )
            role_map.role_id = form_param.role_id
            update_data.pop("role_id")

    updated = session.is_modified(role_map)
    if updated:
        session.commit()
        session.refresh(role_map)
    return updated, jsonable_encoder(role_map)


def search_executive_role_maps(
    session: Session, query_params: QueryParams
) -> list[ExecutiveRoleMap]:
    """
    Searches for executive role mappings based on the provided query parameters.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters for filtering and pagination.

    Returns:
        list[ExecutiveRoleMap]: List of executive role mappings matching the query parameters.
    """
    query = session.query(ExecutiveRoleMap)
    if query_params.role_id is not None:
        query = query.filter(ExecutiveRoleMap.role_id == query_params.role_id)
    if query_params.executive_id is not None:
        query = query.filter(ExecutiveRoleMap.executive_id == query_params.executive_id)

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


def delete_executive_role_map(session: Session, id: int) -> tuple[bool, dict]:
    """
    Deletes an executive role mapping from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the executive role mapping to delete.

    Returns:
        Tuple[bool, dict]:
            - bool: True if the executive role mapping was found and deleted.
            - dict: JSON-encoded representation of the deleted executive role mapping.
    """
    role_map = session.query(ExecutiveRoleMap).filter(ExecutiveRoleMap.id == id).first()
    if role_map is not None:
        role_map_data = jsonable_encoder(role_map)
        session.delete(role_map)
        session.commit()
        return True, role_map_data
    return False, {}


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ExecutiveRoleMap.executive_id),
    exceptions.UnknownValue(ExecutiveRoleMap.role_id),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ExecutiveRoleMap.id),
    exceptions.UnknownValue(ExecutiveRoleMap.role_id),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new executive role mapping.")
    .add_line("Duplicate mappings are not allowed.")
    .add_line("Logged-in executive must have the `executive.role.update` permission.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing executive role mapping.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line("Duplicate mappings are not allowed.")
    .add_line("Logged-in executive must have the `executive.role.update` permission.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing executive role mapping.")
    .add_line(
        "Returns 204 No Content even if the specified role mapping does not exist."
    )
    .add_line("Logged-in executive must have the `executive.role.update` permission.")
)

GET_DESCRIPTION = Description().add_head("Fetches all executive role mappings.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_ROLE_MAP,
    summary="Create executive role map",
    tags=["Role Map"],
    response_model=ExecutiveRoleMapSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_executive_role_map_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session, access_token, [PermissionPath.UPDATE_EXECUTIVE_ROLE]
        )

        role_map_data = create_executive_role_map(session, form_param)
        log_event(token, request_info, role_map_data)
        return role_map_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_EXECUTIVE_ROLE_MAP}/{{id}}",
    summary="Update executive role map",
    tags=["Role Map"],
    response_model=ExecutiveRoleMapSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(PATCH_DESCRIPTION.to_string()),
)
async def update_executive_role_map_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session, access_token, [PermissionPath.UPDATE_EXECUTIVE_ROLE]
        )

        updated, role_map_data = update_executive_role_map(session, id, form_param)
        if updated:
            log_event(token, request_info, role_map_data)
        return role_map_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_EXECUTIVE_ROLE_MAP}/{{id}}",
    summary="Delete executive role map",
    tags=["Role Map"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(DELETE_DESCRIPTION.to_string()),
)
async def delete_executive_role_map_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session, access_token, [PermissionPath.UPDATE_EXECUTIVE_ROLE]
        )

        deleted, role_map_data = delete_executive_role_map(session, id)
        if deleted:
            log_event(token, request_info, role_map_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_EXECUTIVE_ROLE_MAP,
    summary="Fetch executive role map",
    tags=["Role Map"],
    response_model=list[ExecutiveRoleMapSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_executive_role_maps_for_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_executive_role_maps(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
