"""
Service Assignment API Router.

Provides endpoints for managing service assignments:
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
    ExecutiveToken,
    Operator,
    OperatorToken,
    Service,
    ServiceAssignment,
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
from app.src.urls import URL_SERVICE_ASSIGNMENT
from app.src.validators import (
    validate_id,
    authorize_executive,
    authorize_operator,
    verify_token,
)

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class ServiceAssignmentSchema(BaseModel):
    """Schema for service assignment response."""

    id: int
    company_id: int
    service_id: int
    operator_id: int
    created_on: datetime
    updated_on: datetime | None


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a service assignment for an operator."""

    service_id: int = Field()
    operator_id: int = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a service assignment for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a service assignment."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a service assignment."""

    operator_id: int | None = Field(default=None)


class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    STARTING_AT = "starting_at"


class QueryParamsForOP(PaginationFilter, IDFilter, CreatedOnFilter, UpdatedOnFilter):
    """Query parameters for operators."""

    service_id: int | None = Field(Query(default=None))
    service_id_excluding: list[int] | None = Field(Query(default=None))
    operator_id: int | None = Field(Query(default=None))
    starting_at_ge: datetime | None = Field(Query(default=None))
    starting_at_le: datetime | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executive assignment listing."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_service_assignment(
    session: Session,
    form_param: CreateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    service_filter=None,
    operator_filter=None,
) -> dict:
    """
    Creates a new service assignment record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a service assignment.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        service_filter: Optional filter for validating the service.
        operator_filter: Optional filter for validating the operator.

    Returns:
        dict: The created service assignment data.
    """
    service = validate_id(
        session,
        Service,
        form_param.service_id,
        ServiceAssignment.service_id,
        extra_filter=service_filter,
    )
    operator = validate_id(
        session,
        Operator,
        form_param.operator_id,
        ServiceAssignment.operator_id,
        extra_filter=operator_filter,
    )

    service_assignment = ServiceAssignment(
        company_id=service.company_id,
        service_id=service.id,
        operator_id=operator.id,
    )
    session.add(service_assignment)
    session.commit()
    session.refresh(service_assignment)

    service_assignment_data = jsonable_encoder(service_assignment)
    log_event(token, request_info, service_assignment_data)
    return service_assignment_data


def update_service_assignment(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    service_assignment_filter=None,
    operator_filter=None,
) -> dict:
    """
    Updates a service assignment with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): The ID of the ServiceAssignment to update.
        form_param (UpdateForm): The form data for updating the service assignment.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        service_assignment_filter: Optional filter for validating the service assignment.
        operator_filter: Optional filter for validating the new operator.

    Returns:
        dict: JSON-encoded representation of the updated service assignment.
    """
    service_assignment = validate_id(
        session,
        ServiceAssignment,
        id,
        ServiceAssignment.id,
        extra_filter=service_assignment_filter,
    )

    if request_info.app_id == AppID.EXECUTIVE:
        operator_filter = Operator.company_id == service_assignment.company_id

    update_data = form_param.model_dump(exclude_unset=True)
    if "operator_id" in update_data:
        if update_data["operator_id"] != service_assignment.operator_id:
            validate_id(
                session,
                Operator,
                update_data["operator_id"],
                ServiceAssignment.operator_id,
                extra_filter=operator_filter,
            )
            service_assignment.operator_id = update_data["operator_id"]
        update_data.pop("operator_id")

    if session.is_modified(service_assignment):
        session.commit()
        session.refresh(service_assignment)
        service_assignment_data = jsonable_encoder(service_assignment)
        log_event(token, request_info, service_assignment_data)
    else:
        service_assignment_data = jsonable_encoder(service_assignment)
    return service_assignment_data


def delete_service_assignment(
    session: Session,
    id: int,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    service_assignment_filter=None,
) -> None:
    """
    Deletes a service assignment from the database.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): The ID of the ServiceAssignment to delete.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        service_assignment_filter: Optional filter for validating the service assignment.
    """
    service_assignment = get_by_id(
        session, ServiceAssignment, id, extra_filter=service_assignment_filter
    )
    if service_assignment is None:
        return

    service_assignment_data = jsonable_encoder(service_assignment)
    session.delete(service_assignment)
    session.commit()
    log_event(token, request_info, service_assignment_data)


def search_service_assignments(
    session: Session, query_params: QueryParams
) -> list[ServiceAssignment]:
    """
    Search for ServiceAssignments based on provided query parameters.

    This function supports filtering, ordering, and pagination
    to retrieve service assignments that match the provided criteria,
    including service starting time filters and ordering.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[ServiceAssignment]: List of ServiceAssignments that match the search criteria.
    """
    query = session.query(ServiceAssignment)
    need_service_join = (
        query_params.starting_at_ge is not None
        or query_params.starting_at_le is not None
        or query_params.order_by == OrderBy.STARTING_AT
    )
    if need_service_join:
        query = query.join(Service, Service.id == ServiceAssignment.service_id)
    if query_params.company_id is not None:
        query = query.filter(ServiceAssignment.company_id == query_params.company_id)
    if query_params.service_id is not None:
        query = query.filter(ServiceAssignment.service_id == query_params.service_id)
    if query_params.service_id_excluding:
        query = query.filter(
            ServiceAssignment.service_id.notin_(query_params.service_id_excluding)
        )
    if query_params.operator_id is not None:
        query = query.filter(ServiceAssignment.operator_id == query_params.operator_id)
    if query_params.starting_at_ge is not None:
        query = query.filter(Service.starting_at >= query_params.starting_at_ge)
    if query_params.starting_at_le is not None:
        query = query.filter(Service.starting_at <= query_params.starting_at_le)

    # Generalized filters
    query = apply_id_filters(query, ServiceAssignment, query_params)
    query = apply_created_on_filters(query, ServiceAssignment, query_params)
    query = apply_updated_on_filters(query, ServiceAssignment, query_params)

    # Ordering and pagination
    ordering_attr = (
        Service.starting_at
        if query_params.order_by == OrderBy.STARTING_AT
        else getattr(ServiceAssignment, query_params.order_by.value)
    )
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    service_assignments = query.all()
    return service_assignments


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ServiceAssignment.service_id),
    exceptions.UnknownValue(ServiceAssignment.operator_id),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(ServiceAssignment.id),
    exceptions.UnknownValue(ServiceAssignment.operator_id),
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
    .add_head("Creates a new service assignment.")
    .add_line("Duplicate assignments are not allowed.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing service assignment.")
    .add_line("Duplicate assignments are not allowed.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing service assignment.")
    .add_line(
        "Returns 204 No Content even if the specified service assignment does not exist."
    )
)

GET_DESCRIPTION = Description().add_head("Fetches a list of service assignments.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_SERVICE_ASSIGNMENT,
    summary="Create service assignment",
    tags=["Service Assignment"],
    response_model=ServiceAssignmentSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in executive must have `company.service.assignment.create` permission."
        )
        .add_line(
            "`company_id` is required and used to validate service and operator ownership."
        )
        .to_string()
    ),
)
async def create_service_assignment_for_executive(
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
        return create_service_assignment(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
            service_filter=(Service.company_id == form_param.company_id),
            operator_filter=(Operator.company_id == form_param.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    summary="Update service assignment",
    tags=["Service Assignment"],
    response_model=ServiceAssignmentSchema,
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
async def update_service_assignment_for_executive(
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
        return update_service_assignment(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    summary="Delete service assignment",
    tags=["Service Assignment"],
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
async def delete_service_assignment_for_executive(
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
        delete_service_assignment(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_SERVICE_ASSIGNMENT,
    summary="Fetch service assignment",
    tags=["Service Assignment"],
    response_model=list[ServiceAssignmentSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "Executives can filter service assignments by company, service, operator, and service starting time."
        )
        .to_string()
    ),
)
async def fetch_service_assignments_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_service_assignments(
            session, QueryParams(**query_params.model_dump())
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_SERVICE_ASSIGNMENT,
    summary="Create service assignment",
    tags=["Service Assignment"],
    response_model=ServiceAssignmentSchema,
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
async def create_service_assignment_for_operator(
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
        return create_service_assignment(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
            service_filter=(Service.company_id == token.company_id),
            operator_filter=(Operator.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    summary="Update service assignment",
    tags=["Service Assignment"],
    response_model=ServiceAssignmentSchema,
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
async def update_service_assignment_for_operator(
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
        return update_service_assignment(
            session,
            id,
            form_param,
            token,
            request_info,
            service_assignment_filter=(
                ServiceAssignment.company_id == token.company_id
            ),
            operator_filter=(Operator.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    summary="Delete service assignment",
    tags=["Service Assignment"],
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
async def delete_service_assignment_for_operator(
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
        delete_service_assignment(
            session,
            id,
            token,
            request_info,
            service_assignment_filter=(
                ServiceAssignment.company_id == token.company_id
            ),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_SERVICE_ASSIGNMENT,
    summary="Fetch service assignment",
    tags=["Service Assignment"],
    response_model=list[ServiceAssignmentSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "Operators can filter service assignments by service, operator, and service starting time."
        )
        .to_string()
    ),
)
async def fetch_service_assignments_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_service_assignments(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
