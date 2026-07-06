"""
Duty API router.

Provides endpoints for managing duties:
    - PATCH (operator, executive)
    - GET (operator, executive)
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Union
from fastapi import APIRouter, Depends, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session
from sqlalchemy import func

from app.api.bearer import bearer_operator, oauth2_executive
from app.src.db import (
    OperatorToken,
    Duty,
    ExecutiveToken,
    Service,
    PaperTicket,
    get_db_session,
)
from app.src.enums import DutyStatus, ServiceStatus
from app.src.urls import URL_DUTY
from app.src.validators import (
    verify_token,
    validate_id,
    validate_state_transition,
    authorize_operator,
    authorize_executive,
)
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_status_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
)
from app.src.redis import acquire_lock, release_lock
from app.src import exceptions, schemas
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, UpdatedOnFilter
from app.api.service import construct_service_transition_lock

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class UpdateForm(BaseModel):
    """Form data for updating a duty."""

    status: DutyStatus | None = Field(description=enum_str(DutyStatus), default=None)


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

    service_id: int | None = Field(Query(default=None))
    operator_id: int | None = Field(Query(default=None))
    status_list: list[DutyStatus] | None = Field(
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


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def update_duty(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    duty_filter=None,
) -> dict:
    """
    Updates a duty record based on the requested status transition.

    Validates status transitions. Calculates collection from PaperTickets when
    transitioning a duty to ENDED, and reactivates an ENDED service if the duty
    is moved back to STARTED.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the duty to update.
        form_param (UpdateForm): Form data containing new status.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        duty_filter (Optional): Additional filter for duty validation.

    Returns:
        dict: A dictionary containing the updated duty data.
    """
    service_lock = None
    try:
        duty = validate_id(session, Duty, id, Duty.id, extra_filter=duty_filter)
        service_lock = acquire_lock(construct_service_transition_lock(duty.service_id))
        session.refresh(duty)

        allowed_duty_status_transitions = {
            DutyStatus.STARTED: [DutyStatus.ENDED],
            DutyStatus.ENDED: [DutyStatus.STARTED],
        }

        update_data = form_param.model_dump(exclude_unset=True)
        service = None
        if "status" in update_data:
            if update_data["status"] != duty.status:
                validate_state_transition(
                    allowed_duty_status_transitions,
                    duty.status,
                    update_data["status"],
                    Duty.status,
                )

                # Handle status transitions
                if update_data["status"] == DutyStatus.ENDED:
                    duty.collection = (
                        session.query(func.sum(PaperTicket.amount))
                        .filter(PaperTicket.duty_id == duty.id)
                        .scalar()
                    )
                    utc_now = datetime.now(timezone.utc)
                    duty.finished_on = utc_now
                elif update_data["status"] == DutyStatus.STARTED:
                    duty.finished_on = None
                    duty.collection = Decimal(0)
                    service = (
                        session.query(Service)
                        .filter(Service.id == duty.service_id)
                        .first()
                    )
                    assert service is not None, "Service cannot be None."
                    if service.status == ServiceStatus.ENDED:
                        service.status = ServiceStatus.STARTED
                duty.status = update_data["status"]
            update_data.pop("status")

        if session.is_modified(duty) or (service and session.is_modified(service)):
            session.commit()
            session.refresh(duty)
            duty_data = jsonable_encoder(duty)
            log_event(token, request_info, duty_data)
        else:
            duty_data = jsonable_encoder(duty)
        return duty_data
    finally:
        release_lock(service_lock)


def search_duties(session: Session, query_params: QueryParams) -> list[Duty]:
    """
    Search for Duties based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve duties that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Duty]: List of Duties that match the search criteria.
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
## Common exceptions
# ---------------------------------------------------------------------------
PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Duty.id),
    exceptions.InvalidStateTransition(Duty.status),
    exceptions.LockAcquireTimeout(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing duty status.")
    .add_line("Allowed status transitions:")
    .add_line("STARTED → ENDED: Mark duty as finished and calculate collection")
    .add_line("ENDED → STARTED: Reactivate duty and clear finished_on and collection")
    .add_line(
        "When status transitions to ENDED, collection is calculated from paper tickets registered under this duty."
    )
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of duties.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.patch(
    f"{URL_DUTY}/{{id}}",
    summary="Update duty",
    tags=["Duty"],
    response_model=DutySchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged in executive must have `company.service.duty.update` permission."
        )
        .to_string()
    ),
)
async def update_duty_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_SERVICE_DUTY],
        )
        return update_duty(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_DUTY,
    summary="Fetch duty",
    tags=["Duty"],
    response_model=list[DutySchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_duties_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_duties(session, QueryParams(**query_params.model_dump()))
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.patch(
    f"{URL_DUTY}/{{id}}",
    summary="Update duty",
    tags=["Duty"],
    response_model=DutySchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged in operator must have `company.service.duty.update` permission."
        )
        .to_string()
    ),
)
async def update_duty_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_SERVICE_DUTY],
        )
        return update_duty(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
            duty_filter=(Duty.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_DUTY,
    summary="Fetch duty",
    tags=["Duty"],
    response_model=list[DutySchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_duties_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        query_params = QueryParams(
            **query_params.model_dump(), company_id=token.company_id
        )
        return search_duties(session, query_params)
    except Exception as e:
        exceptions.handle(e)
