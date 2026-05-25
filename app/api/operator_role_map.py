"""
Operator Role Map API Router for EnteBus.

Provides endpoints for managing operator role mappings, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from fastapi import APIRouter, Response, status, Depends, Query
from enum import StrEnum
from typing import List
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

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
from app.src.enums import OrderIn
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.openobserve import log_event
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    verify_permission,
    verify_token,
    validate_id,
)
from app.src.functions import (
    fuse_exception_responses,
    get_executive_roles,
    get_request_info,
    get_operator_roles,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
    enum_str,
)
from app.src.filters import IDFilter, CreatedOnFilter, UpdatedOnFilter, PaginationFilter
from app.src.description import Description

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
class CreateForm(BaseModel):
    """Form data for creating a new operator role mapping."""

    role_id: int = Field()
    operator_id: int = Field()


class UpdateForm(BaseModel):
    """Form data for updating an operator role mapping."""

    role_id: int = Field(default=None)


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
## Functions
# ---------------------------------------------------------------------------
def search_role_map(
    session: Session, query_params: QueryParams
) -> list[OperatorRoleMap]:
    """
    Search for operator role mappings based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve operator role mappings that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[OperatorRoleMap]: List of OperatorRoleMap instances that match the search criteria.
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

    role_maps = query.all()
    return role_maps


def delete_role_map(session: Session, role_map: OperatorRoleMap) -> dict:
    """
    Deletes an OperatorRoleMap from the database.

    Args:
        session (Session): SQLAlchemy database session.
        role_map (OperatorRoleMap): OperatorRoleMap to delete.

    Returns:
        dict: JSON-encoded representation of the deleted role mapping.
    """
    role_map_data = jsonable_encoder(role_map)
    session.delete(role_map)
    session.commit()
    return role_map_data


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
## Common descriptions
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
    responses=fuse_exception_responses(
        [
            *POST_EXCEPTIONS,
            exceptions.InvalidAssociation(
                OperatorRoleMap.role_id, OperatorRoleMap.operator_id
            ),
        ]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.operator.role.update` permission."
        )
        .to_string()
    ),
)
async def create_operator_role_map_for_executive(
    form_param: CreateForm,
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

        operator = validate_id(
            session, Operator, form_param.operator_id, OperatorRoleMap.operator_id
        )
        role = validate_id(
            session, OperatorRole, form_param.role_id, OperatorRoleMap.role_id
        )
        if role.company_id != operator.company_id:
            raise exceptions.InvalidAssociation(
                OperatorRoleMap.role_id, OperatorRoleMap.operator_id
            )

        role_map = OperatorRoleMap(
            role_id=role.id, operator_id=operator.id, company_id=operator.company_id
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
    f"{URL_OPERATOR_ROLE_MAP}/{{id}}",
    summary="Update operator role map",
    tags=["Operator Role Map"],
    response_model=OperatorRoleMapSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            *PATCH_EXCEPTIONS,
            exceptions.InvalidAssociation(
                OperatorRoleMap.role_id, OperatorRoleMap.operator_id
            ),
        ]
    ),
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
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )

        role_map = validate_id(
            session,
            OperatorRoleMap,
            id,
            OperatorRoleMap.id,
        )
        if form_param.role_id is not None and role_map.role_id != form_param.role_id:
            role = validate_id(
                session,
                OperatorRole,
                form_param.role_id,
                OperatorRoleMap.role_id,
            )
            if role.company_id != role_map.company_id:
                raise exceptions.InvalidAssociation(
                    OperatorRoleMap.role_id, OperatorRoleMap.operator_id
                )
            role_map.role_id = form_param.role_id

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
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )

        role_map = (
            session.query(OperatorRoleMap).filter(OperatorRoleMap.id == id).first()
        )
        if role_map is not None:
            role_map_data = delete_role_map(session, role_map)
            log_event(token, request_info, role_map_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_OPERATOR_ROLE_MAP,
    summary="Fetch operator role map",
    tags=["Operator Role Map"],
    response_model=List[OperatorRoleMapSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_operator_role_maps_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_role_map(
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
    form_param: CreateForm,
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

        operator = validate_id(
            session,
            Operator,
            form_param.operator_id,
            OperatorRoleMap.operator_id,
            extra_filter=(Operator.company_id == token.company_id),
        )
        role = validate_id(
            session,
            OperatorRole,
            form_param.role_id,
            OperatorRoleMap.role_id,
            extra_filter=(OperatorRole.company_id == token.company_id),
        )

        role_map = OperatorRoleMap(
            role_id=role.id, operator_id=operator.id, company_id=token.company_id
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
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )

        role_map = validate_id(
            session,
            OperatorRoleMap,
            id,
            OperatorRoleMap.id,
            extra_filter=(OperatorRoleMap.company_id == token.company_id),
        )

        if form_param.role_id is not None and role_map.role_id != form_param.role_id:
            validate_id(
                session,
                OperatorRole,
                form_param.role_id,
                OperatorRoleMap.role_id,
                extra_filter=(OperatorRole.company_id == token.company_id),
            )
            role_map.role_id = form_param.role_id

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
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_OPERATOR_ROLE],
        )

        role_map = (
            session.query(OperatorRoleMap)
            .filter(
                OperatorRoleMap.id == id, OperatorRoleMap.company_id == token.company_id
            )
            .first()
        )
        if role_map is not None:
            role_map_data = delete_role_map(session, role_map)
            log_event(token, request_info, role_map_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_OPERATOR_ROLE_MAP,
    summary="Fetch operator role map",
    tags=["Role Map"],
    response_model=List[OperatorRoleMapSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "Only operator role mappings belonging to the same company as the logged-in operator will be returned."
        )
        .to_string()
    ),
)
async def fetch_operator_role_maps_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_role_map(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
