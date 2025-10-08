"""
This module provides helper functions commonly used across FastAPI routes and
services:

- `get_request_info`: Extracts key metadata from incoming FastAPI requests for
  logging, auditing, or contextual processing.
- `fuse_exception_responses`: Generates OpenAPI-compatible response documentation
  by merging multiple `APIException` instances.
- `enum_str`: Converts an Enum class into a human-readable string representation.

It also includes examples for usage, making it easier for developers to integrate
these utilities into their projects.
"""

from typing import List, Dict
from fastapi import Request

from app.src import schemas
from app.src.exceptions import APIException


def get_request_info(request: Request) -> schemas.RequestInfo:
    """
    Extract request metadata and return it as a `RequestInfo` schema.

    This function pulls essential information about the incoming request,
    including the HTTP method, request path, and the `app_id` stored in the
    application state. It is typically used for logging, auditing, or
    generating contextual information about requests.

    Args:
        request (Request): The incoming FastAPI request object.

    Returns:
        schemas.RequestInfo: A dictionary (pydantic model) containing:
            - method (str): The HTTP method of the request (e.g., GET, POST).
            - path (str): The request URL path (e.g., "/landmark").
            - app_id (int): The application identifier (e.g., AppID.EXECUTIVE, AppID.VENDOR)
    """
    app_id: int = request.scope["app"].state.id
    return {"method": request.method, "path": request.url.path, "app_id": app_id}


def fuse_exception_responses(exceptions: List[APIException]) -> Dict[int, dict]:
    """
    Generate OpenAPI response documentation by fusing multiple APIException instances.

    Args:
        exceptions (List[APIException]): List of instantiated exceptions.

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
