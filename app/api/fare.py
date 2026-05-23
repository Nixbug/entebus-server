"""
Fare API Router for EnteBus.

Provides endpoints for managing fares, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from typing import List, Dict, Any
from fastapi import APIRouter, status, Depends, Response, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session
from sqlalchemy import or_, String

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.src.db import (
    Company,
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
    Fare,
    VendorToken,
)
from app.src.enums import FareScope, OrderIn
from app.src.functions import (
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    enum_str,
    update_if_changed,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_name_filters,
)
from app.src.openobserve import log_event
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_FARE
from app.src.validators import verify_token, verify_permission
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.validators import validate_fare_function, validate_id
from app.src.filters import PaginationFilter, CreatedOnFilter, UpdatedOnFilter, IDFilter

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()


## Output Schema
class TicketTypesInAttribute(BaseModel):
    """Schema for ticket types in fare attributes."""

    id: int
    name: str


class FareAttributes(BaseModel):
    """Attributes for fare details."""

    df_version: int
    ticket_types: List[TicketTypesInAttribute]
    currency_type: str
    distance_unit: str
    extras: Dict[str, Any]


class FareSchema(BaseModel):
    """Schema for fare response."""

    id: int
    company_id: int | None
    version: int
    name: str
    attributes: FareAttributes
    function: str
    scope: FareScope
    updated_on: datetime | None
    created_on: datetime


## Input Forms
class CreateFormForOP(BaseModel):
    """Form data for creating a new fare for an operator."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    attributes: FareAttributes = Field()
    function: str = Field(min_length=1, max_length=32768)


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new fare for an executive."""

    company_id: int | None = Field(default=None)
    scope: FareScope = Field(description=enum_str(FareScope), default=FareScope.GLOBAL)


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new fare."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating a fare."""

    name: str = Field(default=None, min_length=1, max_length=32, pattern=NAME_PATTERN)
    attributes: FareAttributes = Field(default=None)
    function: str = Field(default=None, min_length=1, max_length=32768)


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering fare results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    VERSION = "version"


class QueryParamsForOP(IDFilter, CreatedOnFilter, UpdatedOnFilter, PaginationFilter):
    """Query parameters for operators."""

    search: str | None = Field(Query(default=None))
    name: str | None = Field(Query(default=None))
    scope: FareScope | None = Field(
        Query(default=None, description=enum_str(FareScope))
    )
    version: int | None = Field(Query(default=None))
    version_ge: int | None = Field(Query(default=None))
    version_le: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParamsForVE(QueryParamsForEX):
    """Query parameters for vendors."""

    pass


class QueryParams(QueryParamsForEX):
    """Generic combined query parameters."""

    pass


## Functions
def create_fare(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new fare record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a fare.

    Returns:
        dict: The created fare data.
    """
    form_param.attributes = form_param.attributes.model_dump()
    validate_fare_function(form_param.function, form_param.attributes)
    fare = Fare(
        company_id=form_param.company_id,
        name=form_param.name,
        attributes=form_param.attributes,
        function=form_param.function,
        scope=form_param.scope,
    )
    session.add(fare)
    session.commit()
    session.refresh(fare)
    fare_data = jsonable_encoder(fare)
    return fare_data


def update_fare(session: Session, fare: Fare, form_param: UpdateForm):
    """
    Updates an existing fare record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        fare (Fare): The existing fare record to be updated.
        form_param (UpdateForm): Form data for updating the fare.

    Returns:
        dict: The updated fare data.
    """
    update_data = form_param.model_dump(exclude_unset=True)
    if form_param.attributes is not None:
        attribute_data = form_param.attributes.model_dump()
        if attribute_data != fare.attributes:
            fare.attributes = attribute_data
        update_data.pop("attributes")
    update_if_changed(fare, update_data)
    validate_fare_function(fare.function, fare.attributes)
    have_updates = session.is_modified(fare)
    if have_updates:
        fare.version += 1
        session.commit()
        session.refresh(fare)

    fare_data = jsonable_encoder(fare)
    return have_updates, fare_data


def delete_fare(session: Session, fare: Fare) -> dict:
    """
    Deletes a fare from the database.

    Args:
        session (Session): SQLAlchemy database session.
        fare (Fare): Fare to delete.

    Returns:
        dict: JSON-encoded representation of the deleted fare.
    """
    fare_data = jsonable_encoder(fare)
    session.delete(fare)
    session.commit()
    return fare_data


def search_fare(session: Session, query_params: QueryParams) -> List[Fare]:
    """
    Search for Fares based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve fares that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Fare]: List of Fares that match the search criteria.
    """
    query = session.query(Fare)
    if query_params.company_id is not None:
        query = query.filter(
            or_(Fare.company_id == query_params.company_id, Fare.company_id.is_(None))
        )
    if query_params.scope is not None:
        query = query.filter(Fare.scope == query_params.scope)
    if query_params.version is not None:
        query = query.filter(Fare.version == query_params.version)
    if query_params.version_ge is not None:
        query = query.filter(Fare.version >= query_params.version_ge)
    if query_params.version_le is not None:
        query = query.filter(Fare.version <= query_params.version_le)

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Fare.id.cast(String).ilike(search),
                Fare.name.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Fare, query_params)
    query = apply_name_filters(query, Fare, query_params)
    query = apply_created_on_filters(query, Fare, query_params)
    query = apply_updated_on_filters(query, Fare, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Fare, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    fares = query.all()
    return fares


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_FARE,
    summary="Create fare",
    tags=["Fare"],
    response_model=FareSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Fare.company_id),
            exceptions.UnexpectedParameter(Fare.company_id),
            exceptions.MissingParameter(Fare.company_id),
            exceptions.InvalidFareVersion(),
            exceptions.InvalidFareFunction(),
            exceptions.JSTimeLimitExceeded(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.UnknownTicketType(),
        ]
    ),
    description=(
        """
            **Creates a new fare for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.fare.create` permission.    
            - If scope is GLOBAL, company_id must be null. If scope is LOCAL, company_id must be provided.    
            - The fare function is validated against the provided attributes.    
            - The maximum allowed size for the fare function is 10 MB and maximum execution time is 1 second.    
            - Preferable dynamic fare version is 1.    
            - Preferable distance unit is meter and currency is INR.    
        """
    ),
)
async def create_fare_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_FARE)

        if form_param.scope == FareScope.GLOBAL and form_param.company_id is not None:
            raise exceptions.UnexpectedParameter(Fare.company_id)
        if form_param.scope == FareScope.LOCAL and form_param.company_id is None:
            raise exceptions.MissingParameter(Fare.company_id)
        if form_param.company_id is not None:
            validate_id(session, Company, form_param.company_id, Fare.company_id)
        fare_data = create_fare(session, CreateForm(**form_param.model_dump()))

        log_event(token, request_info, fare_data)
        return fare_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_FARE}/{{id}}",
    summary="Update fare",
    tags=["Fare"],
    response_model=FareSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Fare.id),
            exceptions.InvalidFareVersion(),
            exceptions.InvalidFareFunction(),
            exceptions.JSTimeLimitExceeded(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.UnknownTicketType(),
        ]
    ),
    description=(
        """
            **Updates an existing fare for a company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.fare.update` permission.    
            - DF function and attributes are validated together.    
            - Empty PATCH requests are allowed and will result in no changes.    
            - Preferable dynamic fare version is 1.    
            - Preferable distance unit is meter and currency is INR.    
        """
    ),
)
async def update_fare_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_FARE)

        fare = validate_id(session, Fare, id, Fare.id)

        have_updates, fare_data = update_fare(
            session, fare, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, fare_data)
        return fare_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_FARE}/{{id}}",
    summary="Delete fare",
    tags=["Fare"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an existing fare.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have the `company.fare.delete` permission.    
            - Returns 204 No Content even if the specified fare does not exist.    
        """
    ),
)
async def delete_fare_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.DELETE_COMPANY_FARE)

        fare = session.query(Fare).filter(Fare.id == id).first()
        if fare is not None:
            fare_data = delete_fare(session, fare)
            log_event(token, request_info, fare_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_FARE,
    summary="Fetch fare",
    tags=["Fare"],
    response_model=List[FareSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of fares.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_fares_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_fare(
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
    URL_FARE,
    summary="Create fare",
    tags=["Fare"],
    response_model=FareSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidFareVersion(),
            exceptions.InvalidFareFunction(),
            exceptions.JSTimeLimitExceeded(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.UnknownTicketType(),
        ]
    ),
    description=(
        """
            **Creates a new fare for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.fare.create` permission.    
            - Operators can only create fares with LOCAL scope for their own company.    
            - The fare function is validated against the provided attributes.    
            - Enforces function size is 10 MB or less and execution time is 1 second or less.    
            - Preferable dynamic fare version is 1.    
            - Preferable distance unit is meter and currency is INR.    
        """
    ),
)
async def create_fare_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_FARE)

        fare_data = create_fare(
            session,
            CreateForm(
                **form_param.model_dump(),
                company_id=token.company_id,
                scope=FareScope.LOCAL,
            ),
        )

        log_event(token, request_info, fare_data)
        return fare_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_FARE}/{{id}}",
    summary="Update fare",
    tags=["Fare"],
    response_model=FareSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidFareVersion(),
            exceptions.InvalidFareFunction(),
            exceptions.JSTimeLimitExceeded(),
            exceptions.JSMemoryLimitExceeded(),
            exceptions.UnknownTicketType(),
            exceptions.UnknownValue(Fare.id),
        ]
    ),
    description=(
        """
            **Updates an existing fare for a company.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.fare.update` permission.    
            - DF function and attributes are validated together.    
            - Only fares belonging to the operator's company can be updated.    
            - Empty PATCH requests are allowed and will result in no changes.    
            - Preferable dynamic fare version is 1.    
            - Preferable distance unit is meter and currency is INR.    
        """
    ),
)
async def update_fare_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_FARE)

        fare = validate_id(
            session, Fare, id, Fare.id, (Fare.company_id == token.company_id)
        )

        have_updates, fare_data = update_fare(
            session, fare, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, fare_data)
        return fare_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_FARE}/{{id}}",
    summary="Delete fare",
    tags=["Fare"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an existing fare.**    
            - Requires a valid access token for authentication.    
            - The logged-in operator must have the `company.fare.delete` permission.    
            - Returns 204 No Content even if the specified fare does not exist.    
        """
    ),
)
async def delete_fare_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.DELETE_COMPANY_FARE)

        fare = (
            session.query(Fare)
            .filter(Fare.id == id, Fare.company_id == token.company_id)
            .first()
        )
        if fare is not None:
            fare_data = delete_fare(session, fare)
            log_event(token, request_info, fare_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_FARE,
    summary="Fetch fare",
    tags=["Fare"],
    response_model=List[FareSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of fares.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_fares_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_fare(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_FARE,
    summary="Fetch fare",
    tags=["Fare"],
    response_model=List[FareSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of fares.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_fares_for_vendor(
    query_params: QueryParamsForVE = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        verify_token(session, VendorToken, access_token.credentials)

        return search_fare(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
