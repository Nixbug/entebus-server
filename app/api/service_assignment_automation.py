"""
Service Assignment Automation API Router.

Provides endpoints for managing service assignment automations:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator)
"""

from datetime import datetime
from enum import StrEnum
from typing import Union
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions, schemas
from app.src.schemas import PatchForm
from app.src.db import (
    Company,
    ExecutiveToken,
    Operator,
    OperatorToken,
    ServiceAssignmentAutomation,
    ServiceAutomation,
    get_db_session,
)
from app.src.enums import AppID, OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, UpdatedOnFilter
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
)
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.urls import URL_SERVICE_ASSIGNMENT_AUTOMATION
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
class ServiceAssignmentAutomationSchema(BaseModel):
    """Schema for service assignment automation response."""

    id: int
    company_id: int
    service_automation_id: int
    operator_id: int
    created_on: datetime
    updated_on: datetime | None


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a service assignment automation for an operator."""

    service_automation_id: int = Field()
    operator_id: int = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a service assignment automation for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a service assignment automation."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a service assignment automation."""

    operator_id: int | None = Field(default=None)


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

    service_automation_id: int | None = Field(Query(default=None))
    operator_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executive assignment automation listing."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_service_assignment_automation(
    session: Session,
    form_param: CreateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    service_automation_filter=None,
    operator_filter=None,
) -> dict:
    """
    Creates a new service assignment automation record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a service assignment automation.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        service_automation_filter: Optional filter for validating the service automation.
        operator_filter: Optional filter for validating the operator.

    Returns:
        dict: The created service assignment automation data.
    """
    service_automation = validate_id(
        session,
        ServiceAutomation,
        form_param.service_automation_id,
        ServiceAssignmentAutomation.service_automation_id,
        extra_filter=service_automation_filter,
    )
    operator = validate_id(
        session,
        Operator,
        form_param.operator_id,
        ServiceAssignmentAutomation.operator_id,
        extra_filter=operator_filter,
    )

    service_assignment_automation = ServiceAssignmentAutomation(
        company_id=service_automation.company_id,
        service_automation_id=service_automation.id,
        operator_id=operator.id,
    )
    session.add(service_assignment_automation)
    session.commit()
    session.refresh(service_assignment_automation)

    service_assignment_automation_data = jsonable_encoder(service_assignment_automation)
    log_event(token, request_info, service_assignment_automation_data)
    return service_assignment_automation_data


def update_service_assignment_automation(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    service_assignment_automation_filter=None,
    operator_filter=None,
) -> dict:
    """
    Updates a service assignment automation with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): The ID of the ServiceAssignmentAutomation to update.
        form_param (UpdateForm): The form data for updating the assignment automation.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        service_assignment_automation_filter: Optional filter for validating the assignment automation.
        operator_filter: Optional filter for validating the new operator.

    Returns:
        dict: JSON-encoded representation of the updated assignment automation.
    """
    service_assignment_automation = validate_id(
        session,
        ServiceAssignmentAutomation,
        id,
        ServiceAssignmentAutomation.id,
        extra_filter=service_assignment_automation_filter,
    )

    if request_info.app_id == AppID.EXECUTIVE:
        operator_filter = (
            Operator.company_id == service_assignment_automation.company_id
        )

    update_data = form_param.model_dump(exclude_unset=True)
    if "operator_id" in update_data:
        if update_data["operator_id"] != service_assignment_automation.operator_id:
            validate_id(
                session,
                Operator,
                update_data["operator_id"],
                ServiceAssignmentAutomation.operator_id,
                extra_filter=operator_filter,
            )
            service_assignment_automation.operator_id = update_data["operator_id"]
        update_data.pop("operator_id")

    if session.is_modified(service_assignment_automation):
        session.commit()
        session.refresh(service_assignment_automation)
        service_assignment_automation_data = jsonable_encoder(
            service_assignment_automation
        )
        log_event(token, request_info, service_assignment_automation_data)
    else:
        service_assignment_automation_data = jsonable_encoder(
            service_assignment_automation
        )
    return service_assignment_automation_data


def delete_service_assignment_automation(
    session: Session,
    id: int,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    service_assignment_automation_filter=None,
) -> None:
    """
    Deletes a service assignment automation from the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): The ID of the ServiceAssignmentAutomation to delete.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        service_assignment_automation_filter: Optional filter for validating the assignment automation.
    """
    service_assignment_automation = get_by_id(
        session,
        ServiceAssignmentAutomation,
        id,
        extra_filter=service_assignment_automation_filter,
    )
    if service_assignment_automation is None:
        return

    service_assignment_automation_data = jsonable_encoder(service_assignment_automation)
    session.delete(service_assignment_automation)
    session.commit()
    log_event(token, request_info, service_assignment_automation_data)


def search_service_assignment_automations(
    session: Session, query_params: QueryParams
) -> list[ServiceAssignmentAutomation]:
    """
    Search for ServiceAssignmentAutomation entries based on provided query parameters.

    This function supports filtering, ordering, and pagination
    to retrieve assignment automation entries that match the provided criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[ServiceAssignmentAutomation]: List of assignment automation entries that match the search criteria.
    """
    query = session.query(ServiceAssignmentAutomation)
    if query_params.company_id is not None:
        query = query.filter(
            ServiceAssignmentAutomation.company_id == query_params.company_id
        )
    if query_params.service_automation_id is not None:
        query = query.filter(
            ServiceAssignmentAutomation.service_automation_id
            == query_params.service_automation_id
        )
    if query_params.operator_id is not None:
        query = query.filter(
            ServiceAssignmentAutomation.operator_id == query_params.operator_id
        )

    # Generalized filters
    query = apply_id_filters(query, ServiceAssignmentAutomation, query_params)
    query = apply_created_on_filters(query, ServiceAssignmentAutomation, query_params)
    query = apply_updated_on_filters(query, ServiceAssignmentAutomation, query_params)

    # Ordering and pagination
    ordering_attr = getattr(ServiceAssignmentAutomation, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    service_assignment_automations = query.all()
    return service_assignment_automations


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ServiceAssignmentAutomation.service_automation_id),
    exceptions.UnknownValue(ServiceAssignmentAutomation.operator_id),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ServiceAssignmentAutomation.id),
    exceptions.UnknownValue(ServiceAssignmentAutomation.operator_id),
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
    .add_head("Creates a new service assignment automation.")
    .add_line("Duplicate assignments are not allowed per service automation.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing service assignment automation.")
    .add_line("Duplicate assignments are not allowed per service automation.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing service assignment automation.")
    .add_line(
        "Returns 204 No Content even if the specified assignment automation does not exist."
    )
)

GET_DESCRIPTION = Description().add_head(
    "Fetches a list of service assignment automations."
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_SERVICE_ASSIGNMENT_AUTOMATION,
    summary="Create service assignment automation",
    tags=["Service Assignment Automation"],
    response_model=ServiceAssignmentAutomationSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.service.assignment.create` permission."
        )
        .add_line(
            "`company_id` is required and used to validate service automation and operator ownership."
        )
        .to_string()
    ),
)
async def create_service_assignment_automation_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_SERVICE_ASSIGNMENT],
        )
        validate_id(
            session,
            Company,
            form_param.company_id,
            ServiceAssignmentAutomation.company_id,
        )
        return create_service_assignment_automation(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
            service_automation_filter=(
                ServiceAutomation.company_id == form_param.company_id
            ),
            operator_filter=(Operator.company_id == form_param.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_SERVICE_ASSIGNMENT_AUTOMATION}/{{id}}",
    summary="Update service assignment automation",
    tags=["Service Assignment Automation"],
    response_model=ServiceAssignmentAutomationSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.service.assignment.update` permission."
        )
        .to_string()
    ),
)
async def update_service_assignment_automation_for_executive(
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
            [ExecutivePermissionPath.UPDATE_COMPANY_SERVICE_ASSIGNMENT],
        )
        return update_service_assignment_automation(
            session, id, form_param, token, request_info
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_SERVICE_ASSIGNMENT_AUTOMATION}/{{id}}",
    summary="Delete service assignment automation",
    tags=["Service Assignment Automation"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.service.assignment.delete` permission."
        )
        .to_string()
    ),
)
async def delete_service_assignment_automation_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_SERVICE_ASSIGNMENT],
        )
        delete_service_assignment_automation(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_SERVICE_ASSIGNMENT_AUTOMATION,
    summary="Fetch service assignment automation",
    tags=["Service Assignment Automation"],
    response_model=list[ServiceAssignmentAutomationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "Executives can filter assignment automations by company, service automation, and operator."
        )
        .to_string()
    ),
)
async def fetch_service_assignment_automations_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_service_assignment_automations(
            session, QueryParams(**query_params.model_dump())
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_SERVICE_ASSIGNMENT_AUTOMATION,
    summary="Create service assignment automation",
    tags=["Service Assignment Automation"],
    response_model=ServiceAssignmentAutomationSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.service.assignment.create` permission."
        )
        .to_string()
    ),
)
async def create_service_assignment_automation_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_SERVICE_ASSIGNMENT],
        )
        return create_service_assignment_automation(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
            service_automation_filter=(
                ServiceAutomation.company_id == token.company_id
            ),
            operator_filter=(Operator.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_SERVICE_ASSIGNMENT_AUTOMATION}/{{id}}",
    summary="Update service assignment automation",
    tags=["Service Assignment Automation"],
    response_model=ServiceAssignmentAutomationSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.service.assignment.update` permission."
        )
        .to_string()
    ),
)
async def update_service_assignment_automation_for_operator(
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
            [OperatorPermissionPath.UPDATE_COMPANY_SERVICE_ASSIGNMENT],
        )
        return update_service_assignment_automation(
            session,
            id,
            form_param,
            token,
            request_info,
            service_assignment_automation_filter=(
                ServiceAssignmentAutomation.company_id == token.company_id
            ),
            operator_filter=(Operator.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_SERVICE_ASSIGNMENT_AUTOMATION}/{{id}}",
    summary="Delete service assignment automation",
    tags=["Service Assignment Automation"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.service.assignment.delete` permission."
        )
        .to_string()
    ),
)
async def delete_service_assignment_automation_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_SERVICE_ASSIGNMENT],
        )
        delete_service_assignment_automation(
            session,
            id,
            token,
            request_info,
            service_assignment_automation_filter=(
                ServiceAssignmentAutomation.company_id == token.company_id
            ),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_SERVICE_ASSIGNMENT_AUTOMATION,
    summary="Fetch service assignment automation",
    tags=["Service Assignment Automation"],
    response_model=list[ServiceAssignmentAutomationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "Operators can filter assignment automations by service automation and operator."
        )
        .to_string()
    ),
)
async def fetch_service_assignment_automations_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_service_assignment_automations(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
