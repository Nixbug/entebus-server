"""
Duty API Router for EnteBus.

Provides endpoints for managing duties, including update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for retrieval are planned for future implementation.
"""

from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session
from sqlalchemy import func


from app.api.bearer import bearer_operator, oauth2_executive
from app.src.db import (
    SessionLocal,
    OperatorToken,
    Duty,
    ExecutiveToken,
    Service,
    PaperTicket,
)
from app.src.enums import DutyStatus, ServiceStatus
from app.src.urls import URL_DUTY
from app.src.validators import (
    verify_token,
    verify_permission,
    validate_id,
    validate_state_transition,
)
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.openobserve import log_event
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_operator_roles,
    get_request_info,
    get_executive_roles,
)
from app.src import exceptions

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


## Input Forms
class UpdateForm(BaseModel):
    """Form data for updating a duty."""

    status: DutyStatus = Field(description=enum_str(DutyStatus), default=None)


# Functions
def update_duty(
    session: Session, duty: Duty, form_param: UpdateForm
) -> tuple[bool, dict]:
    """
    Updates a duty record based on the requested status transition.

    Validates status transitions. Calculates collection from PaperTickets when
    transitioning a duty to ENDED, and reactivates an ENDED service if the duty
    is moved back to STARTED.

    Args:
        session (Session): SQLAlchemy database session.
        duty (Duty): Duty object to update.
        form_param (UpdateForm): Form data containing new status.

    Returns:
        tuple[bool, dict]: (have_updates, duty_data)
    """
    _allowed_transitions = {
        DutyStatus.STARTED: [DutyStatus.ENDED],
        DutyStatus.ENDED: [DutyStatus.STARTED],
    }

    update_data = form_param.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc)
    service = None

    if "status" in update_data and update_data["status"] != duty.status:
        new_status = update_data["status"]

        validate_state_transition(
            _allowed_transitions,
            duty.status,
            new_status,
            Duty.status,
        )
        if new_status == DutyStatus.ENDED:
            duty.collection = (
                session.query(func.sum(PaperTicket.amount))
                .filter(PaperTicket.duty_id == duty.id)
                .scalar()
            )
            duty.finished_on = now
        elif new_status == DutyStatus.STARTED and duty.status == DutyStatus.ENDED:
            duty.finished_on = None
            duty.collection = 0
            service = (
                session.query(Service).filter(Service.id == duty.service_id).first()
            )
            if service.status == ServiceStatus.ENDED:
                service.status = ServiceStatus.STARTED
        duty.status = new_status

    have_updates = session.is_modified(duty) or (
        service is not None and session.is_modified(service)
    )
    if have_updates:
        session.commit()
        session.refresh(duty)

    duty_data = jsonable_encoder(duty)
    return have_updates, duty_data


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.patch(
    f"{URL_DUTY}/{{id}}",
    tags=["Duty"],
    response_model=DutySchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Duty.id),
            exceptions.InvalidStateTransition(Duty.status),
        ]
    ),
    description=("""
            **Updates an existing duty for a service.**    
            - Requires a valid executive access token.    
            - Logged in executive must have `company.service.duty.update` permission.    
            - Allowed status transitions:
              - STARTED → ENDED: Mark duty as finished and calculate collection  
              - ENDED → STARTED: Reactivate duty and clear finished_on and collection  
            - When status transitions to ENDED, collection is calculated from PaperTickets.    
            - Invalid state transitions will raise an exception.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """),
)
async def update_duty_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_SERVICE_DUTY)

        duty = validate_id(session, Duty, id, Duty.id)

        have_updates, duty_data = update_duty(
            session, duty, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, duty_data)
        return duty_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.patch(
    f"{URL_DUTY}/{{id}}",
    tags=["Duty"],
    response_model=DutySchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Duty.id),
            exceptions.InvalidStateTransition(Duty.status),
        ]
    ),
    description=("""
            **Updates an existing duty status.**    
            - Requires a valid operator access token.    
            - Logged in operator must have `company.service.duty.update` permission.    
            - Allowed status transitions:    
              - STARTED → ENDED: Mark duty as finished and calculate collection    
              - ENDED → STARTED: Reactivate duty and clear finished_on and collection    
            - When status transitions to ENDED, collection is calculated from PaperTickets.    
            - Invalid state transitions will raise an exception.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """),
)
async def update_duty_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_SERVICE_DUTY)

        duty = validate_id(
            session, Duty, id, Duty.id, (Duty.company_id == token.company_id)
        )

        have_updates, duty_data = update_duty(
            session, duty, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, duty_data)
        return duty_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
