"""
Executive Role API router.

Provides endpoints for managing executive roles:
    - POST (executive)
    - PATCH (executive)
    - DELETE (executive)
    - GET (executive)
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import or_, String
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive
from app.src.db import ExecutiveRole, ExecutiveToken, get_db_session
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
    NameFilter,
)
from app.src.permissions.executive import PermissionSchema, PermissionPath
from app.src import exceptions, schemas
from app.src.schemas import PatchForm
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_EXECUTIVE_ROLE
from app.src.openobserve import log_event
from app.src.validators import validate_id, verify_token, authorize_executive
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    apply_name_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    update_if_changed,
)
from app.src.description import Description
from app.src.constants import MAX_EXECUTIVE_ROLE

route_executive = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class ExecutiveRoleSchema(BaseModel):
    """Schema for executive role response."""

    id: int
    name: str
    permissions: PermissionSchema
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """Form data for creating a new executive role."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    permissions: PermissionSchema


class UpdateForm(PatchForm):
    """Form data for updating an executive role."""

    name: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=NAME_PATTERN
    )
    permissions: PermissionSchema | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
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
## Core Functions
# ---------------------------------------------------------------------------
def create_executive_role(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Creates a new executive role with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new executive role.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created executive role data.
    """
    role_count = session.query(ExecutiveRole).count()
    if role_count >= MAX_EXECUTIVE_ROLE:
        raise exceptions.LimitExceeded(ExecutiveRole)

    executive_role = ExecutiveRole(
        name=form_param.name,
        permissions=form_param.permissions.model_dump(),
    )
    session.add(executive_role)
    session.commit()
    session.refresh(executive_role)

    executive_role_data = jsonable_encoder(executive_role)
    log_event(token, request_info, executive_role_data)
    return executive_role_data


def update_executive_role(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Updates an ExecutiveRole with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the executive role to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Updated executive role data.
    """
    executive_role = validate_id(session, ExecutiveRole, id, ExecutiveRole.id)

    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(executive_role, update_data)
    if session.is_modified(executive_role):
        session.commit()
        session.refresh(executive_role)
        executive_role_data = jsonable_encoder(executive_role)
        log_event(token, request_info, executive_role_data)
    else:
        executive_role_data = jsonable_encoder(executive_role)
    return executive_role_data


def delete_executive_role(
    session: Session,
    id: int,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> None:
    """
    Delete an executive role from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the executive role to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    executive_role = get_by_id(session, ExecutiveRole, id)
    if executive_role is None:
        return

    executive_role_data = jsonable_encoder(executive_role)
    session.delete(executive_role)
    session.commit()
    log_event(token, request_info, executive_role_data)


def search_executive_roles(
    session: Session, query_params: QueryParams
) -> list[ExecutiveRole]:
    """
    Searches for executive roles based on the provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve executive roles that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters for filtering, searching, and pagination.

    Returns:
        list[ExecutiveRole]: List of executive roles matching the search criteria.
    """
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

    executive_roles = query.all()
    return executive_roles


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.LimitExceeded(ExecutiveRole),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ExecutiveRole.id),
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
    .add_head("Creates a new executive role.")
    .add_line("Duplicate names are not allowed.")
    .add_line("Logged-in executive must have the `executive.role.create` permission.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing executive role.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line("Duplicate names are not allowed.")
    .add_line("Logged-in executive must have the `executive.role.update` permission.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing executive role.")
    .add_line("Returns 204 No Content even if the specified role does not exist.")
    .add_line("Logged-in executive must have the `executive.role.delete` permission.")
)

GET_DESCRIPTION = Description().add_head("Fetches all executive roles.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_ROLE,
    summary="Create executive role",
    tags=["Role"],
    response_model=ExecutiveRoleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_executive_role_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.CREATE_EXECUTIVE_ROLE]
        )
        return create_executive_role(session, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_EXECUTIVE_ROLE}/{{id}}",
    summary="Update executive role",
    tags=["Role"],
    response_model=ExecutiveRoleSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(PATCH_DESCRIPTION.to_string()),
)
async def update_executive_role_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.UPDATE_EXECUTIVE_ROLE]
        )
        return update_executive_role(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_EXECUTIVE_ROLE}/{{id}}",
    summary="Delete executive role",
    tags=["Role"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(DELETE_DESCRIPTION.to_string()),
)
async def delete_executive_role_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [PermissionPath.DELETE_EXECUTIVE_ROLE]
        )
        delete_executive_role(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_EXECUTIVE_ROLE,
    summary="Fetch executive role",
    tags=["Role"],
    response_model=list[ExecutiveRoleSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_executive_roles_for_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_executive_roles(session, query_params)
    except Exception as e:
        exceptions.handle(e)
