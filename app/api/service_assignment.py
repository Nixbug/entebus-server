"""
Service Assignment API Router for EnteBus.

Provides endpoints for managing service assignments, including creation,
update, deletion and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from typing import List
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
    Company,
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


class ServiceAssignmentSchema(BaseModel):
    """Schema for service assignment response."""

    id: int
    company_id: int
    service_id: int
    operator_id: int
    created_on: datetime
    updated_on: datetime | None


class CreateFormForOP(BaseModel):
    """Form data for creating a service assignment for an operator."""

    service_id: int = Field()
    operator_id: int = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new service assignment for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new service assignment."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating a service assignment."""

    operator_id: int = Field(default=None)


class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(PaginationFilter, IDFilter, CreatedOnFilter, UpdatedOnFilter):
    """Query parameters for operators."""

    service_id: int | None = Field(Query(default=None))
    operator_id: int | None = Field(Query(default=None))
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


# Functions
def create_service_assignment(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new service assignment record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a service assignment.

    Returns:
        dict: The created service assignment data.
    """
    service_assignment = ServiceAssignment(
        company_id=form_param.company_id,
        service_id=form_param.service_id,
        operator_id=form_param.operator_id,
    )
    session.add(service_assignment)
    session.commit()
    session.refresh(service_assignment)
    assignment_data = jsonable_encoder(service_assignment)
    return assignment_data


def delete_service_assignment(
    session: Session, service_assignment: ServiceAssignment
) -> dict:
    """
    Deletes a service assignment from the database.

    Args:
        session (Session): SQLAlchemy database session.
        service_assignment (ServiceAssignment): Service assignment to delete.

    Returns:
        dict: JSON-encoded representation of the deleted service assignment.
    """
    service_assignment_data = jsonable_encoder(service_assignment)
    session.delete(service_assignment)
    session.commit()
    return service_assignment_data


def search_service_assignments(
    session: Session, query_params: QueryParams
) -> List[ServiceAssignment]:
    """
    Search for ServiceAssignments based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve service assignments that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[ServiceAssignment]: List of ServiceAssignments that match the search criteria.
    """
    query = session.query(ServiceAssignment)
    if query_params.company_id is not None:
        query = query.filter(ServiceAssignment.company_id == query_params.company_id)
    if query_params.service_id is not None:
        query = query.filter(ServiceAssignment.service_id == query_params.service_id)
    if query_params.operator_id is not None:
        query = query.filter(ServiceAssignment.operator_id == query_params.operator_id)

    # Generalized filters
    query = apply_id_filters(query, ServiceAssignment, query_params)
    query = apply_created_on_filters(query, ServiceAssignment, query_params)
    query = apply_updated_on_filters(query, ServiceAssignment, query_params)

    # Ordering and pagination
    ordering_attr = getattr(ServiceAssignment, query_params.order_by.value)
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
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_SERVICE_ASSIGNMENT,
    tags=["Service Assignment"],
    response_model=ServiceAssignmentSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ServiceAssignment.service_id),
            exceptions.UnknownValue(ServiceAssignment.operator_id),
            exceptions.UnknownValue(ServiceAssignment.company_id),
            exceptions.InvalidAssociation(
                ServiceAssignment.service_id, ServiceAssignment.company_id
            ),
            exceptions.InvalidAssociation(
                ServiceAssignment.operator_id, ServiceAssignment.company_id
            ),
        ]
    ),
    description=(
        """
                **Creates a new service assignment.**    
                - Executive must have a valid access token.    
                - Logged-in executive must have `company.service.assignment.create` permission.    
                - Duplicate assignments are not allowed.    
            """
    ),
)
async def create_assignment_executive(
    form_param: CreateFormForEX,
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

        company = validate_id(
            session, Company, form_param.company_id, ServiceAssignment.company_id
        )
        service = validate_id(
            session, Service, form_param.service_id, ServiceAssignment.service_id
        )
        operator = validate_id(
            session, Operator, form_param.operator_id, ServiceAssignment.operator_id
        )

        if service.company_id != company.id:
            raise exceptions.InvalidAssociation(
                ServiceAssignment.service_id, ServiceAssignment.company_id
            )
        if operator.company_id != company.id:
            raise exceptions.InvalidAssociation(
                ServiceAssignment.operator_id, ServiceAssignment.company_id
            )
        service_assignment_data = create_service_assignment(
            session, CreateForm(**form_param.model_dump())
        )

        log_event(token, request_info, service_assignment_data)
        return service_assignment_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    tags=["Service Assignment"],
    response_model=ServiceAssignmentSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ServiceAssignment.id),
            exceptions.UnknownValue(ServiceAssignment.operator_id),
            exceptions.InvalidAssociation(
                ServiceAssignment.operator_id, ServiceAssignment.company_id
            ),
        ]
    ),
    description=(
        """
            **Updates an existing service assignment.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.service.assignment.update` permission.    
            - Duplicate assignments are not allowed.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_assignment_executive(
    id: int,
    form_param: UpdateForm,
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

        service_assignment = validate_id(
            session, ServiceAssignment, id, ServiceAssignment.id
        )
        if (
            form_param.operator_id is not None
            and service_assignment.operator_id != form_param.operator_id
        ):
            operator = validate_id(
                session,
                Operator,
                form_param.operator_id,
                ServiceAssignment.operator_id,
            )
            if operator.company_id != service_assignment.company_id:
                raise exceptions.InvalidAssociation(
                    ServiceAssignment.operator_id, ServiceAssignment.company_id
                )
            service_assignment.operator_id = form_param.operator_id

        have_updates = session.is_modified(service_assignment)
        if have_updates:
            session.commit()
            session.refresh(service_assignment)
        service_assignment_data = jsonable_encoder(service_assignment)
        if have_updates:
            log_event(token, request_info, service_assignment_data)
        return service_assignment_data
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
    description=(
        """
            **Deletes an existing service assignment.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have the `company.service.assignment.delete` permission.    
            - Returns 204 No Content even if the specified service assignment does not exist.    
        """
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

        service_assignment = (
            session.query(ServiceAssignment).filter(ServiceAssignment.id == id).first()
        )
        if service_assignment is not None:
            service_assignment_data = delete_service_assignment(
                session, service_assignment
            )
            log_event(token, request_info, service_assignment_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_SERVICE_ASSIGNMENT,
    tags=["Service Assignment"],
    response_model=list[ServiceAssignmentSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of service assignments.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_assignment_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_service_assignments(
            session, QueryParams(**query_params.model_dump())
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_SERVICE_ASSIGNMENT,
    tags=["Service Assignment"],
    response_model=ServiceAssignmentSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ServiceAssignment.service_id),
            exceptions.UnknownValue(ServiceAssignment.operator_id),
        ]
    ),
    description=(
        """
                **Creates a new service assignment.**    
                - Operator must have a valid access token.    
                - Logged-in operator must have `company.service.assignment.create` permission.    
                - Duplicate mappings are not allowed.    
            """
    ),
)
async def create_assignment_operator(
    form_param: CreateFormForOP,
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

        validate_id(
            session,
            Service,
            form_param.service_id,
            ServiceAssignment.service_id,
            extra_filter=(Service.company_id == token.company_id),
        )
        validate_id(
            session,
            Operator,
            form_param.operator_id,
            ServiceAssignment.operator_id,
            extra_filter=(Operator.company_id == token.company_id),
        )
        service_assignment_data = create_service_assignment(
            session, CreateForm(**form_param.model_dump(), company_id=token.company_id)
        )
        log_event(token, request_info, service_assignment_data)
        return service_assignment_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_SERVICE_ASSIGNMENT}/{{id}}",
    tags=["Service Assignment"],
    response_model=ServiceAssignmentSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(ServiceAssignment.id),
            exceptions.UnknownValue(ServiceAssignment.operator_id),
        ]
    ),
    description=(
        """
            **Updates an existing service assignment.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.service.assignment.update` permission.    
            - Duplicate mappings are not allowed.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_assignment_operator(
    id: int,
    form_param: UpdateForm,
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

        service_assignment = validate_id(
            session,
            ServiceAssignment,
            id,
            ServiceAssignment.id,
            extra_filter=(ServiceAssignment.company_id == token.company_id),
        )
        if (
            form_param.operator_id is not None
            and service_assignment.operator_id != form_param.operator_id
        ):
            validate_id(
                session,
                Operator,
                form_param.operator_id,
                ServiceAssignment.operator_id,
                extra_filter=(Operator.company_id == token.company_id),
            )
            service_assignment.operator_id = form_param.operator_id
        have_updates = session.is_modified(service_assignment)
        if have_updates:
            session.commit()
            session.refresh(service_assignment)

        service_assignment_data = jsonable_encoder(service_assignment)
        if have_updates:
            log_event(token, request_info, service_assignment_data)
        return service_assignment_data
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
    description=(
        """
            **Deletes an existing service assignment.**    
            - Requires a valid access token for authentication.    
            - The logged-in operator must have the `company.service.assignment.delete` permission.    
            - Returns 204 No Content even if the specified service assignment does not exist.    
        """
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

        service_assignment = (
            session.query(ServiceAssignment)
            .filter(
                ServiceAssignment.id == id,
                ServiceAssignment.company_id == token.company_id,
            )
            .first()
        )
        if service_assignment is not None:
            service_assignment_data = delete_service_assignment(
                session, service_assignment
            )
            log_event(token, request_info, service_assignment_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_SERVICE_ASSIGNMENT,
    tags=["Service Assignment"],
    response_model=List[ServiceAssignmentSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of service assignments.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_assignment_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_service_assignments(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
