"""
Service Automation API Router.

Provides endpoints for managing service automations:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator)
"""

from datetime import datetime, time
from enum import StrEnum
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import String, or_
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions
from app.src.db import (
    Company,
    ExecutiveToken,
    Fare,
    Job,
    OperatorToken,
    Route,
    ServiceAutomation,
    Vehicle,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import AppID, FareScope, OrderIn, TicketingMode
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    NameFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_name_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    update_if_changed,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.regex import NAME_PATTERN
from app.src.schemas import PatchForm
from app.src.urls import URL_SERVICE_AUTOMATION
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    verify_token,
)

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class ServiceAutomationSchema(BaseModel):
    """Schema for service automation response."""

    id: int
    company_id: int
    job_id: int | None
    name: str
    description: str | None
    route_id: int
    fare_id: int
    vehicle_id: int
    ticket_mode: int
    starting_at: time
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new service automation for an operator."""

    job_id: int = Field()
    name: str = Field(min_length=1, max_length=128, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    route_id: int = Field()
    fare_id: int = Field()
    vehicle_id: int = Field()
    ticket_mode: TicketingMode = Field(
        default=TicketingMode.HYBRID, description=enum_str(TicketingMode)
    )
    starting_at: time = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new service automation for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new service automation."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a service automation."""

    name: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=NAME_PATTERN
    )
    description: Annotated[str | None, "nullable"] = Field(
        default=None, max_length=1024
    )
    route_id: int | None = Field(default=None)
    fare_id: int | None = Field(default=None)
    vehicle_id: int | None = Field(default=None)
    ticket_mode: TicketingMode | None = Field(
        default=None, description=enum_str(TicketingMode)
    )
    starting_at: time | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering service automation results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    STARTING_AT = "starting_at"


class QueryParamsForOP(
    IDFilter, CreatedOnFilter, UpdatedOnFilter, NameFilter, PaginationFilter
):
    """Query parameters for operators."""

    search: str | None = Field(Query(default=None))
    description: str | None = Field(Query(default=None))
    job_id: int | None = Field(Query(default=None))
    route_id: int | None = Field(Query(default=None))
    fare_id: int | None = Field(Query(default=None))
    vehicle_id: int | None = Field(Query(default=None))
    ticket_mode: TicketingMode | None = Field(
        Query(default=None, description=enum_str(TicketingMode))
    )
    starting_at_ge: time | None = Field(Query(default=None))
    starting_at_le: time | None = Field(Query(default=None))
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


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_service_automation(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | OperatorToken,
    request_info,
    job_filter=None,
    route_filter=None,
    fare_filter=None,
    vehicle_filter=None,
) -> dict:
    """
    Creates a new service automation record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a service automation.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info: Request information for logging.
        job_filter: Additional filter for validating the job.
        route_filter: Additional filter for validating the route.
        fare_filter: Additional filter for validating the fare.
        vehicle_filter: Additional filter for validating the vehicle.

    Returns:
        dict: The created service automation data.
    """
    validate_id(
        session,
        Job,
        form_param.job_id,
        ServiceAutomation.job_id,
        extra_filter=job_filter,
    )
    validate_id(
        session,
        Route,
        form_param.route_id,
        ServiceAutomation.route_id,
        extra_filter=route_filter,
    )
    validate_id(
        session,
        Fare,
        form_param.fare_id,
        ServiceAutomation.fare_id,
        extra_filter=fare_filter,
    )
    validate_id(
        session,
        Vehicle,
        form_param.vehicle_id,
        ServiceAutomation.vehicle_id,
        extra_filter=vehicle_filter,
    )

    service_automation = ServiceAutomation(
        company_id=form_param.company_id,
        job_id=form_param.job_id,
        name=form_param.name,
        description=form_param.description,
        route_id=form_param.route_id,
        fare_id=form_param.fare_id,
        vehicle_id=form_param.vehicle_id,
        ticket_mode=form_param.ticket_mode,
        starting_at=form_param.starting_at,
    )
    session.add(service_automation)
    session.commit()
    session.refresh(service_automation)

    service_automation_data = jsonable_encoder(service_automation)
    log_event(token, request_info, service_automation_data)
    return service_automation_data


def update_service_automation(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken | OperatorToken,
    request_info,
    service_automation_filter=None,
    route_filter=None,
    fare_filter=None,
    vehicle_filter=None,
) -> dict:
    """
    Updates an existing service automation record.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the service automation to update.
        form_param (UpdateForm): Form data for updating the service automation.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info: Request information for logging.
        service_automation_filter: Additional filter for service automation validation.
        route_filter: Additional filter for validating the route.
        fare_filter: Additional filter for validating the fare.
        vehicle_filter: Additional filter for validating the vehicle.

    Returns:
        dict: JSON-encoded representation of the updated service automation.
    """
    service_automation = validate_id(
        session,
        ServiceAutomation,
        id,
        ServiceAutomation.id,
        extra_filter=service_automation_filter,
    )

    if request_info.app_id == AppID.EXECUTIVE:
        route_filter = Route.company_id == service_automation.company_id
        vehicle_filter = Vehicle.company_id == service_automation.company_id
        fare_filter = (Fare.company_id == service_automation.company_id) | (
            Fare.scope == FareScope.GLOBAL
        )

    update_data = form_param.model_dump(exclude_unset=True)
    if "route_id" in update_data:
        if update_data["route_id"] != service_automation.route_id:
            validate_id(
                session,
                Route,
                update_data["route_id"],
                ServiceAutomation.route_id,
                extra_filter=route_filter,
            )
    if "fare_id" in update_data:
        if update_data["fare_id"] != service_automation.fare_id:
            validate_id(
                session,
                Fare,
                update_data["fare_id"],
                ServiceAutomation.fare_id,
                extra_filter=fare_filter,
            )
    if "vehicle_id" in update_data:
        if update_data["vehicle_id"] != service_automation.vehicle_id:
            validate_id(
                session,
                Vehicle,
                update_data["vehicle_id"],
                ServiceAutomation.vehicle_id,
                extra_filter=vehicle_filter,
            )

    update_if_changed(service_automation, update_data)
    if session.is_modified(service_automation):
        session.commit()
        session.refresh(service_automation)
        service_automation_data = jsonable_encoder(service_automation)
        log_event(token, request_info, service_automation_data)
    else:
        service_automation_data = jsonable_encoder(service_automation)
    return service_automation_data


def delete_service_automation(
    session: Session,
    id: int,
    token: ExecutiveToken | OperatorToken,
    request_info,
    service_automation_filter=None,
) -> None:
    """
    Deletes a service automation from the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the service automation to delete.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info: Request information for logging.
        service_automation_filter: Additional filter for service automation validation.
    """
    service_automation = get_by_id(
        session,
        ServiceAutomation,
        id,
        extra_filter=service_automation_filter,
    )
    if service_automation is None:
        return

    service_automation_data = jsonable_encoder(service_automation)
    session.delete(service_automation)
    session.commit()
    log_event(token, request_info, service_automation_data)


def search_service_automations(
    session: Session, query_params: QueryParams
) -> list[ServiceAutomation]:
    """
    Searches for service automations based on the provided query parameters.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters for filtering and sorting results.

    Returns:
        list[ServiceAutomation]: A list of matching service automations.
    """
    query = session.query(ServiceAutomation)
    if query_params.company_id is not None:
        query = query.filter(ServiceAutomation.company_id == query_params.company_id)
    if query_params.description is not None:
        query = query.filter(
            ServiceAutomation.description.ilike(f"%{query_params.description}%")
        )
    if query_params.job_id is not None:
        query = query.filter(ServiceAutomation.job_id == query_params.job_id)
    if query_params.route_id is not None:
        query = query.filter(ServiceAutomation.route_id == query_params.route_id)
    if query_params.fare_id is not None:
        query = query.filter(ServiceAutomation.fare_id == query_params.fare_id)
    if query_params.vehicle_id is not None:
        query = query.filter(ServiceAutomation.vehicle_id == query_params.vehicle_id)
    if query_params.ticket_mode is not None:
        query = query.filter(ServiceAutomation.ticket_mode == query_params.ticket_mode)
    if query_params.starting_at_ge is not None:
        query = query.filter(
            ServiceAutomation.starting_at >= query_params.starting_at_ge
        )
    if query_params.starting_at_le is not None:
        query = query.filter(
            ServiceAutomation.starting_at <= query_params.starting_at_le
        )

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                ServiceAutomation.id.cast(String).ilike(search),
                ServiceAutomation.name.ilike(search),
                ServiceAutomation.description.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, ServiceAutomation, query_params)
    query = apply_name_filters(query, ServiceAutomation, query_params)
    query = apply_created_on_filters(query, ServiceAutomation, query_params)
    query = apply_updated_on_filters(query, ServiceAutomation, query_params)

    # Ordering and pagination
    ordering_attr = getattr(ServiceAutomation, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    service_automations = query.all()
    return service_automations


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ServiceAutomation.job_id),
    exceptions.UnknownValue(ServiceAutomation.route_id),
    exceptions.UnknownValue(ServiceAutomation.fare_id),
    exceptions.UnknownValue(ServiceAutomation.vehicle_id),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ServiceAutomation.id),
    exceptions.UnknownValue(ServiceAutomation.route_id),
    exceptions.UnknownValue(ServiceAutomation.fare_id),
    exceptions.UnknownValue(ServiceAutomation.vehicle_id),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new service automation.")
    .add_line("This record stores the template used for service creation.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing service automation.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing service automation.")
    .add_line(
        "Returns 204 No Content even if the specified service automation does not exist."
    )
)

GET_DESCRIPTION = Description().add_head("Fetches a list of service automations.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_SERVICE_AUTOMATION,
    summary="Create service automation",
    tags=["Service Automation"],
    response_model=ServiceAutomationSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [*POST_EXCEPTIONS, exceptions.UnknownValue(ServiceAutomation.company_id)]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.service.create` permission.")
        .add_line(
            "`company_id` is required and used to validate job, route, fare, and vehicle ownership."
        )
        .to_string()
    ),
)
async def create_service_automation_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_SERVICE],
        )
        validate_id(
            session, Company, form_param.company_id, ServiceAutomation.company_id
        )
        return create_service_automation(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
            job_filter=(Job.company_id == form_param.company_id),
            route_filter=(Route.company_id == form_param.company_id),
            fare_filter=(Fare.company_id == form_param.company_id)
            | (Fare.scope == FareScope.GLOBAL),
            vehicle_filter=(Vehicle.company_id == form_param.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_SERVICE_AUTOMATION}/{{id}}",
    summary="Update service automation",
    tags=["Service Automation"],
    response_model=ServiceAutomationSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.service.update` permission.")
        .to_string()
    ),
)
async def update_service_automation_for_executive(
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
            [ExecutivePermissionPath.UPDATE_COMPANY_SERVICE],
        )
        return update_service_automation(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_SERVICE_AUTOMATION}/{{id}}",
    summary="Delete service automation",
    tags=["Service Automation"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.service.delete` permission.")
        .to_string()
    ),
)
async def delete_service_automation_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_SERVICE],
        )
        delete_service_automation(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_SERVICE_AUTOMATION,
    summary="Fetch service automation",
    tags=["Service Automation"],
    response_model=list[ServiceAutomationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_automations_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_service_automations(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_SERVICE_AUTOMATION,
    summary="Create service automation",
    tags=["Service Automation"],
    response_model=ServiceAutomationSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.service.create` permission.")
        .to_string()
    ),
)
async def create_service_automation_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_SERVICE],
        )
        return create_service_automation(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
            job_filter=(Job.company_id == token.company_id),
            route_filter=(Route.company_id == token.company_id),
            fare_filter=(Fare.company_id == token.company_id)
            | (Fare.scope == FareScope.GLOBAL),
            vehicle_filter=(Vehicle.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_SERVICE_AUTOMATION}/{{id}}",
    summary="Update service automation",
    tags=["Service Automation"],
    response_model=ServiceAutomationSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.service.update` permission.")
        .to_string()
    ),
)
async def update_service_automation_for_operator(
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
            [OperatorPermissionPath.UPDATE_COMPANY_SERVICE],
        )
        return update_service_automation(
            session,
            id,
            form_param,
            token,
            request_info,
            service_automation_filter=(
                ServiceAutomation.company_id == token.company_id
            ),
            route_filter=(Route.company_id == token.company_id),
            fare_filter=(Fare.company_id == token.company_id)
            | (Fare.scope == FareScope.GLOBAL),
            vehicle_filter=(Vehicle.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_SERVICE_AUTOMATION}/{{id}}",
    summary="Delete service automation",
    tags=["Service Automation"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.service.delete` permission.")
        .to_string()
    ),
)
async def delete_service_automation_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_SERVICE],
        )
        delete_service_automation(
            session,
            id,
            token,
            request_info,
            service_automation_filter=(
                ServiceAutomation.company_id == token.company_id
            ),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_SERVICE_AUTOMATION,
    summary="Fetch service automation",
    tags=["Service Automation"],
    response_model=list[ServiceAutomationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_automations_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_service_automations(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
