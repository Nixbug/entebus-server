"""
Centralized exception handling for EnteBus API.

This module provides:
- Base APIException class extending FastAPI's HTTPException.
- Custom domain-specific exceptions with appropriate status codes and headers.
- Utility functions for formatting DB errors, logging, and routing exceptions.

Usage:
    - Raise specific exceptions in route handlers or services.
    - Use `handle()` to normalize raw exceptions (DB, Redis, Pydantic) into API-friendly responses.
"""

from traceback import format_exception
from logging import getLogger
from fastapi import status, HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from psycopg2.errorcodes import UNIQUE_VIOLATION, FOREIGN_KEY_VIOLATION
from pydantic import ValidationError
from redis.exceptions import RedisError
from requests.exceptions import ConnectionError, Timeout


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
def format_integrity_error(e: Exception) -> str:
    """
    Convert a raw SQL IntegrityError into a clean, user-friendly message.
    """
    if isinstance(e, IntegrityError) and hasattr(e.orig, "diag") and e.orig.diag:
        message = getattr(e.orig.diag, "message_detail", None)
        if not message:
            message = str(e.orig)
    else:
        message = str(getattr(e, "orig", e))
    if "\n" in message:
        message = message.split("\n")[0]
    cleaned = message.translate({ord(i): None for i in '\\"\\.\\(\\)'})
    cleaned = cleaned.replace("Key ", "For ").replace("=", " value ")
    return cleaned


def log_exception(e: Exception) -> None:
    """
    Log an exception with traceback using Uvicorn's error logger.
    """
    logger = getLogger("uvicorn.error")
    logger.error("".join(format_exception(type(e), e, e.__traceback__)))


# ---------------------------------------------------------------------------
# Base Exception
# ---------------------------------------------------------------------------
class APIException(HTTPException):
    """
    Base class for all application-specific exceptions.

    Provides default handling of status_code, detail, and headers.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    detail = None
    headers = None

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("status_code", self.status_code)
        kwargs.setdefault("detail", self.detail)
        kwargs.setdefault("headers", self.headers)
        super().__init__(*args, **kwargs)


# ---------------------------------------------------------------------------
# Exception handling entrypoint
# ---------------------------------------------------------------------------
def handle(e: Exception) -> None:
    """
    Normalize and re-raise exceptions as API-friendly errors.

    Converts raw exceptions from DB, Pydantic, Redis, etc. into
    corresponding APIException subclasses.
    """
    if isinstance(e, IntegrityError):
        sqlstate = getattr(e.orig.diag, "sqlstate", None)
        if sqlstate == UNIQUE_VIOLATION:
            raise UniqueViolation(format_integrity_error(e))
        if sqlstate == FOREIGN_KEY_VIOLATION:
            raise ForeignKeyViolation(format_integrity_error(e))
    if isinstance(e, ProgrammingError):
        raise DatabaseError(detail=format_integrity_error(e))
    if isinstance(e, ValidationError):
        raise PydanticError(detail=e.errors())
    if isinstance(e, RedisError):
        raise RedisDBError(detail=str(e))
    if isinstance(e, (OperationalError, ConnectionError, Timeout)):
        raise NetworkError(detail=str(e))
    # Log and raise an unhandled exception
    if isinstance(e, APIException):
        raise e

    log_exception(e)
    raise e


# ---------------------------------------------------------------------------
# Exception Classes
# ---------------------------------------------------------------------------
class PydanticError(APIException):
    """
    Raised when a Pydantic validation error occurs.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    headers = {"X-Error": "PydanticError"}

    def __init__(self, detail: str):
        super().__init__(detail=detail)


class UniqueViolation(APIException):
    """
    Raised when a unique constraint is violated.
    """

    status_code = status.HTTP_409_CONFLICT
    headers = {"X-Error": "UniqueViolation"}

    def __init__(self, detail: str):
        super().__init__(detail=detail)


class ForeignKeyViolation(APIException):
    """
    Raised when a foreign key constraint is violated.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    headers = {"X-Error": "ForeignKeyViolation"}

    def __init__(self, detail: str):
        super().__init__(detail=detail)


class RedisDBError(APIException):
    """
    Raised when a Redis database operation fails.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    headers = {"X-Error": "RedisDBError"}

    def __init__(self, detail: str):
        super().__init__(detail=detail)


class NetworkError(APIException):
    """
    Raised when a network operation fails.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    headers = {"X-Error": "NetworkError"}

    def __init__(self, detail: str):
        super().__init__(detail=detail)


class DatabaseError(APIException):
    """
    Raised when a database operation fails.
    """

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    headers = {"X-Error": "DatabaseError"}

    def __init__(self, detail: str):
        super().__init__(detail=detail)
