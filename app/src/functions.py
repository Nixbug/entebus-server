"""
This module provides helper functions commonly used across FastAPI routes and
services.

It also includes examples for usage, making it easier for developers to integrate
these utilities into their projects.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Dict, Type, Union, Tuple
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import Column, asc, desc
from sqlalchemy.orm.session import Session

from app.src import argon2, schemas, exceptions
from app.src.db import ExecutiveToken, OperatorToken, VendorToken
from app.src.enums import AccountStatus, GrantType


def get_request_info(request: Request) -> schemas.RequestInfo:
    """
    Extract metadata about the incoming request.

    This function retrieves essential request information — HTTP method,
    path, and associated application ID — and returns it as a
    `RequestInfo` Pydantic model for structured use across the system.

    Args:
        request (Request): FastAPI request object.

    Returns:
        schemas.RequestInfo: Pydantic model containing:
            - method (str): HTTP method (GET, POST, etc.).
            - path (str): Path portion of the request URL.
            - app_id (int): Application ID from app state.
    """
    return schemas.RequestInfo(
        method=request.method,
        path=request.url.path,
        app_id=request.scope["app"].state.id,
    )


def fuse_exception_responses(
    exceptions: List[exceptions.APIException],
) -> Dict[int, dict]:
    """
    Generate OpenAPI response documentation by fusing multiple APIException instances.

    Args:
        exceptions (List[exceptions.APIException]): List of instantiated exceptions.

    Returns:
        Dict[int, dict]: A dictionary of OpenAPI response specs grouped by status code.
    """
    responses = {}

    for exception in exceptions:
        status_code = exception.status_code
        example_key = type(exception).__name__
        example_value = {
            "summary": str(exception.headers),
            "value": {"detail": exception.detail},
        }

        if status_code not in responses:
            responses[status_code] = {
                "model": schemas.ErrorResponse,
                "content": {
                    "application/json": {"examples": {example_key: example_value}}
                },
            }
        else:
            responses[status_code]["content"]["application/json"]["examples"][
                example_key
            ] = example_value

    return responses


def enum_str(enum_class: Type[Enum]) -> str:
    """
    Convert an Enum class into a comma-separated string of its members.

    Each enum member is formatted as "<NAME>: <VALUE>".

    Args:
        enumClass (Type[Enum]): The Enum class to be stringified.

    Returns:
        str: A human-readable string representation of the enum members.
    """
    return ", ".join(f"{x.name}: {x.value}" for x in enum_class)


def cleanup_old_tokens(
    session: Session,
    model_cls: Type[Union[ExecutiveToken, OperatorToken, VendorToken]],
    filter_condition: Column,
    max_tokens: int,
) -> None:
    """
    Remove excess tokens for a given entity, retaining only the most recent valid ones.

    This function enforces a maximum number of active tokens per entity (executive,
    operator, vendor.) and deletes older ones beyond the specified limit.
    Tokens are ordered such that revoked tokens are prioritized for deletion.

    Args:
        session (Session): Active SQLAlchemy session.
        model_cls Type[Union[ExecutiveToken, OperatorToken, VendorToken]]: The valid ORM model class.
        filter_condition (Column): SQLAlchemy filter condition (e.g., ExecutiveToken.executive_id == id).
        max_tokens (int): The maximum number of tokens allowed.

    Returns:
        None
    """
    tokens = (
        session.query(model_cls)
        .filter(filter_condition)
        .order_by(desc(model_cls.is_revoked), asc(model_cls.created_on))
        .all()
    )
    # Remove oldest tokens if we exceed max_tokens
    while len(tokens) > max_tokens:
        token_to_delete = tokens.pop(0)
        session.delete(token_to_delete)
        session.flush()


def authenticate_user(
    session: Session,
    model_cls: Type[Union[ExecutiveToken, OperatorToken, VendorToken]],
    form_param: Any,
) -> Union[ExecutiveToken, OperatorToken, VendorToken]:
    """
    Generic user authentication function for Executive, Operator, Vendor.

    This generic function handles authentication for different account types.
    It validates the username, password and ensures the account is active.
    Authenticate a user using the grant type.

    Args:
        session (Session): Active SQLAlchemy session.
        model_cls Type[Union[ExecutiveToken, OperatorToken, VendorToken]]: The valid ORM model class.
        form_param (Any): Form parameters containing username, password, and grant_type.

    Returns:
        user: The valid user object from the database.

    Raises:
        InvalidGrantType: If the grant_type is not PASSWORD.
        InvalidCredentials: If the username or password is invalid.
        InactiveAccount: If the user account is not active.
    """
    if form_param.grant_type != GrantType.PASSWORD:
        raise exceptions.InvalidGrantType()
    user = (
        session.query(model_cls)
        .filter(model_cls.username == form_param.username)
        .first()
    )
    if user is None:
        raise exceptions.InvalidCredentials()
    if not argon2.check_password(form_param.password, user.password):
        raise exceptions.InvalidCredentials()
    if user.status != AccountStatus.ACTIVE:
        raise exceptions.InactiveAccount()
    return user


def validate_and_revoke_refresh_token(
    session: Session,
    model_cls: Type[Union[ExecutiveToken, OperatorToken, VendorToken]],
    form_param: Any,
) -> Union[ExecutiveToken, OperatorToken, VendorToken]:
    """
    Validates a refresh token and revokes it.

    This function ensures the provided refresh token exists, is valid,
    not revoked, and not expired. Once validated, the token is revoked
    to prevent reuse. It can be used across different token models
    (ExecutiveToken, OperatorToken, VendorToken).

    Args:
        session (Session): Active SQLAlchemy session.
        model_cls Type[Union[ExecutiveToken, OperatorToken, VendorToken]]: The valid ORM model class.
        form_param (Any): Form parameters containing refresh_token and grant_type.

    Returns:
        token: The valid token object from the database.

    Raises:
        InvalidGrantType: If the grant_type is not REFRESH_TOKEN.
        InvalidToken: If the token does not exist, is revoked, or has expired.
    """
    if form_param.grant_type != GrantType.REFRESH_TOKEN:
        raise exceptions.InvalidGrantType()
    token = (
        session.query(model_cls)
        .filter(model_cls.refresh_token == form_param.refresh_token)
        .first()
    )
    if token is None or token.is_revoked:
        raise exceptions.InvalidToken()
    # TODO: Optionally suspend account if revoked token reuse detected
    if token.refresh_before < datetime.now(timezone.utc):
        raise exceptions.InvalidToken()
    # Revoke the current token
    token.is_revoked = True
    session.flush()
    return token


def token_to_json(
    token: Union[ExecutiveToken, OperatorToken, VendorToken],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Convert a token object into two JSON-compatible dicts.

    Args:
        token (Union[ExecutiveToken, OperatorToken, VendorToken]): Token model instance.

    Returns:
        Tuple[Dict[str, Any], Dict[str, Any]]:
            - token_data: the full JSON-encoded token.
            - token_log_data: same as token_data but with sensitive fields removed.
    """
    token_data = jsonable_encoder(token)
    token_log_data = token_data.copy()
    for sensitive_field in ("access_token", "refresh_token"):
        token_log_data.pop(sensitive_field, None)
    return token_data, token_log_data
