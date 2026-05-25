"""
Operator Account API Router for EnteBus.

Provides endpoints for managing operator accounts, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from enum import StrEnum
from typing import List, Tuple
from datetime import datetime
from fastapi import APIRouter, Query, status, Depends, Response
from fastapi.encoders import jsonable_encoder
from pydantic_extra_types.phone_numbers import PhoneNumber
from sqlalchemy import String, or_
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
    Operator,
    OperatorImage,
)
from app.src.enums import AccountStatus, GenderType, OperatorType, OrderIn
from app.src.filters import (
    AccountDataFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.minio import delete_file
from app.src.buckets import OPERATOR_IMAGES
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.regex import PASSWORD_PATTERN, USERNAME_PATTERN
from app.src.urls import URL_OPERATOR_ACCOUNT
from app.src.openobserve import log_event
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    verify_permission,
    verify_token,
    validate_id,
)
from app.src.functions import (
    apply_account_filters,
    apply_created_on_filters,
    apply_id_filters,
    apply_updated_on_filters,
    enum_str,
    get_operator_roles,
    fuse_exception_responses,
    get_request_info,
    update_if_changed,
    apply_status_filters,
    apply_type_filters,
)
from app.src.constants import MAX_OPERATORS_PER_COMPANY

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
        description=enum_str(GenderType), default=GenderType.OTHER
    )
    description: str | None = Field(min_length=1, max_length=1024, default=None)
    type: OperatorType = Field(
        description=enum_str(OperatorType),
        default=OperatorType.NORMAL,
    )
    full_name: str | None = Field(min_length=1, max_length=32, default=None)
    status: AccountStatus = Field(
        description=enum_str(AccountStatus), default=AccountStatus.ACTIVE
    )
    phone_number: PhoneNumber | None = Field(
        max_length=32, default=None, description="Phone number in RFC 3966 format"
    )
    email_id: EmailStr | None = Field(
        max_length=256, default=None, description="Email in RFC 5322 format"
    )


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new operator account for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new operator account."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating an operator account."""

    password: str = Field(
        default=None, min_length=8, max_length=32, pattern=PASSWORD_PATTERN
    )
    gender: GenderType = Field(description=enum_str(GenderType), default=None)
    description: str | None = Field(min_length=1, max_length=1024, default=None)
    type: OperatorType = Field(
        description=enum_str(OperatorType),
        default=None,
    )
    full_name: str | None = Field(min_length=1, max_length=32, default=None)
    status: AccountStatus = Field(description=enum_str(AccountStatus), default=None)
    phone_number: PhoneNumber | None = Field(
        max_length=32, default=None, description="Phone number in RFC 3966 format"
    )
    email_id: EmailStr | None = Field(
        max_length=256, default=None, description="Email in RFC 5322 format"
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
    AccountDataFilter,
    UpdatedOnFilter,
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
):
    """Query parameters for operators."""

    search: str | None = Field(Query(default=None))
    description: str | None = Field(Query(default=None))
    type_list: List[OperatorType] | None = Field(
        Query(default=None, description=enum_str(OperatorType))
    )
    status_list: List[AccountStatus] | None = Field(
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
## Functions
# ---------------------------------------------------------------------------
def create_operator_account(session: Session, form_param: CreateForm) -> dict:
    """
    Create a new operator account with the provided form data.
    Also checks if the maximum operator limit per company has been reached before creating the account.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating the operator.

    Returns:
        dict: The created operator data.
    """
    operator_count = (
        session.query(Operator)
        .filter(
            Operator.company_id == form_param.company_id,
        )
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
    operator_data = jsonable_encoder(operator, exclude={Operator.password.name})
    return operator_data


def update_operator(
    session: Session, id: int, form_param: UpdateForm, extra_filter=None
) -> Tuple[bool, dict]:
    """
    Updates an operator account with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the operator to update.
        form_param (UpdateForm): Form data for updating the operator.
        extra_filter (Optional): Additional filter to apply when validating the operator ID.

    Returns:
    Tuple[bool, dict]:
            - bool: True if the operator was modified and the changes were committed.
            - dict: JSON-encoded representation of the updated operator.
    """
    operator = validate_id(
        session,
        Operator,
        id,
        Operator.id,
        extra_filter=extra_filter,
    )
    update_data = form_param.model_dump(exclude_unset=True)
    tokens_revoked = False
    if form_param.status == AccountStatus.SUSPENDED:
        tokens_revoked = (
            session.query(OperatorToken)
            .filter(
                OperatorToken.operator_id == operator.id,
                OperatorToken.is_revoked.is_(False),
            )
            .update({OperatorToken.is_revoked: True})
            > 0
        )

    update_if_changed(operator, update_data)
    have_updates = session.is_modified(operator) or tokens_revoked
    if have_updates:
        session.commit()
        session.refresh(operator)

    operator_data = jsonable_encoder(operator, exclude={Operator.password.name})
    return have_updates, operator_data


def search_operator(session: Session, query_params: QueryParams) -> List[Operator]:
    """
    Search for Operators based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve operators that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Operator]: List of Operators that match the search criteria.
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


def delete_operator(session: Session, operator: Operator) -> dict:
    """
    Delete an Operator and its associated image.

    Args:
        session (Session): SQLAlchemy database session.
        operator (Operator): Operator to delete.

    Returns:
        dict: deleted operator data for logging purposes.
    """
    operator_image = (
        session.query(OperatorImage)
        .filter(OperatorImage.operator_id == operator.id)
        .first()
    )
    operator_data = jsonable_encoder(operator, exclude={Operator.password.name})
    session.delete(operator)
    session.commit()

    if operator_image is not None:
        delete_file(OPERATOR_IMAGES, str(operator_image.id))
    return operator_data


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
        f"""
            **Creates a new operator account.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.operator.create` permission.    
            - Duplicate usernames are not allowed.    
            - By default the user is created in active status.    
            - Maximum `{MAX_OPERATORS_PER_COMPANY}` operators are allowed per company.    
        """
    ),
)
async def create_operator_account_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY_OPERATOR],
        )

        operator_data = create_operator_account(
            session, CreateForm(**form_param.model_dump())
        )
        log_event(token, request_info, operator_data)
        return operator_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_OPERATOR_ACCOUNT}/{{id}}",
    summary="Update operator account",
    tags=["Operator Account"],
    response_model=OperatorSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        """
            **Updates an existing operator account.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.operator.update` permission to update other operators.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_operator_account_for_executive(
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
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR],
        )

        have_updates, operator_data = update_operator(session, id, form_param)
        if have_updates:
            log_event(token, request_info, operator_data)
        return operator_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_OPERATOR_ACCOUNT,
    summary="Fetch operator account",
    tags=["Operator Account"],
    response_model=List[OperatorSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        """
            **Fetches a list of operators.**    
            - Requires a valid access token for authentication.    
            - Common search supports searching by id, username, full_name, designation, phone_number, and email_id.    
        """
    ),
)
async def fetch_operator_accounts_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_operator(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_OPERATOR_ACCOUNT}/{{id}}",
    summary="Delete operator account",
    tags=["Operator Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        """
            **Deletes an existing operator account.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have the `company.operator.delete` permission.    
            - Returns 204 No Content even if the specified account does not exist.    
        """
    ),
)
async def delete_operator_account_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY_OPERATOR],
        )

        operator = session.query(Operator).filter(Operator.id == id).first()
        if operator is not None:
            operator_data = delete_operator(session, operator)
            log_event(token, request_info, operator_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


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
        f"""
            **Creates a new operator account.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.operator.create` permission.    
            - Duplicate usernames are not allowed.    
            - By default the user is created in active status.    
            - Maximum `{MAX_OPERATORS_PER_COMPANY}` operators are allowed per company.    
        """
    ),
)
async def create_operator_account_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_OPERATOR],
        )

        operator_data = create_operator_account(
            session, CreateForm(**form_param.model_dump(), company_id=token.company_id)
        )
        log_event(token, request_info, operator_data)
        return operator_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.patch(
    f"{URL_OPERATOR_ACCOUNT}/{{id}}",
    summary="Update operator account",
    tags=["Account"],
    response_model=OperatorSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        """
            **Updates an existing operator account.**    
            - Requires a valid access token.    
            - Logged-in operator must have `company.operator.update` permission to update other operators.    
            - Operators can update their own account except status.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_operator_account_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        is_self_update = id == token.operator_id
        if not is_self_update:
            roles = get_operator_roles(session, token)
            verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR)

        if is_self_update and form_param.status is not None:
            raise exceptions.NoPermission()

        have_updates, operator_data = update_operator(
            session,
            id,
            form_param,
            extra_filter=(Operator.company_id == token.company_id),
        )
        if have_updates:
            log_event(token, request_info, operator_data)
        return operator_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_OPERATOR_ACCOUNT,
    summary="Fetch operator account",
    tags=["Account"],
    response_model=List[OperatorSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        """
            **Fetches a list of operators.**    
            - Requires a valid access token for authentication.    
            - Common search supports searching by id, username, full_name, designation, phone_number, and email_id.    
        """
    ),
)
async def fetch_operator_accounts_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_operator(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_OPERATOR_ACCOUNT}/{{id}}",
    summary="Delete operator account",
    tags=["Account"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        """
            **Deletes an existing operator account.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have the `company.operator.delete` permission.    
            - Self-deletion is not allowed for safety reasons.    
            - Returns 204 No Content even if the specified account does not exist.    
        """
    ),
)
async def delete_operator_account_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_OPERATOR],
        )

        if token.operator_id == id:
            raise exceptions.NoPermission()
        operator = (
            session.query(Operator)
            .filter(Operator.id == id, Operator.company_id == token.company_id)
            .first()
        )
        if operator is not None:
            operator_data = delete_operator(session, operator)
            log_event(token, request_info, operator_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
