"""
Paper Ticket API Router.

Provides endpoints for managing landmarks:
    - POST (operator)
    - GET (executive, operator)
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from fastapi import APIRouter, Depends, status, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src.db import (
    OperatorToken,
    ExecutiveToken,
    PaperTicket,
    Service,
    Duty,
    FareInService,
    LandmarkInService,
    Operator,
    ServiceLocation,
    get_db_session,
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
from app.src.digital_ticket.v1 import TwoDecimalPlaces, TicketTypeSchema
from app.api.service import construct_service_transition_lock
from app.src.enums import PaperTicketWarning

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class MinimalPaperTicketDetailSchema(BaseModel):
    """Schema for the paper ticket batch response."""

    sequence_id: int
    warnings: list[PaperTicketWarning] = Field(default_factory=list)
    uploaded_by: int | None = None


class MinimalPaperTicketSchema(BaseModel):
    """Schema for paper ticket batch response."""

    id: int
    duty_id: int
    ticket: MinimalPaperTicketDetailSchema
    created_on: datetime


class PaperTicketDetailSchema(MinimalPaperTicketDetailSchema):
    """Schema for paper ticket detail response."""

    ticket_types: list[TicketTypeSchema]
    pickup_point: int
    dropping_point: int
    extras: dict = Field(default_factory=dict)
    created_on: datetime


class PaperTicketSchema(BaseModel):
    """Schema for paper ticket response."""

    id: int
    service_id: int
    duty_id: int
    company_id: int
    amount: TwoDecimalPlaces
    ticket: PaperTicketDetailSchema
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class PaperTicketForm(BaseModel):
    """Form data for a ticket within a paper ticket."""

    operator_id: int = Field()
    sequence_id: int = Field()
    created_on: datetime = Field()
    ticket_types: list[TicketTypeSchema] = Field()
    amount: TwoDecimalPlaces = Field()
    pickup_point: int = Field()
    dropping_point: int = Field()
    extras: dict = Field(default_factory=dict)


class CreateForm(BaseModel):
    """Form data for creating a new paper ticket."""

    service_id: int = Field()
    tickets: list[PaperTicketForm] = Field(min_length=1, max_length=50)


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
## Core Functions
# ---------------------------------------------------------------------------
def create_paper_ticket(
    session: Session, token: OperatorToken, form_param: CreateForm
) -> list[dict]:
    """
    Create a new paper ticket in the database.

    Args:
        session (Session): Database session for performing queries and transactions.
        token (OperatorToken): Validated operator token containing company and operator information.
        form_param (CreateForm): Validated input data for creating the paper ticket, including service ID, ticket details, and total amount.

    Raises:
        exceptions.UnknownValue: If the service ID does not correspond to a valid service for the operator's company, or if required landmarks are missing.
        exceptions.InvalidValue: If there are duplicate sequence IDs, or if the ticket amount does not match the calculated fare.
        exceptions.UnknownTicketType: If any ticket type ID in the input does not match the ticket types defined in the service fare configuration.

    Returns:
        list[dict]: List of created paper ticket records as JSON-serializable dicts. Each ticket payload may include `uploaded_by` and `warnings`.
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

        # Fetch all landmarks in that service and construct a map for validation and distance calculation
        landmarks_in_service = (
            session.query(LandmarkInService)
            .filter(LandmarkInService.service_id == form_param.service_id)
            .all()
        )
        landmarks_in_service_map = {
            lis.landmark_id: lis for lis in landmarks_in_service
        }

        # Fetch fare function and ticket types for the service and construct a map for validation
        fare_in_service = (
            session.query(FareInService)
            .filter(FareInService.id == service.fare_in_service_id)
            .first()
        )
        assert fare_in_service is not None, "FareInService should exist for the service"
        fare_function = v1.DynamicFare(fare_in_service.function)
        fare_ticket_types = fare_in_service.attributes["ticket_types"]
        fare_ticket_types_map = {ft["id"]: ft for ft in fare_ticket_types}

        # Validate each ticket in the batch and collect warnings without aborting the process
        # Maps ticket.sequence_id to list of warnings for that ticket
        duty_cache: dict[int | None, Duty] = (
            {}
        )  # Cache to store operator_id to Duty mapping
        paper_tickets: list[PaperTicket] = []

        service_location = (
            session.query(ServiceLocation)
            .filter(ServiceLocation.service_id == form_param.service_id)
            .first()
        )
        assert (
            service_location is not None
        ), "ServiceLocation should exist for the service"
        current_landmark = landmarks_in_service_map.get(service_location.landmark_id)
        assert (
            current_landmark is not None
        ), "Current landmark in service should exist for the service location"

        for ticket in form_param.tickets:
            pickup_point = landmarks_in_service_map.get(ticket.pickup_point)
            if pickup_point is None:
                raise exceptions.UnknownValue("pickup_point")

            if pickup_point.distance_from_start > current_landmark.distance_from_start:
                current_landmark = pickup_point

            dropping_point = landmarks_in_service_map.get(ticket.dropping_point)
            if dropping_point is None:
                raise exceptions.UnknownValue("dropping_point")

            distance = (
                dropping_point.distance_from_start - pickup_point.distance_from_start
            )
            if distance <= 0:
                raise exceptions.InvalidValue("dropping_point")

            # Validate each ticket type and calculate fare
            total_fare = Decimal(0)
            extras = jsonable_encoder(ticket.extras)
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
                if ticket_type.price != expected_price:
                    raise exceptions.InvalidValue(PaperTicket.amount)
                total_fare += expected_price * ticket_type.count
            if total_fare != ticket.amount:
                raise exceptions.InvalidValue(PaperTicket.amount)

            # Check for amount mismatch and operator mismatch, but do not abort the process,
            # instead, record warnings and uploaded_by info in the ticket payload
            warnings = []
            operator = (
                session.query(Operator)
                .filter(
                    Operator.id == ticket.operator_id,
                    Operator.company_id == token.company_id,
                )
                .first()
            )
            if operator is None:
                warnings.append(PaperTicketWarning.OPERATOR_NOT_FOUND)
            if token.operator_id != ticket.operator_id:
                warnings.append(PaperTicketWarning.OPERATOR_MISMATCH)

            if operator is not None:
                operator_id = operator.id
            else:
                operator_id = None  # Orphaned duty for missing operator

            # Check cache for existing duty for the operator
            duty = None
            if operator_id not in duty_cache:
                duty = (
                    session.query(Duty)
                    .filter(
                        Duty.service_id == form_param.service_id,
                        Duty.operator_id == operator_id,
                        Duty.status.in_((DutyStatus.STARTED, DutyStatus.ENDED)),
                    )
                    .first()
                )

                # Create new duty if not found, or reactivate if ended
                if duty is None:
                    duty = Duty(
                        company_id=token.company_id,
                        operator_id=operator_id,
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
                    duty.collection = Decimal(0)
                    session.flush()

                duty_cache[operator_id] = duty
            else:
                duty = duty_cache[operator_id]

            # Create paper ticket with warnings and uploaded_by info if applicable
            paper_ticket = PaperTicket(
                service_id=form_param.service_id,
                duty_id=duty.id,
                company_id=token.company_id,
                ticket={
                    "sequence_id": ticket.sequence_id,
                    "created_on": ticket.created_on.isoformat(),
                    "ticket_types": [
                        tt.model_dump(mode="json") for tt in ticket.ticket_types
                    ],
                    "pickup_point": ticket.pickup_point,
                    "dropping_point": ticket.dropping_point,
                    "extras": extras,
                },
                amount=ticket.amount,
            )
            if warnings:
                paper_ticket.ticket["warnings"] = [w.value for w in warnings]
            if token.operator_id != ticket.operator_id:
                paper_ticket.ticket["uploaded_by"] = token.operator_id
            session.add(paper_ticket)
            paper_tickets.append(paper_ticket)

        if service.status != ServiceStatus.STARTED:
            service.status = ServiceStatus.STARTED

        service_location.landmark_id = current_landmark.landmark_id

        session.commit()
        for paper_ticket in paper_tickets:
            session.refresh(paper_ticket)
        return jsonable_encoder(paper_tickets)
    finally:
        release_lock(service_lock)


def search_paper_tickets(
    session: Session, query_params: QueryParams
) -> list[PaperTicket]:
    """
    Search for paper tickets provided on query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve paper tickets that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.


    Returns:
        list[PaperTicket]: List of paper tickets that match the search criteria.
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
    exceptions.InvalidValue("sequence_id"),
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
    .add_line("Supports batch uploads")
    .add_line(
        "Each ticket may specify its own `operator_id` and will be processed individually."
    )
    .add_line(
        "If an operator is not found, the ticket is attached to an orphaned duty."
    )
    .add_line(
        "If a duty is ENDED, it is reactivated to STARTED with `finished_on` cleared."
    )
    .add_line(
        "Ticket pickup/dropping points are validated against service landmarks (batch-validated)."
    )
    .add_line("Ticket fares are validated server-side using the service fare function.")
    .add_line("Amount mismatches will raise an `InvalidValue` exception.")
    .add_line(
        "If the specified operator is not found, an `OPERATOR_NOT_FOUND` warning is added. If `ticket.operator_id` differs from the uploader, an `OPERATOR_MISMATCH` warning is added and `uploaded_by` records the uploader."
    )
    .add_line(
        "If no warnings are generated, `warnings` will be empty. `uploaded_by` is only populated when an operator mismatch occurs."
    )
    .add_line("A maximum of 50 tickets can be created in a single batch upload.")
    .add_line("Duplicate sequence IDs within the same batch are not allowed.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of paper tickets.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_PAPER_TICKET,
    summary="Fetch paper ticket",
    tags=["Paper Ticket"],
    response_model=list[PaperTicketSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_paper_tickets_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_paper_tickets(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_PAPER_TICKET,
    summary="Create paper ticket",
    tags=["Paper Ticket"],
    response_model=list[PaperTicketSchema],
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def create_paper_ticket_for_operator(
    form_param: CreateForm,
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_SERVICE_TICKET],
        )
        return create_paper_ticket(session, token, form_param)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_PAPER_TICKET,
    summary="Fetch paper ticket",
    tags=["Paper Ticket"],
    response_model=list[PaperTicketSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_paper_tickets_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_paper_tickets(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
