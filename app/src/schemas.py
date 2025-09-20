"""
Pydantic schemas used across the EnteBus API.

These models define the structure of request/response payloads
that are reused in multiple endpoints (e.g., error responses, health checks).
"""

from pydantic import BaseModel


class RequestInfo(BaseModel):
    """
    Metadata about the incoming HTTP request.

    Attributes:
        method (str): The HTTP method used (GET, POST, etc.).
        path (str): The request path (URL without domain).
        app_id (int): Identifier of the application handling the request
            (from AppID enum).
    """

    method: str
    path: str
    app_id: int


class HealthStatus(BaseModel):
    """
    Schema for health check responses.

    Attributes:
        status (str): Current health status of the API.
        version (str): Current version of the API.
    """

    status: str
    version: str


class ErrorResponse(BaseModel):
    """
    Standardized error response schema.

    Attributes:
        detail (str): Human-readable error message describing the problem.
    """

    detail: str
