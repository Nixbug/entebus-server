"""
Service Assignment API Router for EnteBus.

Provides endpoints for managing assignments between services and operators,
including creation, update, deletion, and retrieval.
"""

from datetime import datetime
from enum import StrEnum

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions
from app.src.db import (
    ExecutiveToken,
    Operator,
    OperatorToken,
    Service,
    ServiceAssignment,
    SessionLocal,
)
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, UpdatedOnFilter
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
)
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.urls import URL_SERVICE_ASSIGNMENT
from app.src.validators import validate_id, verify_permission, verify_token

route_executive = APIRouter()
route_operator = APIRouter()


class AssignmentRead(BaseModel):
    """Schema for service assignment response."""

    id: int
    service_id: int
    operator_id: int
    created_on: datetime
    updated_on: datetime | None


class AssignmentCreate(BaseModel):
    """Form data for creating a service assignment."""

    service_id: int = Field()
    operator_id: int = Field()


class AssignmentUpdate(BaseModel):
    """Form data for updating a service assignment."""

    service_id: int | None = Field(default=None)
    operator_id: int | None = Field(default=None)


class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(PaginationFilter, IDFilter, CreatedOnFilter, UpdatedOnFilter):
    """Query parameters for operator assignment listing."""

    service_id: int | None = Field(Query(default=None))
    operator_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executive assignment listing."""

    company_id: int | None = Field(Query(default=None))


def validate_assignment_association(service: Service, operator: Operator) -> None:
    """Validate that the service and operator belong to the same company."""

    if service.company_id != operator.company_id:
        raise exceptions.InvalidAssociation(
            ServiceAssignment.service_id, ServiceAssignment.operator_id
        )


def search_assignments(
    session: Session, query_params: QueryParamsForEX
) -> list[ServiceAssignment]:
    """Fetch service assignments matching query parameters."""

    query = session.query(ServiceAssignment).join(
        Service, Service.id == ServiceAssignment.service_id
    )
    if query_params.company_id is not None:
        query = query.filter(Service.company_id == query_params.company_id)
    if query_params.service_id is not None:
        query = query.filter(ServiceAssignment.service_id == query_params.service_id)
    if query_params.operator_id is not None:
        query = query.filter(ServiceAssignment.operator_id == query_params.operator_id)

    query = apply_id_filters(query, ServiceAssignment, query_params)
    query = apply_created_on_filters(query, ServiceAssignment, query_params)
    query = apply_updated_on_filters(query, ServiceAssignment, query_params)

    ordering_attr = getattr(ServiceAssignment, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)
    return query.all()


@route_executive.post(
    URL_SERVICE_ASSIGNMENT,
    tags=["Service Assignment"],
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ServiceAssignment.service_id),
            exceptions.UnknownValue(ServiceAssignment.operator_id),
            exceptions.InvalidAssociation(
                ServiceAssignment.service_id, ServiceAssignment.operator_id
            ),
        ]
    ),
)
async def create_assignment_executive(
    form_param: AssignmentCreate,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(
            roles, ExecutivePermissionPath.CREATE_COMPANY_SERVICE_ASSIGNMENT
        )

        service = validate_id(
            session, Service, form_param.service_id, ServiceAssignment.service_id
        )
        operator = validate_id(
            session, Operator, form_param.operator_id, ServiceAssignment.operator_id
        )
        validate_assignment_association(service, operator)

        assignment = ServiceAssignment(
            service_id=form_param.service_id, operator_id=form_param.operator_id
        )
        session.add(assignment)
        session.commit()
        session.refresh(assignment)

        assignment_data = jsonable_encoder(assignment)
        log_event(token, request_info, assignment_data)
        return assignment_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_SERVICE_ASSIGNMENT,
    tags=["Service Assignment"],
    response_model=list[AssignmentRead],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
)
async def fetch_assignment_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)
        return search_assignments(
            session, QueryParamsForEX(**query_params.model_dump())
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    tags=["Service Assignment"],
    response_model=AssignmentRead,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ServiceAssignment.id),
            exceptions.UnknownValue(ServiceAssignment.service_id),
            exceptions.UnknownValue(ServiceAssignment.operator_id),
            exceptions.InvalidAssociation(
                ServiceAssignment.service_id, ServiceAssignment.operator_id
            ),
        ]
    ),
)
async def update_assignment_executive(
    id: int,
    form_param: AssignmentUpdate,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(
            roles, ExecutivePermissionPath.UPDATE_COMPANY_SERVICE_ASSIGNMENT
        )

        assignment = validate_id(session, ServiceAssignment, id, ServiceAssignment.id)
        service_id = (
            form_param.service_id
            if form_param.service_id is not None
            else assignment.service_id
        )
        operator_id = (
            form_param.operator_id
            if form_param.operator_id is not None
            else assignment.operator_id
        )
        service = validate_id(
            session,
            Service,
            service_id,
            ServiceAssignment.service_id,
        )
        operator = validate_id(
            session,
            Operator,
            operator_id,
            ServiceAssignment.operator_id,
        )
        validate_assignment_association(service, operator)

        if form_param.service_id is not None:
            assignment.service_id = form_param.service_id
        if form_param.operator_id is not None:
            assignment.operator_id = form_param.operator_id

        have_updates = session.is_modified(assignment)
        if have_updates:
            session.commit()
            session.refresh(assignment)

        assignment_data = jsonable_encoder(assignment)
        if have_updates:
            log_event(token, request_info, assignment_data)
        return assignment_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    tags=["Service Assignment"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
)
async def delete_assignment_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(
            roles, ExecutivePermissionPath.DELETE_COMPANY_SERVICE_ASSIGNMENT
        )

        assignment = (
            session.query(ServiceAssignment).filter(ServiceAssignment.id == id).first()
        )
        if assignment is not None:
            assignment_data = jsonable_encoder(assignment)
            session.delete(assignment)
            session.commit()
            log_event(token, request_info, assignment_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.post(
    URL_SERVICE_ASSIGNMENT,
    tags=["Service Assignment"],
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ServiceAssignment.service_id),
            exceptions.UnknownValue(ServiceAssignment.operator_id),
            exceptions.InvalidAssociation(
                ServiceAssignment.service_id, ServiceAssignment.operator_id
            ),
        ]
    ),
)
async def create_assignment_operator(
    form_param: AssignmentCreate,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(
            roles, OperatorPermissionPath.CREATE_COMPANY_SERVICE_ASSIGNMENT
        )

        service = validate_id(
            session,
            Service,
            form_param.service_id,
            ServiceAssignment.service_id,
            extra_filter=(Service.company_id == token.company_id),
        )
        operator = validate_id(
            session,
            Operator,
            form_param.operator_id,
            ServiceAssignment.operator_id,
            extra_filter=(Operator.company_id == token.company_id),
        )
        validate_assignment_association(service, operator)

        assignment = ServiceAssignment(
            service_id=form_param.service_id, operator_id=form_param.operator_id
        )
        session.add(assignment)
        session.commit()
        session.refresh(assignment)

        assignment_data = jsonable_encoder(assignment)
        log_event(token, request_info, assignment_data)
        return assignment_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_SERVICE_ASSIGNMENT,
    tags=["Service Assignment"],
    response_model=list[AssignmentRead],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
)
async def fetch_assignment_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        query_params_data = query_params.model_dump()
        query_params_data["company_id"] = token.company_id
        return search_assignments(session, QueryParamsForEX(**query_params_data))
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    tags=["Service Assignment"],
    response_model=AssignmentRead,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ServiceAssignment.id),
            exceptions.UnknownValue(ServiceAssignment.service_id),
            exceptions.UnknownValue(ServiceAssignment.operator_id),
            exceptions.InvalidAssociation(
                ServiceAssignment.service_id, ServiceAssignment.operator_id
            ),
        ]
    ),
)
async def update_assignment_operator(
    id: int,
    form_param: AssignmentUpdate,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(
            roles, OperatorPermissionPath.UPDATE_COMPANY_SERVICE_ASSIGNMENT
        )

        assignment = (
            session.query(ServiceAssignment)
            .join(Service, Service.id == ServiceAssignment.service_id)
            .filter(ServiceAssignment.id == id, Service.company_id == token.company_id)
            .first()
        )
        if assignment is None:
            raise exceptions.UnknownValue(ServiceAssignment.id)

        service_id = (
            form_param.service_id
            if form_param.service_id is not None
            else assignment.service_id
        )
        operator_id = (
            form_param.operator_id
            if form_param.operator_id is not None
            else assignment.operator_id
        )
        service = validate_id(
            session,
            Service,
            service_id,
            ServiceAssignment.service_id,
            extra_filter=(Service.company_id == token.company_id),
        )
        operator = validate_id(
            session,
            Operator,
            operator_id,
            ServiceAssignment.operator_id,
            extra_filter=(Operator.company_id == token.company_id),
        )
        validate_assignment_association(service, operator)

        if form_param.service_id is not None:
            assignment.service_id = form_param.service_id
        if form_param.operator_id is not None:
            assignment.operator_id = form_param.operator_id

        have_updates = session.is_modified(assignment)
        if have_updates:
            session.commit()
            session.refresh(assignment)

        assignment_data = jsonable_encoder(assignment)
        if have_updates:
            log_event(token, request_info, assignment_data)
        return assignment_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    tags=["Service Assignment"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
)
async def delete_assignment_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(
            roles, OperatorPermissionPath.DELETE_COMPANY_SERVICE_ASSIGNMENT
        )

        assignment = (
            session.query(ServiceAssignment)
            .join(Service, Service.id == ServiceAssignment.service_id)
            .filter(ServiceAssignment.id == id, Service.company_id == token.company_id)
            .first()
        )
        if assignment is not None:
            assignment_data = jsonable_encoder(assignment)
            session.delete(assignment)
            session.commit()
            log_event(token, request_info, assignment_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
