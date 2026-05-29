"""
Paper Ticket API Router for EnteBus.

Provides endpoints for managing paper tickets, including creation and retrieval.
Uses Pydantic schemas for input validation and structured output.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import List
from fastapi import APIRouter, Depends, status, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src.db import (
    SessionLocal,
    OperatorToken,
    ExecutiveToken,
    PaperTicket,
    Service,
    Duty,
    FareInService,
    LandmarkInService,
)
from app.src.enums import DutyStatus, ServiceStatus, OrderIn
from app.src.urls import URL_PAPER_TICKET
from app.src.description import Description
from app.src.validators import (
    verify_token,
    validate_id,
    authorize_operator,
)
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.functions import (
    fuse_exception_responses,
    enum_str,
    apply_id_filters,
    apply_created_on_filters,
)
from app.src.filters import PaginationFilter, IDFilter, CreatedOnFilter
from app.src import exceptions
from app.src.redis import acquire_lock, release_lock
from app.src.dynamic_fare import v1
from app.src.digital_ticket.v1 import TicketSchema, TwoDecimalPlaces

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class PaperTicketSchema(BaseModel):
    """Schema for paper ticket response."""

    id: int
    service_id: int
    duty_id: int
    company_id: int
    ticket: TicketSchema
    amount: TwoDecimalPlaces
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """
    Form data for creating a new paper ticket."""

    ticket: TicketSchema = Field()


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering paper ticket results."""

    ID = "id"
    CREATED_ON = "created_on"


class QueryParamsForOP(PaginationFilter, IDFilter, CreatedOnFilter):
    """Query parameters for operators."""

    service_id: int | None = Field(Query(default=None))
    duty_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """Generic combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Lock Generator
# ---------------------------------------------------------------------------
def create_operator_service_lock(
    operator_id: int,
    service_id: int,
) -> str:
    """
    Creates a unique Redis lock key for an operator-service pair.

    Args:
        operator_id (int): The operator ID.
        service_id (int): The service ID.
    """
    return f"lk_operator_service:{operator_id}:{service_id}"


# ---------------------------------------------------------------------------
## Functions
# ---------------------------------------------------------------------------
def create_paper_ticket(
    session: Session, token: OperatorToken, form_param: CreateForm
) -> dict:
    """
    create a new paper ticket in the database.

    Args:
        session (Session): Database session for performing queries and transactions.
        token (OperatorToken): Validated operator token containing company and operator information.
        form_param (CreateForm): Validated input data for creating the paper ticket, including service ID, ticket details, and total amount.

    Raises:
        exceptions.UnknownValue: If the service ID does not correspond to a valid service for the operator's company, or if the boarding/alight landmarks are not valid for the service.
        exceptions.InactiveResource: If the duty associated with the service is in a state that cannot accept new tickets (e.g., AUDITED).
        exceptions.InvalidValue: If the provided amount does not match the calculated total fare, or if any ticket type has an invalid price.
        exceptions.UnknownTicketType: If any ticket type ID in the input does not match the ticket types defined in the service fare configuration.

    Returns:
        dict: The created paper ticket data.
    """
    service = validate_id(
        session,
        Service,
        form_param.ticket.service_id,
        PaperTicket.service_id,
        (Service.company_id == token.company_id),
    )
    if service.status != ServiceStatus.STARTED:
        service.status = ServiceStatus.STARTED

    duty_lock = None
    try:
        duty_lock = acquire_lock(
            create_operator_service_lock(
                token.operator_id,
                form_param.ticket.service_id,
            )
        )

        duty = (
            session.query(Duty)
            .filter(
                Duty.service_id == form_param.ticket.service_id,
                Duty.operator_id == token.operator_id,
                Duty.status.in_((DutyStatus.STARTED, DutyStatus.ENDED)),
            )
            .first()
        )
        if duty is None:
            duty = Duty(
                company_id=token.company_id,
                operator_id=token.operator_id,
                service_id=form_param.ticket.service_id,
                status=DutyStatus.STARTED,
                started_on=form_param.ticket.created_on,
            )
            session.add(duty)
            session.flush()
        elif duty.status == DutyStatus.ENDED:
            duty.status = DutyStatus.STARTED
            duty.finished_on = None
            duty.collection = 0
            session.flush()

        ticket = form_param.ticket
        pickup_point = (
            session.query(LandmarkInService)
            .filter(
                LandmarkInService.service_id == form_param.ticket.service_id,
                LandmarkInService.landmark_id == ticket.pickup_point,
            )
            .first()
        )
        if pickup_point is None:
            raise exceptions.UnknownValue("pickup_point")
        dropping_point = (
            session.query(LandmarkInService)
            .filter(
                LandmarkInService.service_id == form_param.ticket.service_id,
                LandmarkInService.landmark_id == ticket.dropping_point,
            )
            .first()
        )
        if dropping_point is None:
            raise exceptions.UnknownValue("dropping_point")

        distance = dropping_point.distance_from_start - pickup_point.distance_from_start
        if distance < 0:
            raise exceptions.UnknownValue("dropping_point")
        if distance != ticket.distance:
            raise exceptions.UnknownValue("distance")

        fare_in_service = (
            session.query(FareInService)
            .filter(FareInService.id == service.fare_in_service_id)
            .first()
        )
        fare_function = v1.DynamicFare(fare_in_service.function)
        fare_ticket_types = fare_in_service.attributes["ticket_types"]
        extras = jsonable_encoder(ticket.extras)
        total_fare = Decimal(0)

        for ticket_type in ticket.ticket_types:
            matched_ticket_type = next(
                (ft for ft in fare_ticket_types if ft["id"] == ticket_type.id),
                None,
            )
            if matched_ticket_type is None:
                raise exceptions.UnknownTicketType()

            expected_price = Decimal(
                str(fare_function.evaluate(matched_ticket_type["name"], distance, extras))
            )
            if ticket_type.price != expected_price:
                raise exceptions.InvalidValue(PaperTicket.amount)
            total_fare += ticket_type.price * ticket_type.count

        if total_fare != form_param.ticket.amount:
            raise exceptions.InvalidValue(PaperTicket.amount)

        paper_ticket = PaperTicket(
            service_id=form_param.ticket.service_id,
            duty_id=duty.id,
            company_id=token.company_id,
            ticket=ticket.model_dump(mode="json"),
            amount=form_param.ticket.amount,
        )
        session.add(paper_ticket)
        session.commit()
        session.refresh(paper_ticket)

        return jsonable_encoder(paper_ticket)
    finally:
        release_lock(duty_lock)


def search_paper_tickets(
    session: Session, query_params: QueryParams
) -> List[PaperTicket]:
    """
    Search for paper tickets provided on query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve paper tickets that match various criteria.

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
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(PaperTicket.service_id),
    exceptions.InvalidValue(PaperTicket.amount),
    exceptions.UnknownTicketType(),
    exceptions.InvalidFareFunction(),
    exceptions.JSMemoryLimitExceeded(),
    exceptions.JSTimeLimitExceeded(),
    exceptions.LockAcquireTimeout(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common descriptions
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new paper ticket for an operator's duty.")
    .add_line(
        "Logged-in operator must have `company.service.ticket.create` permission."
    )
    .add_line("Service must belong to the operator's company.")
    .add_line("If no active duty exists, a new duty is created automatically.")
    .add_line(
        "If a duty is ENDED, it is reactivated to STARTED with `finished_on` cleared."
    )
    .add_line("A duty in AUDITED status is ignored; a new duty is created instead.")
    .add_line(
        "`ticket.pickup_point` and `ticket.dropping_point` must be landmarks assigned to the service."
    )
    .add_line(
        "Ticket type IDs must match those defined in the service fare configuration."
    )
    .add_line("Prices are cross-validated server-side using the fare function.")
    .add_line("`amount` must equal the sum of (price × count) for all ticket types.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of paper tickets.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_PAPER_TICKET,
    summary="Fetch paper ticket",
    tags=["Paper Ticket"],
    response_model=List[PaperTicketSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_paper_tickets_for_executive(
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
@route_operator.post(
    URL_PAPER_TICKET,
    summary="Create paper ticket",
    tags=["Paper Ticket"],
    response_model=PaperTicketSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_paper_ticket_for_operator(
    form_param: CreateForm,
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_SERVICE_TICKET],
        )

        paper_ticket_data = create_paper_ticket(session, token, form_param)
        return paper_ticket_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_PAPER_TICKET,
    summary="Fetch paper ticket",
    tags=["Paper Ticket"],
    response_model=List[PaperTicketSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_paper_tickets_for_operator(
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
