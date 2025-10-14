"""
This module provides helper functions commonly used across FastAPI routes and
services.

It also includes examples for usage, making it easier for developers to integrate
these utilities into their projects.
"""

from datetime import datetime, timezone
from typing import List, Dict
from fastapi import Request
from sqlalchemy.orm.session import Session

from app.src import schemas, exceptions
from app.src.db import ExecutiveToken


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


def validate_executive_token(access_token: str, session: Session) -> ExecutiveToken:
    """
    Validate an executive access token.

    Args:
        access_token (str): The token string to validate.
        session (Session): Active SQLAlchemy session for DB lookup.

    Returns:
        ExecutiveToken: The valid token object from the database.

    Raises:
        exceptions.InvalidToken: If the token is not found or has expired.
    """
    current_time = datetime.now(timezone.utc)
    token = (
        session.query(ExecutiveToken)
        .filter(
            ExecutiveToken.access_token == access_token,
            ExecutiveToken.expires_at > current_time,
        )
        .first()
    )
    if token is None:
        raise exceptions.InvalidToken()

    return token


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


def enum_str(enum_class) -> str:
    """
    Convert an Enum class into a comma-separated string of its members.

    Each enum member is formatted as "<NAME>: <VALUE>".

    Args:
        enumClass (Type[Enum]): The Enum class to be stringified.

    Returns:
        str: A human-readable string representation of the enum members.
    """
    return ", ".join(f"{x.name}: {x.value}" for x in enum_class)
