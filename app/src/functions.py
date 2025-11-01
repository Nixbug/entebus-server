"""
This module provides helper functions commonly used across FastAPI routes.

It offers reusable utilities that make it easier for developers to integrate them into their projects.
"""

from enum import Enum
from typing import Any, List, Dict, Type, Union, Tuple
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import Column, asc, desc
from sqlalchemy.orm.session import Session

from app.src import schemas, exceptions
from app.src.db import (
    ExecutiveRole,
    ExecutiveRoleMap,
    ExecutiveToken,
    OperatorRole,
    OperatorRoleMap,
    OperatorToken,
    VendorRole,
    VendorRoleMap,
    VendorToken,
)


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
        model_cls Type[Union[ExecutiveToken, OperatorToken, VendorToken]]: The ORM model class.
        filter_condition (Column): SQLAlchemy filter condition.
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


def get_executive_roles(
    session: Session,
    executive_id: int,
) -> list[ExecutiveRole]:
    """
    Retrieve all roles assigned to a specific executive.

    Args:
        session (Session): Active SQLAlchemy session.
        executive_id (int): The ID of the executive.

    Returns:
        list[ExecutiveRole]: List of ExecutiveRole objects assigned to the executive.
                             Returns an empty list if no roles are found.
    """
    return (
        session.query(ExecutiveRole)
        .join(ExecutiveRoleMap, ExecutiveRole.id == ExecutiveRoleMap.role_id)
        .filter(ExecutiveRoleMap.executive_id == executive_id)
        .all()
    )


def get_vendor_roles(
    session: Session,
    vendor_id: int,
) -> list[VendorRole]:
    """
    Retrieve all roles assigned to a specific vendor.

    Args:
        session (Session): Active SQLAlchemy session.
        vendor_id (int): The ID of the vendor.

    Returns:
        list[VendorRole]: List of VendorRole objects assigned to the vendor.
                          Returns an empty list if no roles are found.
    """
    return (
        session.query(VendorRole)
        .join(VendorRoleMap, VendorRole.id == VendorRoleMap.role_id)
        .filter(VendorRoleMap.vendor_id == vendor_id)
        .all()
    )


def get_operator_roles(
    session: Session,
    operator_id: int,
) -> list[OperatorRole] | None:
    """
    Retrieve all roles assigned to a specific operator.

    Args:
        session (Session): Active SQLAlchemy session.
        operator_id (int): The ID of the operator.

    Returns:
        list[OperatorRole]: List of OperatorRole objects assigned to the operator.
                            Returns an empty list if no roles are found.
    """
    return (
        session.query(OperatorRole)
        .join(OperatorRoleMap, OperatorRole.id == OperatorRoleMap.role_id)
        .filter(OperatorRoleMap.operator_id == operator_id)
        .all()
    )


def get_by_path(data: dict, path: str) -> Any:
    """
    Retrieve a nested value from a dictionary using a dot-separated key path.

    Args:
        data (dict): The dictionary to traverse.
        path (str): Dot-separated string representing the path.

    Returns:
        Any: The value at the specified path.
    """
    for key in path.split("."):
        data = data[key]
    return data
