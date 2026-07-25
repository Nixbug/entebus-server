"""
Operator Account API router.

Provides endpoints for managing operator accounts:
    - POST (executive, operator)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator)
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, EmailStr, Field
from pydantic_extra_types.phone_numbers import PhoneNumber
from sqlalchemy import String, or_
from sqlalchemy.sql import ColumnElement
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions, schemas
from app.src.buckets import OPERATOR_IMAGES
from app.src.constants import MAX_OPERATORS_PER_COMPANY
from app.src.db import (
    ExecutiveToken,
    Operator,
    OperatorImage,
    OperatorToken,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import AccountStatus, GenderType, OperatorType, OrderIn
from app.src.filters import (
    AccountDataFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.functions import (
    apply_account_filters,
    apply_created_on_filters,
    apply_id_filters,
    apply_status_filters,
    apply_type_filters,
    apply_updated_on_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_operator_roles,
    get_request_info,
    update_if_changed,
)
from app.src.minio import delete_file
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.regex import PASSWORD_PATTERN, USERNAME_PATTERN
from app.src.schemas import PatchForm
from app.src.urls import URL_OPERATOR_ACCOUNT
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    verify_permission,
    verify_token,
)

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class OperatorSchema(BaseModel):
    """Schema for operator account response."""

    id: int
    company_id: int
    username: str
    gender: int
    description: str | None
    type: int
    full_name: str | None
    status: int
    phone_number: str | None
    email_id: str | None
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new operator account for an operator."""

    username: str = Field(min_length=4, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=32, pattern=PASSWORD_PATTERN)
    gender: GenderType = Field(
        description=enum_str(GenderType),
        default=GenderType.OTHER,
    )
    description: str | None = Field(min_length=1, max_length=1024, default=None)
    type: OperatorType = Field(
        description=enum_str(OperatorType),
        default=OperatorType.NORMAL,
    )
    full_name: str | None = Field(min_length=1, max_length=32, default=None)
    status: AccountStatus = Field(
        description=enum_str(AccountStatus),
        default=AccountStatus.ACTIVE,
    )
    phone_number: PhoneNumber | None = Field(
        max_length=32,
        default=None,
        description="Phone number in RFC 3966 format",
    )
    email_id: EmailStr | None = Field(
        max_length=256,
        default=None,
        description="Email in RFC 5322 format",
    )


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new operator account for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new operator account."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating an operator account."""

    password: str | None = Field(
        default=None,
        min_length=8,
        max_length=32,
        pattern=PASSWORD_PATTERN,
    )
    gender: GenderType | None = Field(description=enum_str(GenderType), default=None)
    description: Annotated[str | None, "nullable"] = Field(
        min_length=1,
        max_length=1024,
        default=None,
    )
    type: OperatorType | None = Field(description=enum_str(OperatorType), default=None)
    full_name: Annotated[str | None, "nullable"] = Field(
        min_length=1,
        max_length=32,
        default=None,
    )
    status: AccountStatus | None = Field(
        description=enum_str(AccountStatus),
        default=None,
    )
    phone_number: Annotated[PhoneNumber | None, "nullable"] = Field(
        max_length=32,
        default=None,
        description="Phone number in RFC 3966 format",
    )
    email_id: Annotated[EmailStr | None, "nullable"] = Field(
        max_length=256,
        default=None,
        description="Email in RFC 5322 format",
    )


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"


class QueryParamsForOP(
    AccountDataFilter, UpdatedOnFilter, CreatedOnFilter, IDFilter, PaginationFilter
):
    """Query parameters for operators."""

    search: str | None = Field(Query(default=None))
    description: str | None = Field(Query(default=None))
    type_list: list[OperatorType] | None = Field(
        Query(default=None, description=enum_str(OperatorType))
    )
    status_list: list[AccountStatus] | None = Field(
        Query(default=None, description=enum_str(AccountStatus))
    )
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
## Helper Functions
# ---------------------------------------------------------------------------
def operator_account_to_dict(operator: Operator) -> dict:
    """
    Convert an Operator account to a dictionary representation.

    Args:
        operator (Operator): The operator object to convert.

    Returns:
        dict: A dictionary representation of the operator object without password.
    """
    operator_account_dict = jsonable_encoder(operator, exclude={Operator.password.name})
    return operator_account_dict


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_operator(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Create a new operator account in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new operator account.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created operator account data.
    """
    operator_count = (
        session.query(Operator)
        .filter(Operator.company_id == form_param.company_id)
        .count()
    )
    if operator_count >= MAX_OPERATORS_PER_COMPANY:
        raise exceptions.LimitExceeded(Operator)

    operator = Operator(
        company_id=form_param.company_id,
        username=form_param.username,
        password=form_param.password,
        gender=form_param.gender,
        description=form_param.description,
        type=form_param.type,
        full_name=form_param.full_name,
        status=form_param.status,
        phone_number=form_param.phone_number,
        email_id=form_param.email_id,
    )
    session.add(operator)
    session.commit()
    session.refresh(operator)

    operator_account_data = operator_account_to_dict(operator)
    log_event(token, request_info, operator_account_data)
    return operator_account_data


def update_operator(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
    operator_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Update an existing operator account in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the operator account to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        operator_filter: Additional filter for validating operator ownership.

    Returns:
        dict: Updated operator account data.
    """
    operator = validate_id(
        session,
        Operator,
        id,
        Operator.id,
        extra_filter=operator_filter,
    )

    update_data = form_param.model_dump(exclude_unset=True)
    if "status" in update_data:
        if operator.status != update_data["status"]:
            if update_data["status"] == AccountStatus.SUSPENDED:
                session.query(OperatorToken).filter(
                    OperatorToken.operator_id == id,
                    OperatorToken.is_revoked.is_(False),
                ).update({OperatorToken.is_revoked: True})
            operator.status = update_data["status"]
        update_data.pop("status")

    update_if_changed(operator, update_data)
    if session.is_modified(operator):
        session.commit()
        session.refresh(operator)
        operator_account_data = operator_account_to_dict(operator)
        log_event(token, request_info, operator_account_data)
    else:
        operator_account_data = operator_account_to_dict(operator)
    return operator_account_data


def search_operators(session: Session, query_params: QueryParams) -> list[Operator]:
    """
    Search for operator accounts based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve operators that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Operator]: List of operators that match the search criteria.
    """
    query = session.query(Operator)
    if query_params.company_id is not None:
        query = query.filter(Operator.company_id == query_params.company_id)
    if query_params.description is not None:
        query = query.filter(
            Operator.description.ilike(f"%{query_params.description}%")
        )

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Operator.id.cast(String).ilike(search),
                Operator.username.ilike(search),
                Operator.full_name.ilike(search),
                Operator.description.ilike(search),
                Operator.phone_number.ilike(search),
                Operator.email_id.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Operator, query_params)
    query = apply_created_on_filters(query, Operator, query_params)
    query = apply_updated_on_filters(query, Operator, query_params)
    query = apply_account_filters(query, Operator, query_params)
    query = apply_status_filters(query, Operator, query_params)
    query = apply_type_filters(query, Operator, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Operator, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    operators = query.all()
    return operators


def delete_operator(
    session: Session,
    id: int,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
    operator_filter: ColumnElement[bool] | None = None,
):
    """
    Delete an operator account from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the operator account to delete.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        operator_filter: Additional filter for operator ownership.
    """
    operator = get_by_id(session, Operator, id, extra_filter=operator_filter)
    if operator is None:
        return

    operator_images = (
        session.query(OperatorImage)
        .filter(OperatorImage.operator_id == operator.id)
        .all()
    )
    operator_account_data = operator_account_to_dict(operator)
    session.delete(operator)
    session.commit()

    # Delete operator images from object storage.
    for operator_image in operator_images:
        delete_file(OPERATOR_IMAGES, str(operator_image.id))

    log_event(token, request_info, operator_account_data)


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.LimitExceeded(Operator),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Operator.id),
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
    .add_head("Creates a new operator account.")
    .add_line("Duplicate usernames are not allowed.")
    .add_line("By default the user is created in active status.")
    .add_line(
        f"Maximum `{MAX_OPERATORS_PER_COMPANY}` operators are allowed per company."
    )
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing operator account.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an existing operator account.")
    .add_line("Returns 204 No Content even if the specified account does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of operators.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_OPERATOR_ACCOUNT,
    summary="Create operator account",
    tags=["Operator Account"],
    response_model=OperatorSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.operator.create` permission.")
        .to_string()
    ),
)
async def create_operator_account_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_OPERATOR],
        )
        return create_operator(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_OPERATOR_ACCOUNT}/{{id}}",
    summary="Update operator account",
    tags=["Operator Account"],
    response_model=OperatorSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.operator.update` permission.")
        .to_string()
    ),
)
async def update_operator_account_for_executive(
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
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR],
        )
        return update_operator(session, id, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_OPERATOR_ACCOUNT,
    summary="Fetch operator account",
    tags=["Operator Account"],
    response_model=list[OperatorSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_operator_accounts_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_operators(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_OPERATOR_ACCOUNT}/{{id}}",
    summary="Delete operator account",
    tags=["Operator Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in executive must have the `company.operator.delete` permission."
        )
        .to_string()
    ),
)
async def delete_operator_account_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_OPERATOR],
        )
        delete_operator(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_ACCOUNT,
    summary="Create operator account",
    tags=["Account"],
    response_model=OperatorSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.operator.create` permission.")
        .to_string()
    ),
)
async def create_operator_account_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_OPERATOR],
        )
        return create_operator(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_OPERATOR_ACCOUNT}/{{id}}",
    summary="Update operator account",
    tags=["Account"],
    response_model=OperatorSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.operator.update` permission to update other operators."
        )
        .add_line("Operators can update their own account except status.")
        .to_string()
    ),
)
async def update_operator_account_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)

        is_self_update = id == token.operator_id
        if not is_self_update:
            roles = get_operator_roles(session, token)
            verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR)
        if is_self_update and form_param.status is not None:
            raise exceptions.NoPermission()

        return update_operator(
            session,
            id,
            form_param,
            token,
            request_info,
            operator_filter=(Operator.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_OPERATOR_ACCOUNT,
    summary="Fetch operator account",
    tags=["Account"],
    response_model=list[OperatorSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_operator_accounts_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_operators(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_OPERATOR_ACCOUNT}/{{id}}",
    summary="Delete operator account",
    tags=["Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "The logged-in operator must have the `company.operator.delete` permission."
        )
        .add_line("Self-deletion is not allowed for operators.")
        .to_string()
    ),
)
async def delete_operator_account_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_OPERATOR],
        )
        if token.operator_id == id:
            raise exceptions.NoPermission()

        delete_operator(
            session,
            id,
            token,
            request_info,
            operator_filter=(Operator.company_id == token.company_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
