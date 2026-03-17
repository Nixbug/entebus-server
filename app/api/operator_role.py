"""
Operator Role API Router for EnteBus.

Provides endpoints for managing operator roles, including creation and retrieval.
Uses Pydantic schemas for input validation and structured output.
Endpoints for update and deletion are planned for future implementation.
"""

from datetime import datetime
from typing import List
from fastapi import APIRouter, status, Depends, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from enum import StrEnum
from sqlalchemy.orm import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.api.operator_token import QueryParamsForOP
from app.src import exceptions
from app.src.db import (
    ExecutiveRole,
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
    apply_created_on_filters,
    apply_id_filters,
    apply_name_filters,
    apply_updated_on_filters,
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    get_operator_roles,
)
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


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(
    UpdatedOnFilter, CreatedOnFilter, NameFilter, IDFilter, PaginationFilter
):
    """Query parameters for operators."""

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


def search_role(session: Session, query_params: QueryParams) -> list[OperatorRole]:
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


@route_executive.get(
    URL_OPERATOR_ROLE,
    tags=["Operator Role"],
    response_model=List[OperatorRoleSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of operator roles.**    
            - Requires a valid access token for authentication.     
        """
    ),
)
async def fetch_role_executive(
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


@route_operator.get(
    URL_OPERATOR_ROLE,
    tags=["Role"],
    response_model=List[OperatorRoleSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of operator roles.**    
            - Requires a valid access token for authentication.    
            - Only operator roles belonging to the same company as the logged-in operator will be returned.    
        """
    ),
)
async def fetch_role_operator(
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
