"""
Company Notification API router.

Provides endpoints for viewing and updating company-level notifications:
    - GET (executive, operator)
    - PATCH (operator)
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions
from app.src import schemas
from app.src.constants import TMZ_PRIMARY
from app.src.description import Description
from app.src.db import (
    CompanyNotification,
    ExecutiveToken,
    Operator,
    OperatorToken,
    get_db_session,
)
from app.src.enums import NotificationType, OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, UpdatedOnFilter
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
)
from app.src.openobserve import log_event
from app.src.schemas import PatchForm
from app.src.urls import URL_COMPANY_NOTIFICATION
from app.src.validators import validate_id, verify_token

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class CompanyNotificationSchema(BaseModel):
    """Schema for company notification response."""

    id: int
    company_id: int
    operator_types: list[int] | None
    type: int
    title: str
    details: dict[str, Any]
    is_read: bool
    read_at: datetime | None
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class UpdateForm(PatchForm):
    """Form data for updating company notification state."""

    is_read: bool = Field()


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering company notification results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(IDFilter, CreatedOnFilter, UpdatedOnFilter, PaginationFilter):
    """Query parameters for operators."""

    type_list: list[NotificationType] | None = Field(
        Query(default=None, description=enum_str(NotificationType))
    )
    is_read: bool | None = Field(Query(default=None))
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
def update_company_notification(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: OperatorToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Update an existing company notification in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the company notification to update.
        form_param (UpdateForm): Form data containing the updated notification state.
        token (OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: JSON-encoded representation of the updated notification.
    """
    company_notification = validate_id(
        session,
        CompanyNotification,
        id,
        CompanyNotification.id,
        extra_filter=(CompanyNotification.company_id == token.company_id),
    )

    update_data = form_param.model_dump(exclude_unset=True)
    if "is_read" in update_data:
        if company_notification.is_read != update_data["is_read"]:
            operator = (
                session.query(Operator)
                .filter(
                    Operator.id == token.operator_id,
                    Operator.company_id == token.company_id,
                )
                .first()
            )
            assert operator is not None, "Operator should not be None"
            if (
                company_notification.operator_types
                and operator.type not in company_notification.operator_types
            ):
                raise exceptions.NoPermission()

            company_notification.is_read = update_data["is_read"]
            company_notification.read_at = (
                datetime.now(TMZ_PRIMARY) if update_data["is_read"] else None
            )
        update_data.pop("is_read")

    if session.is_modified(company_notification):
        session.commit()
        session.refresh(company_notification)
        notification_data = jsonable_encoder(company_notification)
        log_event(token, request_info, notification_data)
    else:
        notification_data = jsonable_encoder(company_notification)
    return notification_data


def search_company_notifications(session: Session, query_params: QueryParams):
    """
    Search company notifications using the project's standard filter pattern.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[CompanyNotification]: List of CompanyNotification instances that match the search criteria.
    """
    query = session.query(CompanyNotification)
    if query_params.company_id is not None:
        query = query.filter(CompanyNotification.company_id == query_params.company_id)
    if query_params.type_list is not None:
        query = query.filter(CompanyNotification.type.in_(query_params.type_list))
    if query_params.is_read is not None:
        query = query.filter(CompanyNotification.is_read == query_params.is_read)

    # Generalized filters
    query = apply_id_filters(query, CompanyNotification, query_params)
    query = apply_created_on_filters(query, CompanyNotification, query_params)
    query = apply_updated_on_filters(query, CompanyNotification, query_params)

    # Ordering and pagination
    ordering_attr = getattr(CompanyNotification, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    company_notifications = query.all()
    return company_notifications


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(CompanyNotification.id),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
GET_DESCRIPTION = Description().add_head(
    "Fetches a list of company-level notifications."
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates the read state of a company notification.")
    .add_line("Only the is_read flag can be modified.")
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.get(
    URL_COMPANY_NOTIFICATION,
    summary="Fetch company notifications",
    tags=["Company Notification"],
    response_model=list[CompanyNotificationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_company_notifications_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_company_notifications(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.get(
    URL_COMPANY_NOTIFICATION,
    summary="Fetch company notifications",
    tags=["Company Notification"],
    response_model=list[CompanyNotificationSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_company_notifications_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_company_notifications(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    URL_COMPANY_NOTIFICATION,
    summary="Update company notification read state",
    tags=["Company Notification"],
    response_model=CompanyNotificationSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=PATCH_DESCRIPTION.copy()
    .add_line(
        "Only operators with the appropriate type can update the read state of a notification."
    )
    .to_string(),
)
async def update_company_notification_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return update_company_notification(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)
