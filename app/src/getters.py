"""
Request Metadata Utility.

This module provides a helper function to extract key metadata
from an incoming FastAPI request, such as the HTTP method, path,
and the application ID associated with the request.

The extracted data is returned as a `RequestInfo` Pydantic model,
which is useful for logging, auditing, and analytics.

"""

from fastapi import Request

from app.src import schemas


def request_info(request: Request) -> schemas.RequestInfo:
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
