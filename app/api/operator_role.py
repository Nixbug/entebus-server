"""
Operator Role API Router for EnteBus.

Provides endpoints for managing operator roles, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from typing import List, Tuple
from fastapi import APIRouter, Response, status, Depends, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session
from sqlalchemy import or_, String
from enum import StrEnum

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
from app.src.validators import (
    authorize_executive,
    verify_token,
    validate_id,
    authorize_operator,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_name_filters,
    apply_updated_on_filters,
    fuse_exception_responses,
    get_request_info,
    update_if_changed,
)
from app.src.description import Description
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
    NameFilter,
    enum_str,
)

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class OperatorRoleSchema(BaseModel):
    """Schema for operator role response."""

    id: int
    company_id: int
    name: str
    permissions: PermissionSchema
    created_on: datetime
    updated_on: datetime | None


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new operator role for an operator."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    permissions: PermissionSchema


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new operator role for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new operator role."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating an operator role."""

    name: str = Field(default=None, min_length=1, max_length=32, pattern=NAME_PATTERN)
    permissions: PermissionSchema = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(
    UpdatedOnFilter, CreatedOnFilter, NameFilter, IDFilter, PaginationFilter
):
    """Query parameters for operators."""

    search: str | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Functions
# ---------------------------------------------------------------------------
def update_role(
    session: Session, id: int, form_param: UpdateForm, extra_filter=None
) -> Tuple[bool, dict]:
    """
    Updates an OperatorRole with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the OperatorRole to update.
        form_param (UpdateForm): Form data containing fields to update.
        extra_filter: Additional filter to apply when validating the role ID.

    Returns:
    Tuple[bool, dict]:
            - bool: True if the role was modified and the changes were committed.
            - dict: JSON-encoded representation of the updated role.
    """
    role = validate_id(
        session, OperatorRole, id, OperatorRole.id, extra_filter=extra_filter
    )
    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(role, update_data)
    have_updates = session.is_modified(role)
    if have_updates:
        session.commit()
        session.refresh(role)

    role_data = jsonable_encoder(role)
    return have_updates, role_data


def search_role(session: Session, query_params: QueryParams) -> List[OperatorRole]:
    """
    Search for operator roles based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve operator roles that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[OperatorRole]: List of OperatorRole instances that match the search criteria.
    """
    query = session.query(OperatorRole)
    if query_params.company_id is not None:
        query = query.filter(OperatorRole.company_id == query_params.company_id)

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                OperatorRole.id.cast(String).ilike(search),
                OperatorRole.name.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, OperatorRole, query_params)
    query = apply_created_on_filters(query, OperatorRole, query_params)
    query = apply_updated_on_filters(query, OperatorRole, query_params)
    query = apply_name_filters(query, OperatorRole, query_params)

    # Ordering and pagination
    ordering_attr = getattr(OperatorRole, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    roles = query.all()
    return roles


def delete_role(session: Session, role: OperatorRole) -> dict:
    """
    Deletes an OperatorRole from the database.

    Args:
        session (Session): SQLAlchemy database session.
        role (OperatorRole): OperatorRole to delete.

    Returns:
        dict: JSON-encoded representation of the deleted role.
    """
    role_data = jsonable_encoder(role)
    session.delete(role)
    session.commit()
    return role_data


def create_role(session: Session, form_param: CreateForm) -> dict:
    """
    Create an new OperatorRole in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param: Form data for creating an operator role.

    Returns:
        dict: The created role data.
    """
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
    return role_data


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(OperatorRole.id),
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
    .add_head("Creates a new operator role.")
    .add_line("Duplicate names are not allowed.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing operator role.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing operator role.")
    .add_line("Returns 204 No Content even if the specified role does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of operator roles.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_OPERATOR_ROLE,
    summary="Create operator role",
    tags=["Operator Role"],
    response_model=OperatorRoleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.operator.role.create` permission."
        )
        .to_string()
    ),
)
async def create_operator_role_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_OPERATOR_ROLE],
        )

        role_data = create_role(session, CreateForm(**form_param.model_dump()))
        log_event(token, request_info, role_data)
        return role_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_OPERATOR_ROLE}/{{id}}",
    summary="Update operator role",
    tags=["Operator Role"],
    response_model=OperatorRoleSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.operator.role.update` permission."
        )
        .to_string()
    ),
)
async def update_operator_role_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )

        have_updates, role_data = update_role(session, id, form_param)
        if have_updates:
            log_event(token, request_info, role_data)
        return role_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_OPERATOR_ROLE}/{{id}}",
    summary="Delete operator role",
    tags=["Operator Role"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `company.operator.role.delete` permission."
        )
        .to_string()
    ),
)
async def delete_operator_role_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_OPERATOR_ROLE],
        )

        role = session.query(OperatorRole).filter(OperatorRole.id == id).first()
        if role is not None:
            role_data = delete_role(session, role)
            log_event(token, request_info, role_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_OPERATOR_ROLE,
    summary="Fetch operator role",
    tags=["Operator Role"],
    response_model=List[OperatorRoleSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_operator_roles_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_role(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_ROLE,
    summary="Create operator role",
    tags=["Role"],
    response_model=OperatorRoleSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.operator.role.create` permission."
        )
        .to_string()
    ),
)
async def create_operator_role_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_OPERATOR_ROLE],
        )

        role_data = create_role(
            session, CreateForm(**form_param.model_dump(), company_id=token.company_id)
        )
        log_event(token, request_info, role_data)
        return role_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_OPERATOR_ROLE}/{{id}}",
    summary="Update operator role",
    tags=["Role"],
    response_model=OperatorRoleSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.operator.role.update` permission."
        )
        .add_line("Operators can update roles within their own company.")
        .to_string()
    ),
)
async def update_operator_role_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )

        have_updates, role_data = update_role(
            session,
            id,
            form_param,
            extra_filter=(OperatorRole.company_id == token.company_id),
        )
        if have_updates:
            log_event(token, request_info, role_data)
        return role_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_OPERATOR_ROLE}/{{id}}",
    summary="Delete operator role",
    tags=["Role"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in operator must have the `company.operator.role.delete` permission."
        )
        .to_string()
    ),
)
async def delete_operator_role_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_OPERATOR_ROLE],
        )

        role = (
            session.query(OperatorRole)
            .filter(OperatorRole.id == id, OperatorRole.company_id == token.company_id)
            .first()
        )
        if role is not None:
            role_data = delete_role(session, role)
            log_event(token, request_info, role_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_OPERATOR_ROLE,
    summary="Fetch operator role",
    tags=["Role"],
    response_model=List[OperatorRoleSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "Only operator roles belonging to the same company as the logged-in operator will be returned."
        )
        .to_string()
    ),
)
async def fetch_operator_roles_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_role(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
