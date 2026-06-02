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
    Operator,
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
from app.src.digital_ticket.v1 import TicketSchema, TicketTypeSchema, TwoDecimalPlaces
from app.api.service import construct_service_transition_lock
from app.src.enums import PaperTicketWarning

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class TicketSchema(BaseModel):
    sequence_id: int
    warnings: List[PaperTicketWarning] | None = None
    uploaded_by: int | None = None


class PaperTicketSchema(BaseModel):
    id: int
    duty_id: int
    ticket: TicketSchema
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class PaperTicketForm(BaseModel):
    """Form data for a ticket within a paper ticket."""

    operator_id: int = Field()
    sequence_id: int = Field()
    created_on: datetime = Field()
    ticket_types: List[TicketTypeSchema] = Field()
    amount: TwoDecimalPlaces = Field()
    pickup_point: int = Field()
    dropping_point: int = Field()
    extras: dict = Field(default_factory=dict)


class CreateForm(BaseModel):
    """
    Form data for creating a new paper ticket."""

    service_id: int = Field()
    tickets: List[PaperTicketForm] = Field(min_length=1, max_length=50)


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
## Functions
# ---------------------------------------------------------------------------
def create_paper_ticket(
    session: Session, token: OperatorToken, form_param: CreateForm
) -> List[dict]:
    """
    create a new paper ticket in the database.

    Args:
        session (Session): Database session for performing queries and transactions.
        token (OperatorToken): Validated operator token containing company and operator information.
        form_param (CreateForm): Validated input data for creating the paper ticket, including service ID, ticket details, and total amount.

    Raises:
        exceptions.UnknownValue: If the service ID does not correspond to a valid service for the operator's company, or if required landmarks are missing.
        exceptions.InactiveResource: If a resource state prevents ticket creation.
        exceptions.UnknownTicketType: If any ticket type ID in the input does not match the ticket types defined in the service fare configuration.

    Returns:
        List[dict]: List of created paper ticket records as JSON-serializable dicts. Each ticket payload may include `uploaded_by` and `warnings`.
    """
    service_lock = None
    try:
        # Check that all sequence_ids in the batch are unique
        sequence_ids = [ticket.sequence_id for ticket in form_param.tickets]
        if len(sequence_ids) != len(set(sequence_ids)):
            raise exceptions.InvalidValue("sequence_id")

        service = validate_id(
            session,
            Service,
            form_param.service_id,
            PaperTicket.service_id,
            (Service.company_id == token.company_id),
        )
        service_lock = acquire_lock(construct_service_transition_lock(service.id))
        session.refresh(service)
        # Batch fetch all landmarks for the service
        landmarks_in_service = (
            session.query(LandmarkInService)
            .filter(LandmarkInService.service_id == form_param.service_id)
            .all()
        )
        landmarks_map = {lis.landmark_id: lis for lis in landmarks_in_service}

        # Validate pickup point, dropping point, and distance for each ticket
        for ticket in form_param.tickets:
            pickup_point = landmarks_map.get(ticket.pickup_point)
            if pickup_point is None:
                raise exceptions.UnknownValue("pickup_point")
            dropping_point = landmarks_map.get(ticket.dropping_point)
            if dropping_point is None:
                raise exceptions.UnknownValue("dropping_point")
            distance = (
                dropping_point.distance_from_start - pickup_point.distance_from_start
            )
            if distance < 0:
                raise exceptions.UnknownValue("dropping_point")

        # Batch fetch fare configuration
        fare_in_service = (
            session.query(FareInService)
            .filter(FareInService.id == service.fare_in_service_id)
            .first()
        )
        fare_function = v1.DynamicFare(fare_in_service.function)
        fare_ticket_types = fare_in_service.attributes["ticket_types"]
        fare_ticket_types_map = {ft["id"]: ft for ft in fare_ticket_types}

        # Validate fare and amount for each ticket
        ticket_warnings_map = {}  # Maps ticket.sequence_id to warnings list
        for ticket in form_param.tickets:
            warnings = []
            ticket_total_fare = Decimal(0)
            extras = jsonable_encoder(ticket.extras)

            # Calculate distance for this ticket
            pickup_landmark = landmarks_map.get(ticket.pickup_point)
            dropping_landmark = landmarks_map.get(ticket.dropping_point)
            distance = (
                dropping_landmark.distance_from_start
                - pickup_landmark.distance_from_start
            )

            # Validate each ticket type and calculate fare
            for ticket_type in ticket.ticket_types:
                matched_ticket_type = fare_ticket_types_map.get(ticket_type.id)
                if matched_ticket_type is None:
                    raise exceptions.UnknownTicketType()

                expected_price = Decimal(
                    str(
                        fare_function.evaluate(
                            matched_ticket_type["name"], distance, extras
                        )
                    )
                )
                ticket_total_fare += expected_price * ticket_type.count

            # Check for amount mismatch and add warning once if needed
            if ticket_total_fare != ticket.amount:
                warnings.append(PaperTicketWarning.AMOUNT_MISMATCH)

            ticket_warnings_map[ticket.sequence_id] = warnings

        if service.status != ServiceStatus.STARTED:
            service.status = ServiceStatus.STARTED

        # Cache for duties keyed by operator_id (None for orphaned duties)
        duties_cache = {}

        # Create paper tickets for each ticket in the batch
        paper_tickets = []
        for ticket in form_param.tickets:
            warnings = ticket_warnings_map.get(ticket.sequence_id, [])

            # Resolve operator
            operator = (
                session.query(Operator)
                .filter(
                    Operator.id == ticket.operator_id,
                    Operator.company_id == token.company_id,
                )
                .first()
            )

            ticket_data = ticket.model_dump(mode="json")
            if operator is None:
                warnings.append(PaperTicketWarning.OPERATOR_NOT_FOUND)
            elif operator.id != token.operator_id:
                warnings.append(PaperTicketWarning.OPERATOR_MISMATCH)
                ticket_data["uploaded_by"] = token.operator_id

            if warnings:
                ticket_data["warnings"] = [w.value for w in warnings]

            duty_operator_id = operator.id if operator else None

            # Check cache for existing duty
            if duty_operator_id not in duties_cache:
                duty = (
                    session.query(Duty)
                    .filter(
                        Duty.service_id == form_param.service_id,
                        Duty.operator_id == duty_operator_id,
                        Duty.status.in_((DutyStatus.STARTED, DutyStatus.ENDED)),
                    )
                    .first()
                )

                if duty is None:
                    # Create new duty
                    duty = Duty(
                        company_id=token.company_id,
                        operator_id=duty_operator_id,
                        service_id=form_param.service_id,
                        status=DutyStatus.STARTED,
                        started_on=ticket.created_on,
                    )
                    session.add(duty)
                    session.flush()
                elif duty.status == DutyStatus.ENDED:
                    # Reactivate ended duty
                    duty.status = DutyStatus.STARTED
                    duty.finished_on = None
                    duty.collection = 0
                    session.flush()

                duties_cache[duty_operator_id] = duty
            else:
                duty = duties_cache[duty_operator_id]

            # Create paper ticket
            paper_ticket = PaperTicket(
                service_id=form_param.service_id,
                duty_id=duty.id,
                company_id=token.company_id,
                ticket=ticket_data,
                amount=ticket.amount,
            )
            session.add(paper_ticket)
            paper_tickets.append(paper_ticket)

        session.commit()
        for paper_ticket in paper_tickets:
            session.refresh(paper_ticket)

        return jsonable_encoder(paper_tickets)
    finally:
        release_lock(service_lock)


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
    .add_line("Supports batch uploads")
    .add_line(
        "Each ticket may specify its own `operator_id` and will be processed individually."
    )
    .add_line(
        "If an operator is not found, the ticket is attached to an orphaned duty (operator_id = NULL)."
    )
    .add_line(
        "If a duty is ENDED, it is reactivated to STARTED with `finished_on` cleared."
    )
    .add_line(
        "Ticket pickup/dropping points are validated against service landmarks (batch-validated)."
    )
    .add_line("Ticket fares are validated server-side using the service fare function.")
    .add_line(
        "Amount mismatches do NOT abort the batch; a `AMOUNT_MISMATCH` warning is added to the ticket payload."
    )
    .add_line(
        "If `ticket.operator_id` differs from the uploader, an `OPERATOR_MISMATCH` warning is added and `uploaded_by` records the uploader."
    )
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
    response_model=List[PaperTicketSchema],
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    response_model_exclude_none=True,
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
