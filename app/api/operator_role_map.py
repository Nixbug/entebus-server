"""
Operator Role Map API router.

Provides endpoints for managing operator role maps:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator)
"""

from datetime import datetime
from enum import StrEnum
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.sql import ColumnElement
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions, schemas
from app.src.db import (
    ExecutiveToken,
    Operator,
    OperatorRole,
    OperatorRoleMap,
    OperatorToken,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, UpdatedOnFilter
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.schemas import PatchForm
from app.src.urls import URL_OPERATOR_ROLE_MAP
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    verify_token,
)

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class OperatorRoleMapSchema(BaseModel):
    """Schema for operator role mapping response."""

    id: int
    company_id: int
    role_id: int
    operator_id: int
    created_on: datetime
    updated_on: datetime | None


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new operator role mapping for an operator."""

    role_id: int = Field()
    operator_id: int = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new operator role mapping for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new operator role mapping."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating an operator role mapping."""

    role_id: int | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(PaginationFilter, IDFilter, CreatedOnFilter, UpdatedOnFilter):
    """Query parameters for operators."""

    role_id: int | None = Field(Query(default=None))
    operator_id: int | None = Field(Query(default=None))
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
## Core Functions
# ---------------------------------------------------------------------------
def create_operator_role_map(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
    operator_filter: ColumnElement[bool] | None = None,
    role_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Creates a new operator role mapping with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new operator role mapping.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        operator_filter: Additional filter for validating operator ownership.
        role_filter: Additional filter for validating role ownership.

    Returns:
        dict: Created operator role mapping data.
    """
    operator = validate_id(
        session,
        Operator,
        form_param.operator_id,
        OperatorRoleMap.operator_id,
        extra_filter=operator_filter,
    )
    operator_role = validate_id(
        session,
        OperatorRole,
        form_param.role_id,
        OperatorRoleMap.role_id,
        extra_filter=role_filter,
    )

    operator_role_map = OperatorRoleMap(
        company_id=form_param.company_id,
        role_id=operator_role.id,
        operator_id=operator.id,
    )
    session.add(operator_role_map)
    session.commit()
    session.refresh(operator_role_map)

    operator_role_map_data = jsonable_encoder(operator_role_map)
    log_event(token, request_info, operator_role_map_data)
    return operator_role_map_data


def update_operator_role_map(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
    role_map_filter: ColumnElement[bool] | None = None,
    role_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Updates an operator role mapping with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the operator role mapping to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        role_map_filter: Additional filter for validating role map ownership.
        role_filter: Additional filter for validating role ownership.

    Returns:
        dict: Updated operator role mapping data.
    """
    operator_role_map = validate_id(
        session,
        OperatorRoleMap,
        id,
        OperatorRoleMap.id,
        extra_filter=role_map_filter,
    )

    if isinstance(token, ExecutiveToken):
        role_filter = OperatorRole.company_id == operator_role_map.company_id

    update_data = form_param.model_dump(exclude_unset=True)
    if "role_id" in update_data:
        if operator_role_map.role_id != update_data["role_id"]:
            operator_role = validate_id(
                session,
                OperatorRole,
                update_data["role_id"],
                OperatorRoleMap.role_id,
                extra_filter=role_filter,
            )
            operator_role_map.role_id = operator_role.id
        update_data.pop("role_id")

    if session.is_modified(operator_role_map):
        session.commit()
        session.refresh(operator_role_map)
        operator_role_map_data = jsonable_encoder(operator_role_map)
        log_event(token, request_info, operator_role_map_data)
    else:
        operator_role_map_data = jsonable_encoder(operator_role_map)
    return operator_role_map_data


def delete_operator_role_map(
    session: Session,
    id: int,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
    role_map_filter: ColumnElement[bool] | None = None,
) -> None:
    """
    Deletes an operator role mapping from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the operator role mapping to delete.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        role_map_filter: Additional filter for role map ownership.
    """
    operator_role_map = get_by_id(
        session, OperatorRoleMap, id, extra_filter=role_map_filter
    )
    if operator_role_map is None:
        return

    operator_role_map_data = jsonable_encoder(operator_role_map)
    session.delete(operator_role_map)
    session.commit()
    log_event(token, request_info, operator_role_map_data)


def search_operator_role_maps(
    session: Session, query_params: QueryParams
) -> list[OperatorRoleMap]:
    """
    Searches for operator role mappings based on the provided query parameters.

    This function supports multiple filtering, ordering, and pagination capabilities
    to retrieve operator role mappings that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[OperatorRoleMap]: List of operator role mappings that match the search criteria.
    """
    query = session.query(OperatorRoleMap)
    if query_params.company_id is not None:
        query = query.filter(OperatorRoleMap.company_id == query_params.company_id)
    if query_params.role_id is not None:
        query = query.filter(OperatorRoleMap.role_id == query_params.role_id)
    if query_params.operator_id is not None:
        query = query.filter(OperatorRoleMap.operator_id == query_params.operator_id)

    # Generalized filters
    query = apply_id_filters(query, OperatorRoleMap, query_params)
    query = apply_created_on_filters(query, OperatorRoleMap, query_params)
    query = apply_updated_on_filters(query, OperatorRoleMap, query_params)

    # Ordering and pagination
    ordering_attr = getattr(OperatorRoleMap, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    operator_role_maps = query.all()
    return operator_role_maps


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(OperatorRoleMap.operator_id),
    exceptions.UnknownValue(OperatorRoleMap.role_id),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(OperatorRoleMap.id),
    exceptions.UnknownValue(OperatorRoleMap.role_id),
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
    .add_head("Creates a new operator role mapping.")
    .add_line("Duplicate mappings are not allowed.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing operator role mapping.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line("Duplicate mappings are not allowed.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing operator role mapping.")
    .add_line(
        "Returns 204 No Content even if the specified role mapping does not exist."
    )
)

GET_DESCRIPTION = Description().add_head("Fetches a list of operator role mappings.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_OPERATOR_ROLE_MAP,
    summary="Create operator role map",
    tags=["Operator Role Map"],
    response_model=OperatorRoleMapSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.operator.role.update` permission."
        )
        .add_line(
            "`company_id` is required and used to validate operator and role ownership."
        )
        .to_string()
    ),
)
async def create_operator_role_map_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )
        return create_operator_role_map(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
            operator_filter=(Operator.company_id == form_param.company_id),
            role_filter=(OperatorRole.company_id == form_param.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_OPERATOR_ROLE_MAP}/{{id}}",
    summary="Update operator role map",
    tags=["Operator Role Map"],
    response_model=OperatorRoleMapSchema,
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
async def update_operator_role_map_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )
        return update_operator_role_map(
            session,
            id,
            form_param,
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_OPERATOR_ROLE_MAP}/{{id}}",
    summary="Delete operator role map",
    tags=["Operator Role Map"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `company.operator.role.update` permission."
        )
        .to_string()
    ),
)
async def delete_operator_role_map_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )
        delete_operator_role_map(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_OPERATOR_ROLE_MAP,
    summary="Fetch operator role map",
    tags=["Operator Role Map"],
    response_model=list[OperatorRoleMapSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_operator_role_maps_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_operator_role_maps(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_ROLE_MAP,
    summary="Create operator role map",
    tags=["Role Map"],
    response_model=OperatorRoleMapSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.operator.role.update` permission."
        )
        .to_string()
    ),
)
async def create_operator_role_map_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )
        return create_operator_role_map(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
            operator_filter=(Operator.company_id == token.company_id),
            role_filter=(OperatorRole.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_OPERATOR_ROLE_MAP}/{{id}}",
    summary="Update operator role map",
    tags=["Role Map"],
    response_model=OperatorRoleMapSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.operator.role.update` permission."
        )
        .to_string()
    ),
)
async def update_operator_role_map_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )
        return update_operator_role_map(
            session,
            id,
            form_param,
            token,
            request_info,
            role_map_filter=(OperatorRoleMap.company_id == token.company_id),
            role_filter=(OperatorRole.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_OPERATOR_ROLE_MAP}/{{id}}",
    summary="Delete operator role map",
    tags=["Role Map"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in operator must have the `company.operator.role.update` permission."
        )
        .to_string()
    ),
)
async def delete_operator_role_map_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )
        delete_operator_role_map(
            session,
            id,
            token,
            request_info,
            role_map_filter=(OperatorRoleMap.company_id == token.company_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_OPERATOR_ROLE_MAP,
    summary="Fetch operator role map",
    tags=["Role Map"],
    response_model=list[OperatorRoleMapSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_operator_role_maps_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_operator_role_maps(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
