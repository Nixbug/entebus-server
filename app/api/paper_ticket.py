"""
Paper Ticket API Router for EnteBus.

Provides endpoints for managing paper tickets, including creation.
Uses Pydantic schemas for input validation and structured output.
Endpoints for retrieval are planned for future implementation.
"""

from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator
from app.src.db import (
    SessionLocal,
    OperatorToken,
    PaperTicket,
    Service,
    Duty,
    FareInService,
    LandmarkInService,
)
from app.src.enums import DutyStatus, ServiceStatus
from app.src.urls import URL_PAPER_TICKET
from app.src.validators import verify_token, verify_permission, validate_id
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.openobserve import log_event
from app.src.functions import (
    fuse_exception_responses,
    get_operator_roles,
    get_request_info,
)
from app.src import exceptions
from app.src.dynamic_fare import v1
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


## Input Forms
class CreateForm(BaseModel):
    """
    Form data for creating a new paper ticket."""

    ticket: TicketSchema = Field()


## Functions
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

    duty = (
        session.query(Duty)
        .filter(
            Duty.service_id == form_param.ticket.service_id,
            Duty.operator_id == token.operator_id,
            Duty.status.in_((DutyStatus.STARTED, DutyStatus.ENDED)),
        )
        .order_by(Duty.started_on.desc(), Duty.id.desc())
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
        session.flush()

    ticket = form_param.ticket
    boarding_landmark = (
        session.query(LandmarkInService)
        .filter(
            LandmarkInService.service_id == form_param.ticket.service_id,
            LandmarkInService.landmark_id == ticket.boarding_landmark_id,
        )
        .first()
    )
    if boarding_landmark is None:
        raise exceptions.UnknownValue("boarding_landmark_id")
    alight_landmark = (
        session.query(LandmarkInService)
        .filter(
            LandmarkInService.service_id == form_param.ticket.service_id,
            LandmarkInService.landmark_id == ticket.alight_landmark_id,
        )
        .first()
    )
    if alight_landmark is None:
        raise exceptions.UnknownValue("alight_landmark_id")
    distance = (
        alight_landmark.distance_from_start - boarding_landmark.distance_from_start
    )
    if distance < 0:
        raise exceptions.UnknownValue("alight_landmark_id")

    fare_in_service = (
        session.query(FareInService)
        .filter(FareInService.id == service.fare_in_service_id)
        .first()
    )
    fare_function = v1.DynamicFare(fare_in_service.function)
    fare_ticket_types = fare_in_service.attributes["ticket_types"]

    extra_obj = jsonable_encoder(ticket.extra)

    total_fare = Decimal(0)

    for ticket_type in ticket.ticket_types:
        matched = next(
            (ft for ft in fare_ticket_types if ft["id"] == ticket_type.id),
            None,
        )
        if matched is None:
            raise exceptions.UnknownTicketType()

        if ticket_type.count <= 0:
            raise exceptions.UnknownValue("ticket_types")

        expected_price = Decimal(
            str(fare_function.evaluate(matched["name"], distance, extra_obj))
        )
        if ticket_type.price != expected_price:
            raise exceptions.InvalidValue(PaperTicket.amount)

        total_fare += ticket_type.price * ticket_type.count

    if total_fare != form_param.ticket.amount:
        raise exceptions.InvalidValue(PaperTicket.amount)

    ticket_dict = ticket.model_dump()
    ticket_dict["created_on"] = ticket_dict["created_on"].isoformat()
    ticket_dict["amount"] = float(ticket_dict["amount"])
    ticket_dict["distance"] = distance
    for tt in ticket_dict["ticket_types"]:
        tt["price"] = float(tt["price"])

    paper_ticket = PaperTicket(
        service_id=form_param.ticket.service_id,
        duty_id=duty.id,
        company_id=token.company_id,
        ticket=ticket_dict,
        amount=form_param.ticket.amount,
    )
    session.add(paper_ticket)
    session.commit()
    session.refresh(paper_ticket)

    return jsonable_encoder(paper_ticket)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------


@route_operator.post(
    URL_PAPER_TICKET,
    tags=["Paper Ticket"],
    response_model=PaperTicketSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(PaperTicket.service_id),
            exceptions.InvalidValue(PaperTicket.amount),
            exceptions.UnknownTicketType(),
            exceptions.InvalidFareFunction(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.JSTimeLimitExceeded(),
        ]
    ),
    description=(
        """
            **Creates a new paper ticket for an operator's duty.**    
            - Requires a valid operator access token.    
            - Logged-in operator must have `company.service.ticket.create` permission.    
            - Service must belong to the operator's company.    
            - If no active duty exists, a new duty is created automatically.    
            - If a duty is ENDED, it is reactivated to STARTED with `finished_on` cleared.    
            - A duty in AUDITED status is ignored; a new duty is created instead.    
            - `ticket.boarding_landmark_id` and `ticket.alight_landmark_id` must be landmarks assigned to the service.    
            - Ticket type IDs must match those defined in the service fare configuration.    
            - Prices are cross-validated server-side using the fare function.    
            - `amount` must equal the sum of (price × count) for all ticket types.    
        """
    ),
)
async def create_paper_ticket_operator(
    form_param: CreateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_SERVICE_TICKET)

        paper_ticket_data = create_paper_ticket(session, token, form_param)
        log_event(token, request_info, paper_ticket_data)
        return paper_ticket_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
