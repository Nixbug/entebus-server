"""
Pydantic schemas used across the EnteBus API.

These models define the structure of request/response payloads
that are reused in multiple endpoints (e.g., error responses, health checks).
"""

from typing import Annotated
from pydantic import BaseModel, model_validator

from app.src.enums import AppID


class RequestInfo(BaseModel):
    """
    Metadata about the incoming HTTP request.

    Attributes:
        method (str): The HTTP method used (GET, POST, etc.).
        path (str): The request path (URL without domain).
        app_id (AppID): Identifier of the application handling the request
            (from AppID enum).
    """

    method: str
    path: str
    app_id: AppID


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


NullableField = Annotated[object, "nullable"]


class PatchForm(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, data: object) -> object:
        if data is None:
            raise ValueError("Request body cannot be null")
        if not isinstance(data, dict):
            return data

        nullable = {
            field_name
            for field_name, field_info in cls.model_fields.items()
            if any(m == "nullable" for m in field_info.metadata)
        }
        null_fields = [k for k, v in data.items() if v is None and k not in nullable]
        if null_fields:
            raise ValueError(f"Fields cannot be set to null: {', '.join(null_fields)}")
        return data
