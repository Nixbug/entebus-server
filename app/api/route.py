"""
Route API Router for EnteBus.

Provides endpoints for managing routes, including creation.
Uses Pydantic schemas for input validation and structured output.
Endpoints for update, deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime, time
from fastapi import APIRouter, status, Depends
from sqlalchemy.orm.session import Session
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    Route,
    ExecutiveToken,
    SessionLocal,
    OperatorToken,
    Company,
)
from app.src import exceptions
from app.src.openobserve import log_event
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_ROUTE
from app.src.validators import (
    validate_id,
    verify_token,
    verify_permission,
)
from app.src.functions import (
    get_request_info,
    get_executive_roles,
    get_operator_roles,
    fuse_exception_responses,
    update_if_changed,
)
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath

route_executive = APIRouter()
route_operator = APIRouter()


# Output Schema
class RouteSchema(BaseModel):
    """Schema for route response."""

    id: int
    company_id: int
    name: str
    start_time: time
    status: int
    updated_on: datetime | None
    created_on: datetime


# Input Forms
class CreateFormForOP(BaseModel):
    """Form data for creating a new route for an operator."""

    name: str = Field(min_length=1, max_length=4096, pattern=NAME_PATTERN)
    start_time: time = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new route for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new route."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating a route."""

    name: str = Field(min_length=1, max_length=4096, pattern=NAME_PATTERN, default=None)
    start_time: time = Field(default=None)


# Functions
def create_route(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a route.

    Returns:
        dict: The created route data.
    """
    route = Route(
        company_id=form_param.company_id,
        name=form_param.name,
        start_time=form_param.start_time,
    )
    session.add(route)
    session.commit()
    session.refresh(route)
    route_data = jsonable_encoder(route)
    return route_data


def update_route(session: Session, route: Route, form_param: UpdateForm):
    """
    Updates an existing route record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        route (Route): The existing route record to be updated.
        form_param (UpdateForm): Form data for updating the route.

    Returns:
        dict: The updated route data.
    """
    update_data = form_param.model_dump(exclude_unset=True)
    update_if_changed(route, update_data)
    have_updates = session.is_modified(route)
    if have_updates:
        session.commit()
        session.refresh(route)

    route_data = jsonable_encoder(route)
    return have_updates, route_data


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_ROUTE,
    tags=["Route"],
    response_model=RouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new route.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.route.create` permission.    
            - Duplicate route names are not allowed.       
            - By default the status of the route is INVALID.    
        """
    ),
)
async def create_route_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_ROUTE)

        validate_id(session, Company, form_param.company_id, Route.company_id)
        route_data = create_route(session, CreateForm(**form_param.model_dump()))

        log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_ROUTE}/{{id}}",
    tags=["Route"],
    response_model=RouteSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Route.id),
        ]
    ),
    description=(
        """
            **Updates an existing route for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.route.update` permission.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_route_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_ROUTE)

        route = validate_id(session, Route, id, Route.id)
        have_updates, route_data = update_route(
            session, route, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_ROUTE,
    tags=["Route"],
    response_model=RouteSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new route.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.route.create` permission.    
            - Duplicate route names are not allowed.       
        """
    ),
)
async def create_route_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_ROUTE)

        route_data = create_route(
            session, CreateForm(**form_param.model_dump(), company_id=token.company_id)
        )
        log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_ROUTE}/{{id}}",
    tags=["Route"],
    response_model=RouteSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Route.id),
        ]
    ),
    description=(
        """
            **Updates an existing route for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.route.update` permission.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_route_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_ROUTE)

        route = validate_id(
            session, Route, id, Route.id, (Route.company_id == token.company_id)
        )
        have_updates, route_data = update_route(
            session, route, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )
        if have_updates:
            log_event(token, request_info, route_data)
        return route_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
