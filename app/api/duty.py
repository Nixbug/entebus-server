"""
Duty API Router for EnteBus.

Provides endpoints for managing duties, including retrieval.
Uses Pydantic schemas for input validation and structured output.
Endpoints for update are planned for future implementation.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.src import exceptions
from app.src.db import (
    ExecutiveToken,
    OperatorToken,
    Duty,
    SessionLocal,
)
from app.src.enums import DutyStatus, OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, UpdatedOnFilter
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    apply_status_filters,
)
from app.src.urls import URL_DUTY
from app.src.validators import verify_token

route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class DutySchema(BaseModel):
    """Schema for duty response."""

    id: int
    company_id: int
    operator_id: int | None
    service_id: int
    status: int
    started_on: datetime | None
    finished_on: datetime | None
    collection: Decimal | None
    updated_on: datetime | None
    created_on: datetime


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(PaginationFilter, IDFilter, CreatedOnFilter, UpdatedOnFilter):
    """Query parameters for operators."""

    service_id: int | None = Field(Query(default=None))
    operator_id: int | None = Field(Query(default=None))
    status_list: List[DutyStatus] | None = Field(
        Query(default=None, description=enum_str(DutyStatus))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executive duty listing."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# Functions
def search_duty(session: Session, query_params: QueryParams) -> List[Duty]:
    """
    Search for Duties based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve duties that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Duty]: List of Duties that match the search criteria.
    """
    query = session.query(Duty)

    if query_params.company_id is not None:
        query = query.filter(Duty.company_id == query_params.company_id)
    if query_params.service_id is not None:
        query = query.filter(Duty.service_id == query_params.service_id)
    if query_params.operator_id is not None:
        query = query.filter(Duty.operator_id == query_params.operator_id)

    # Generalized filters
    query = apply_id_filters(query, Duty, query_params)
    query = apply_created_on_filters(query, Duty, query_params)
    query = apply_updated_on_filters(query, Duty, query_params)
    query = apply_status_filters(query, Duty, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Duty, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    duties = query.all()
    return duties


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_DUTY,
    tags=["Duty"],
    response_model=List[DutySchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of duties.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_duty_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_duty(session, QueryParams(**query_params.model_dump()))
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.get(
    URL_DUTY,
    tags=["Duty"],
    response_model=List[DutySchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of duties for the operator's company.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_duty_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_duty(
            session,
            QueryParams(
                **query_params.model_dump(),
                company_id=token.company_id,
            ),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
