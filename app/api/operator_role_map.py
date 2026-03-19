"""
Operator Role Map API Router for EnteBus.

Provides endpoints for managing operator role mappings, including creation, update, and retrieval.
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, status, Depends, Query
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
from app.src.validators import verify_permission, verify_token
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
class CreateForm(BaseModel):
    """Form data for creating a new operator role mapping."""

    role_id: int = Field()
    operator_id: int = Field()


class UpdateForm(BaseModel):
    """Form data for updating an operator role mapping."""

    role_id: int | None = Field(default=None)


# Query Parameters
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


# Functions
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


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_OPERATOR_ROLE_MAP,
    tags=["Operator Role Map"],
    response_model=OperatorRoleMapSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(OperatorRoleMap.operator_id),
            exceptions.UnknownValue(OperatorRoleMap.role_id),
            exceptions.InvalidAssociation(
                OperatorRoleMap.role_id, OperatorRoleMap.operator_id
            ),
        ]
    ),
    description=(
        """
            **Creates a new operator role mapping.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.operator.role.update` permission.    
            - Duplicate mappings are not allowed.    
        """
    ),
)
async def create_role_map_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR_ROLE)

        operator = (
            session.query(Operator)
            .filter(Operator.id == form_param.operator_id)
            .first()
        )
        if operator is None:
            raise exceptions.UnknownValue(OperatorRoleMap.operator_id)
        role = (
            session.query(OperatorRole)
            .filter(OperatorRole.id == form_param.role_id)
            .first()
        )
        if role is None:
            raise exceptions.UnknownValue(OperatorRoleMap.role_id)
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

        if form_param.role_id is not None and role_map.role_id != form_param.role_id:
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


@route_executive.get(
    URL_OPERATOR_ROLE_MAP,
    tags=["Operator Role Map"],
    response_model=List[OperatorRoleMapSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of operator role mappings.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_role_map_executive(
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
    tags=["Role Map"],
    response_model=OperatorRoleMapSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(OperatorRoleMap.operator_id),
            exceptions.UnknownValue(OperatorRoleMap.role_id),
        ]
    ),
    description=(
        """
            **Creates a new operator role mapping.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.operator.role.update` permission.    
            - Duplicate mappings are not allowed.    
        """
    ),
)
async def create_role_map_operator(
    form_param: CreateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR_ROLE)

        operator = (
            session.query(Operator)
            .filter(
                Operator.id == form_param.operator_id,
                Operator.company_id == token.company_id,
            )
            .first()
        )
        if operator is None:
            raise exceptions.UnknownValue(OperatorRoleMap.operator_id)
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

        if form_param.role_id is not None and role_map.role_id != form_param.role_id:
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


@route_operator.get(
    URL_OPERATOR_ROLE_MAP,
    tags=["Role Map"],
    response_model=List[OperatorRoleMapSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of operator roles mappings.**    
            - Requires a valid access token for authentication.    
            - Only operator role mappings belonging to the same company as the logged-in operator will be returned.    
        """
    ),
)
async def fetch_role_map_operator(
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
