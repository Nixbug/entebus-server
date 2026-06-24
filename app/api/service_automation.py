"""
Service Automation API Router for EnteBus.

Provides endpoints for managing service automations, including creation,
update, deletion and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime, time
from enum import StrEnum
from typing import List, Tuple
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import or_
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
    SessionLocal,
    Vehicle,
)
from app.src.description import Description
from app.src.enums import FareScope, OrderIn, TicketingMode
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
    get_request_info,
    update_if_changed,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.regex import NAME_PATTERN
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

    job_id: int | None = Field(default=None)
    name: str = Field(min_length=1, max_length=128, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    route_id: int = Field()
    fare_id: int = Field()
    vehicle_id: int = Field()
    ticket_mode: TicketingMode = Field(
        description=enum_str(TicketingMode), default=TicketingMode.HYBRID
    )
    starting_at: time = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new service automation for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new service automation."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating a service automation."""

    job_id: int | None = Field(default=None)
    name: str = Field(default=None, min_length=1, max_length=128, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    route_id: int = Field(default=None)
    fare_id: int = Field(default=None)
    vehicle_id: int = Field(default=None)
    ticket_mode: TicketingMode = Field(
        default=None, description=enum_str(TicketingMode)
    )
    starting_at: time = Field(default=None)


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
## Functions
# ---------------------------------------------------------------------------
def create_service_automation(
    session: Session,
    form_param: CreateForm,
    extra_filter_for_job=None,
    extra_filter_for_route=None,
    extra_filter_for_fare=None,
    extra_filter_for_vehicle=None,
) -> dict:
    """
    Creates a new service automation record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a service automation.
        extra_filter_for_job: Optional filter for validating the job.
        extra_filter_for_route: Optional filter for validating the route.
        extra_filter_for_fare: Optional filter for validating the fare.
        extra_filter_for_vehicle: Optional filter for validating the vehicle.

    Returns:
        dict: The created service automation data.
    """
    job = None
    if form_param.job_id is not None:
        job = validate_id(
            session,
            Job,
            form_param.job_id,
            ServiceAutomation.job_id,
            extra_filter=extra_filter_for_job,
        )
    vehicle = validate_id(
        session,
        Vehicle,
        form_param.vehicle_id,
        ServiceAutomation.vehicle_id,
        extra_filter=extra_filter_for_vehicle,
    )
    route = validate_id(
        session,
        Route,
        form_param.route_id,
        ServiceAutomation.route_id,
        extra_filter=extra_filter_for_route,
    )
    fare = validate_id(
        session,
        Fare,
        form_param.fare_id,
        ServiceAutomation.fare_id,
        extra_filter=extra_filter_for_fare,
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
    return service_automation_data


def update_service_automation(
    session: Session,
    id: int,
    form_param: UpdateForm,
    extra_filter_for_service_automation=None,
    extra_filter_for_job=None,
    extra_filter_for_route=None,
    extra_filter_for_fare=None,
    extra_filter_for_vehicle=None,
) -> Tuple[bool, dict]:
    """
    Updates an existing service automation record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the service automation to update.
        form_param (UpdateForm): Form data for updating the service automation.
        extra_filter_for_service_automation: Optional filter for validating the service automation.
        extra_filter_for_job: Optional filter for validating the job.
        extra_filter_for_route: Optional filter for validating the route.
        extra_filter_for_fare: Optional filter for validating the fare.
        extra_filter_for_vehicle: Optional filter for validating the vehicle.

    Returns:
        Tuple[bool, dict]: A tuple containing a boolean indicating whether any updates were made and the updated service automation data.
    """
    service_automation = validate_id(
        session,
        ServiceAutomation,
        id,
        ServiceAutomation.id,
        extra_filter=extra_filter_for_service_automation,
    )

    update_data = form_param.model_dump(exclude_unset=True)
    if "job_id" in update_data and update_data["job_id"] is not None:
        validate_id(
            session,
            Job,
            form_param.job_id,
            ServiceAutomation.job_id,
            extra_filter=extra_filter_for_job,
        )
    if "route_id" in update_data:
        validate_id(
            session,
            Route,
            form_param.route_id,
            ServiceAutomation.route_id,
            extra_filter=extra_filter_for_route,
        )
    if "fare_id" in update_data:
        validate_id(
            session,
            Fare,
            form_param.fare_id,
            ServiceAutomation.fare_id,
            extra_filter=extra_filter_for_fare,
        )
    if "vehicle_id" in update_data:
        validate_id(
            session,
            Vehicle,
            form_param.vehicle_id,
            ServiceAutomation.vehicle_id,
            extra_filter=extra_filter_for_vehicle,
        )

    update_if_changed(service_automation, update_data)
    have_updates = session.is_modified(service_automation)
    if have_updates:
        session.commit()
        session.refresh(service_automation)

    service_automation_data = jsonable_encoder(service_automation)
    return have_updates, service_automation_data


def delete_service_automation(
    session: Session, service_automation: ServiceAutomation
) -> dict:
    """
    Deletes a service automation from the database.

    Args:
        session (Session): SQLAlchemy database session.
        service_automation (ServiceAutomation): Service automation to delete.

    Returns:
        dict: JSON-encoded representation of the deleted service automation.
    """
    service_automation_data = jsonable_encoder(service_automation)
    session.delete(service_automation)
    session.commit()
    return service_automation_data


def search_service_automation(
    session: Session, query_params: QueryParams
) -> List[ServiceAutomation]:
    """
    Searches for service automations based on the provided query parameters.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters for filtering and sorting service automations.

    Returns:
        List[ServiceAutomation]: A list of service automations matching the search criteria.
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
                ServiceAutomation.name.ilike(search),
                ServiceAutomation.description.ilike(search),
            )
        )

    # General filters
    query = apply_id_filters(query, ServiceAutomation, query_params)
    query = apply_name_filters(query, ServiceAutomation, query_params)
    query = apply_created_on_filters(query, ServiceAutomation, query_params)
    query = apply_updated_on_filters(query, ServiceAutomation, query_params)

    # Ordering and Pagination
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
    exceptions.UnknownValue(ServiceAutomation.job_id),
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
## Common descriptions
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
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged in executive must have `company.service.create` permission.")
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
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_SERVICE],
        )

        service_automation_data = create_service_automation(
            session,
            CreateForm(**form_param.model_dump()),
            extra_filter_for_job=(Job.company_id == form_param.company_id),
            extra_filter_for_route=(Route.company_id == form_param.company_id),
            extra_filter_for_fare=(Fare.company_id == form_param.company_id)
            | (Fare.scope == FareScope.GLOBAL),
            extra_filter_for_vehicle=(Vehicle.company_id == form_param.company_id),
        )
        log_event(token, request_info, service_automation_data)
        return service_automation_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_SERVICE_AUTOMATION}/{{id}}",
    summary="Update service automation",
    tags=["Service Automation"],
    response_model=ServiceAutomationSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged in executive must have `company.service.update` permission.")
        .to_string()
    ),
)
async def update_service_automation_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_SERVICE],
        )

        service_automation = validate_id(
            session,
            ServiceAutomation,
            id,
            ServiceAutomation.id,
        )
        have_updates, service_automation_data = update_service_automation(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            extra_filter_for_service_automation=(
                ServiceAutomation.company_id == service_automation.company_id
            ),
            extra_filter_for_job=(Job.company_id == service_automation.company_id),
            extra_filter_for_route=(Route.company_id == service_automation.company_id),
            extra_filter_for_fare=(Fare.company_id == service_automation.company_id)
            | (Fare.scope == FareScope.GLOBAL),
            extra_filter_for_vehicle=(
                Vehicle.company_id == service_automation.company_id
            ),
        )
        if have_updates:
            log_event(token, request_info, service_automation_data)
        return service_automation_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_SERVICE_AUTOMATION}/{{id}}",
    summary="Delete service automation",
    tags=["Service Automation"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged in executive must have `company.service.delete` permission.")
        .to_string()
    ),
)
async def delete_service_automation_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_SERVICE],
        )

        service_automation = (
            session.query(ServiceAutomation).filter(ServiceAutomation.id == id).first()
        )
        if service_automation is not None:
            service_automation_data = delete_service_automation(
                session, service_automation
            )
            log_event(token, request_info, service_automation_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_SERVICE_AUTOMATION,
    summary="Fetch service automation",
    tags=["Service Automation"],
    response_model=List[ServiceAutomationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_automations_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_service_automation(
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
    URL_SERVICE_AUTOMATION,
    summary="Create service automation",
    tags=["Service Automation"],
    response_model=ServiceAutomationSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged in operator must have `company.service.create` permission.")
        .to_string()
    ),
)
async def create_service_automation_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_SERVICE],
        )

        service_automation_data = create_service_automation(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            extra_filter_for_job=(Job.company_id == token.company_id),
            extra_filter_for_route=(Route.company_id == token.company_id),
            extra_filter_for_fare=(Fare.company_id == token.company_id)
            | (Fare.scope == FareScope.GLOBAL),
            extra_filter_for_vehicle=(Vehicle.company_id == token.company_id),
        )
        log_event(token, request_info, service_automation_data)
        return service_automation_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_SERVICE_AUTOMATION}/{{id}}",
    summary="Update service automation",
    tags=["Service Automation"],
    response_model=ServiceAutomationSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged in operator must have `company.service.update` permission.")
        .to_string()
    ),
)
async def update_service_automation_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_SERVICE],
        )

        have_updates, service_automation_data = update_service_automation(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            extra_filter_for_service_automation=(
                ServiceAutomation.company_id == token.company_id
            ),
            extra_filter_for_job=(Job.company_id == token.company_id),
            extra_filter_for_route=(Route.company_id == token.company_id),
            extra_filter_for_fare=(Fare.company_id == token.company_id)
            | (Fare.scope == FareScope.GLOBAL),
            extra_filter_for_vehicle=(Vehicle.company_id == token.company_id),
        )
        if have_updates:
            log_event(token, request_info, service_automation_data)
        return service_automation_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_SERVICE_AUTOMATION}/{{id}}",
    summary="Delete service automation",
    tags=["Service Automation"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged in operator must have `company.service.delete` permission.")
        .to_string()
    ),
)
async def delete_service_automation_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_SERVICE],
        )

        service_automation = (
            session.query(ServiceAutomation)
            .filter(
                ServiceAutomation.id == id,
                ServiceAutomation.company_id == token.company_id,
            )
            .first()
        )
        if service_automation is not None:
            service_automation_data = delete_service_automation(
                session, service_automation
            )
            log_event(token, request_info, service_automation_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_SERVICE_AUTOMATION,
    summary="Fetch service automation",
    tags=["Service Automation"],
    response_model=List[ServiceAutomationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_service_automations_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_service_automation(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
