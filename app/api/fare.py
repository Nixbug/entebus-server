"""
Fare API Router.

Provides endpoints for managing fares:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator, vendor)
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Union
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
    Fare,
    VendorToken,
    get_db_session,
)
from app.src.enums import FareScope, OrderIn
from app.src.constants import (
    JSX_TIMEOUT_MS,
    JSX_MAX_MEMORY_BYTES,
    MAX_LOCAL_FARES_PER_COMPANY,
)
from app.src.functions import (
    fuse_exception_responses,
    get_by_id,
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
from app.src.validators import (
    verify_token,
    authorize_executive,
    authorize_operator,
    validate_fare_function,
    validate_id,
)
from app.src.description import Description
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions, schemas
from app.src.schemas import PatchForm
from app.src.filters import PaginationFilter, CreatedOnFilter, UpdatedOnFilter, IDFilter

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class TicketTypesInAttribute(BaseModel):
    """Schema for ticket types in fare attributes."""

    id: int
    name: str


class FareAttributes(BaseModel):
    """Attributes for fare details."""

    df_version: int
    ticket_types: list[TicketTypesInAttribute]
    currency_type: str
    distance_unit: str
    extras: dict[str, Any]


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


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
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


class UpdateForm(PatchForm):
    """Form data for updating a fare."""

    name: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=NAME_PATTERN
    )
    attributes: FareAttributes | None = Field(default=None)
    function: str | None = Field(default=None, min_length=1, max_length=32768)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
## Lock Generator
# ---------------------------------------------------------------------------
def construct_fare_reference_lock(fare_id: int, version: int) -> str:
    """
    Creates a Redis lock key for Fare snapshot creation and reference operations.

    Serializes access to Fare snapshot operations for the same
    fare_id, preventing concurrent creation or reference count
    updates of the same snapshot.

    Args:
        fare_id (int): Fare ID.
        version (int): Version of the fare.

    Returns:
        str: Redis lock key in the format "lk_fare_:<fare_id>:<version>".
    """
    return f"lk_fare_:{fare_id}:{version}"


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_fare(
    session: Session,
    form_param: CreateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Creates a new fare record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a fare.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: The created fare data.
    """
    if form_param.scope == FareScope.LOCAL:
        local_fare_count = (
            session.query(Fare)
            .filter(
                Fare.company_id == form_param.company_id,
                Fare.scope == FareScope.LOCAL,
            )
            .count()
        )
        if local_fare_count >= MAX_LOCAL_FARES_PER_COMPANY:
            raise exceptions.LimitExceeded(Fare)

    attributes = form_param.attributes.model_dump()
    validate_fare_function(form_param.function, attributes)
    fare = Fare(
        company_id=form_param.company_id,
        name=form_param.name,
        attributes=attributes,
        function=form_param.function,
        scope=form_param.scope,
    )
    session.add(fare)
    session.commit()
    session.refresh(fare)

    fare_data = jsonable_encoder(fare)
    log_event(token, request_info, fare_data)
    return fare_data


def update_fare(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    fare_filter=None,
) -> dict:
    """
    Update an existing fare in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the fare to update.
        form_param (UpdateForm): Form data for updating the fare.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        fare_filter (Optional): Additional filter to apply when fetching the fare.

    Returns:
        dict: JSON-encoded representation of the updated fare.
    """
    fare = validate_id(session, Fare, id, Fare.id, extra_filter=fare_filter)

    update_data = form_param.model_dump(exclude_unset=True)
    revalidate_fare = False
    if "attributes" in update_data:
        if update_data["attributes"] != fare.attributes:
            fare.attributes = update_data["attributes"]
            revalidate_fare = True
        update_data.pop("attributes")
    if "function" in update_data:
        revalidate_fare = True

    update_if_changed(fare, update_data)
    if revalidate_fare:
        validate_fare_function(fare.function, fare.attributes)
    if session.is_modified(fare):
        fare.version += 1
        session.commit()
        session.refresh(fare)
        fare_data = jsonable_encoder(fare)
        log_event(token, request_info, fare_data)
    else:
        fare_data = jsonable_encoder(fare)
    return fare_data


def delete_fare(
    session: Session,
    id: int,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    fare_filter=None,
) -> None:
    """
    Delete a fare from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the fare to delete.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        fare_filter (Optional): Additional filter to apply when fetching the fare.
    """
    fare = get_by_id(session, Fare, id, extra_filter=fare_filter)
    if fare is None:
        return

    fare_data = jsonable_encoder(fare)
    session.delete(fare)
    session.commit()
    log_event(token, request_info, fare_data)


def search_fares(session: Session, query_params: QueryParams) -> list[Fare]:
    """
    Search for Fares based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve fares that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Fare]: List of Fares that match the search criteria.
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
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS_COMMON = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidFareVersion(),
    exceptions.InvalidFareFunction(),
    exceptions.JSTimeLimitExceeded(),
    exceptions.JSMemoryLimitExceeded(),
    exceptions.UnknownTicketType(),
    exceptions.LimitExceeded(Fare),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Fare.id),
    exceptions.InvalidFareVersion(),
    exceptions.InvalidFareFunction(),
    exceptions.JSTimeLimitExceeded(),
    exceptions.JSMemoryLimitExceeded(),
    exceptions.UnknownTicketType(),
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
    .add_head("Creates a new fare.")
    .add_line("The fare function is validated against the provided attributes.")
    .add_line(
        f"The maximum allowed size for the fare function is `{JSX_MAX_MEMORY_BYTES // 1024} KB` and maximum execution time is `{JSX_TIMEOUT_MS} ms`."
    )
    .add_line(
        f"Local fare creation is limited to `{MAX_LOCAL_FARES_PER_COMPANY}` fares per company. Requests exceeding this limit will be rejected."
    )
    .add_line("Preferable dynamic fare version is 1.")
    .add_line("Preferable distance unit is meter and currency is INR.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing fare.")
    .add_line("DF function and attributes are validated together.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line("Preferable dynamic fare version is 1.")
    .add_line("Preferable distance unit is meter and currency is INR.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing fare.")
    .add_line("Returns 204 No Content even if the specified fare does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of fares.")


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
            *POST_EXCEPTIONS_COMMON,
            exceptions.MissingParameter(Fare.company_id),
            exceptions.UnexpectedParameter(Fare.company_id),
            exceptions.UnknownValue(Fare.company_id),
        ]
    ),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.fare.create` permission.")
        .add_line(
            "If scope is GLOBAL, company_id must be null. If scope is LOCAL, company_id must be provided."
        )
        .to_string()
    ),
)
async def create_fare_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_FARE],
        )
        if form_param.scope == FareScope.GLOBAL and form_param.company_id is not None:
            raise exceptions.UnexpectedParameter(Fare.company_id)
        if form_param.scope == FareScope.LOCAL and form_param.company_id is None:
            raise exceptions.MissingParameter(Fare.company_id)
        if form_param.company_id is not None:
            validate_id(session, Company, form_param.company_id, Fare.company_id)

        return create_fare(
            session, CreateForm(**form_param.model_dump()), token, request_info
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_FARE}/{{id}}",
    summary="Update fare",
    tags=["Fare"],
    response_model=FareSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.fare.update` permission.")
        .to_string()
    ),
)
async def update_fare_for_executive(
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
            [ExecutivePermissionPath.UPDATE_COMPANY_FARE],
        )
        return update_fare(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_FARE}/{{id}}",
    summary="Delete fare",
    tags=["Fare"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `company.fare.delete` permission."
        )
        .to_string()
    ),
)
async def delete_fare_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_FARE],
        )
        delete_fare(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_FARE,
    summary="Fetch fare",
    tags=["Fare"],
    response_model=list[FareSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_fares_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_fares(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_FARE,
    summary="Create fare",
    tags=["Fare"],
    response_model=FareSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS_COMMON),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.fare.create` permission.")
        .add_line(
            "Operators can only create fares with LOCAL scope for their own company."
        )
        .to_string()
    ),
)
async def create_fare_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_FARE],
        )
        return create_fare(
            session,
            CreateForm(
                **form_param.model_dump(),
                company_id=token.company_id,
                scope=FareScope.LOCAL,
            ),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_FARE}/{{id}}",
    summary="Update fare",
    tags=["Fare"],
    response_model=FareSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.fare.update` permission.")
        .add_line("Only fares belonging to the operator's company can be updated.")
        .to_string()
    ),
)
async def update_fare_for_operator(
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
            [OperatorPermissionPath.UPDATE_COMPANY_FARE],
        )
        return update_fare(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
            fare_filter=(Fare.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_FARE}/{{id}}",
    summary="Delete fare",
    tags=["Fare"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in operator must have the `company.fare.delete` permission."
        )
        .to_string()
    ),
)
async def delete_fare_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_FARE],
        )
        delete_fare(
            session,
            id,
            fare_filter=(Fare.company_id == token.company_id),
            token=token,
            request_info=request_info,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_FARE,
    summary="Fetch fare",
    tags=["Fare"],
    response_model=list[FareSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_fares_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_fares(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_FARE,
    summary="Fetch fare",
    tags=["Fare"],
    response_model=list[FareSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_fares_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return search_fares(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
