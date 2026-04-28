"""
Paper Ticket API Router for EnteBus.

Provides endpoints for managing paper tickets, including retrieval.
Uses Pydantic schemas for input validation and structured output.
Endpoints for creation are planned for future implementation.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import List
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    SessionLocal,
    OperatorToken,
    PaperTicket,
    ExecutiveToken,
)
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter
from app.src.urls import URL_PAPER_TICKET
from app.src.validators import verify_token
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
)
from app.src import exceptions
from app.src.digital_ticket.v1 import TicketSchema

route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class PaperTicketSchema(BaseModel):
    """Schema for paper ticket response."""

    id: int
    service_id: int
    duty_id: int
    company_id: int
    ticket: TicketSchema
    amount: Decimal
    created_on: datetime


## Query Params
class OrderBy(StrEnum):
    """Enum for ordering paper ticket results."""

    ID = "id"
    CREATED_ON = "created_on"


class QueryParamsForOP(PaginationFilter, IDFilter, CreatedOnFilter):
    """Query parameters for listing paper tickets."""

    service_id: int | None = Field(Query(default=None))
    duty_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives users."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """Generic query parameters."""

    pass


## Functions
def search_paper_tickets(
    session: Session, query_params: QueryParams
) -> List[PaperTicket]:
    """
    Search for paper tickets provided on query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve fares that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.


    Returns:
        List[PaperTicket]: List of paper tickets that match the search criteria.
    """
    query = session.query(PaperTicket)
    if query_params.company_id is not None:
        query = query.filter(PaperTicket.company_id == query_params.company_id)
    if query_params.service_id is not None:
        query = query.filter(PaperTicket.service_id == query_params.service_id)
    if query_params.duty_id is not None:
        query = query.filter(PaperTicket.duty_id == query_params.duty_id)

    # Generalized filters
    query = apply_id_filters(query, PaperTicket, query_params)
    query = apply_created_on_filters(query, PaperTicket, query_params)

    # ordering and pagination
    ordering_attr = getattr(PaperTicket, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    paper_tickets = query.all()
    return paper_tickets


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------


@route_executive.get(
    URL_PAPER_TICKET,
    tags=["Paper Ticket"],
    response_model=List[PaperTicketSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of paper tickets.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_paper_ticket_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_paper_tickets(
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
@route_operator.get(
    URL_PAPER_TICKET,
    tags=["Paper Ticket"],
    response_model=List[PaperTicketSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of paper tickets.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_paper_ticket_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_paper_tickets(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
