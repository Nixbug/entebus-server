"""
Centralized exception handling for EnteBus API.

This module defines a unified approach to managing application errors by
providing custom exception classes, formatting utilities, and a central
`handle()` function to normalize and re-raise errors from various sources
(e.g., database, Redis, Pydantic, network).

It ensures consistent error responses across the API.
"""

from traceback import format_exception
from logging import getLogger
from typing import TYPE_CHECKING, NoReturn
from fastapi import status, HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from psycopg2.errorcodes import UNIQUE_VIOLATION, FOREIGN_KEY_VIOLATION
from pydantic import ValidationError
from redis.exceptions import RedisError
from requests.exceptions import ConnectionError, Timeout
from sqlalchemy.orm import InstrumentedAttribute

if TYPE_CHECKING:
    from app.src.db import ORMbase
else:
    # Runtime placeholder to avoid importing app.src.db and initializing DB engine.
    class ORMbase:  # pragma: no cover
        pass


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
def handle(e: Exception) -> NoReturn:
    """
    Normalize and re-raise exceptions as API-friendly errors.

    Converts raw exceptions from DB, Pydantic, Redis, etc. into
    corresponding APIException subclasses.
    """
    if isinstance(e, IntegrityError):
        sqlstate = getattr(e.orig.diag, "sqlstate", None)
        if sqlstate == UNIQUE_VIOLATION:
            raise UniqueViolation(format_integrity_error(e))
        elif sqlstate == FOREIGN_KEY_VIOLATION:
            raise ForeignKeyViolation(format_integrity_error(e))
        else:
            raise DatabaseError(detail=format_integrity_error(e))
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


class InvalidCredentials(APIException):
    """
    Raised when invalid username or password is provided.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    detail = "Invalid username or password"
    headers = {"X-Error": "InvalidCredentials"}


class InactiveAccount(APIException):
    """
    Raised when account is not in active status.
    """

    status_code = status.HTTP_412_PRECONDITION_FAILED
    detail = "The account is not in active status"
    headers = {"X-Error": "InactiveAccount"}


class InvalidToken(APIException):
    """
    Raised when an invalid token is provided.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    detail = "Invalid token"
    headers = {"X-Error": "InvalidToken"}


class UnknownValue(APIException):
    """
    Raised when an unknown id or value is provided.

    Accepts either an ORM `InstrumentedAttribute` (preferred) or a plain
    string column name for convenience when callers only have the name.
    """

    status_code = status.HTTP_404_NOT_FOUND
    headers = {"X-Error": "UnknownValue"}

    def __init__(self, column: InstrumentedAttribute | str):
        column_name = column if isinstance(column, str) else column.key
        detail = f"Unknown {column_name} is provided"
        super().__init__(detail=detail)


class InvalidValue(APIException):
    """
    Raised when an invalid id or value is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    headers = {"X-Error": "InvalidValue"}

    def __init__(self, column: InstrumentedAttribute | str):
        column_name = column if isinstance(column, str) else column.key
        detail = f"Invalid {column_name} is provided"
        super().__init__(detail=detail)


class InvalidStateTransition(APIException):
    """
    Raised when an attempted state transition is not permitted.
    """

    status_code = status.HTTP_409_CONFLICT
    headers = {"X-Error": "InvalidStateTransition"}

    def __init__(self, column: InstrumentedAttribute):
        detail = f"The {column.name} cannot be set to the provided value"
        super().__init__(detail=detail)


class MissingParameter(APIException):
    """
    Raised when a required parameter is missing from the request.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    headers = {"X-Error": "MissingParameter"}

    def __init__(self, column: InstrumentedAttribute):
        detail = f"The {column.name} is missing"
        super().__init__(detail=detail)


class UnexpectedParameter(APIException):
    """Raised when an unexpected parameter is provided in the request."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    headers = {"X-Error": "UnexpectedParameter"}

    def __init__(self, column: InstrumentedAttribute):
        detail = f"Unexpected parameter {column.name} is provided"
        super().__init__(detail=detail)


class InactiveResource(APIException):
    """
    Raised when a resource is not in an active or useful state.
    """

    status_code = status.HTTP_412_PRECONDITION_FAILED
    headers = {"X-Error": "InactiveResource"}

    def __init__(self, orm_class: type[ORMbase]):
        detail = (
            f"The status of {orm_class.__name__} is not in an active or useful state"
        )
        super().__init__(detail=detail)


class DataInUse(APIException):
    """
    Raised when trying to delete a resource that is currently in use or not in a deletable state.
    """

    status_code = status.HTTP_409_CONFLICT
    headers = {"X-Error": "DataInUse"}

    def __init__(self, orm_class: type[ORMbase]):
        detail = f"The {orm_class.__name__} is currently in use"
        super().__init__(detail=detail)


class LimitExceeded(APIException):
    """
    Raised when the number of entries in a table reaches the allowed maximum.
    """

    status_code = status.HTTP_409_CONFLICT
    headers = {"X-Error": "LimitExceeded"}

    def __init__(self, orm_class: type[ORMbase]):
        detail = (
            f"The number of entries in {orm_class.__name__} exceeds the allowed limit."
        )
        super().__init__(detail=detail)


class NoPermission(APIException):
    """
    Raised when a user does not have permission to perform an action.
    """

    status_code = status.HTTP_403_FORBIDDEN
    detail = "This user has no permission to perform this action"
    headers = {"X-Error": "NoPermission"}


class InvalidGrantType(APIException):
    """
    Raised when an invalid grant type is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Invalid grant type"
    headers = {"X-Error": "InvalidGrantType"}


class InvalidImageFile(APIException):
    """
    Raised when an invalid image file is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    headers = {"X-Error": "InvalidImageFile"}
    detail = "Invalid image provided"


class InvalidWKTStringOrType(APIException):
    """
    Raised when an invalid WKT string or type is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Invalid WKT string or type"
    headers = {"X-Error": "InvalidWKTStringOrType"}


class InvalidSRID4326(APIException):
    """
    Raised when the SRID of a geometry is not 4326.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Coordinates are outside valid WGS84 (SRID 4326) bounds"
    headers = {"X-Error": "InvalidSRID4326"}


class InvalidAABB(APIException):
    """
    Raised when the geometry is not a valid Axis-Aligned Bounding Box.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "The geometry is not a valid Axis-Aligned Bounding Box"
    headers = {"X-Error": "InvalidAABB"}


class InvalidRRULEString(APIException):
    """
    Raised when an invalid RRULE string is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Invalid RRULE string"
    headers = {"X-Error": "InvalidRRULEString"}


class OverlappingLandmarkBoundary(APIException):
    """
    Raised when a landmark boundary overlaps with another landmark boundary.
    """

    status_code = status.HTTP_409_CONFLICT
    detail = "Boundary overlaps with another landmark's boundary"
    headers = {"X-Error": "OverlappingLandmarkBoundary"}


class OverlappingService(APIException):
    """
    Raised when a vehicle is already assigned to another service
    whose time window overlaps with the requested service time.
    """

    status_code = status.HTTP_409_CONFLICT
    detail = "Vehicle is already assigned to another service during the requested time"
    headers = {"X-Error": "OverlappingService"}


class InvalidBoundaryArea(APIException):
    """
    Raised when the area of a landmark boundary is not within the prescribed limits.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Boundary area not within the prescribed limits"
    headers = {"X-Error": "InvalidBoundaryArea"}


class StationOutsideLandmark(APIException):
    """
    Raised when the station location is not within the landmark boundary.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "The station location is not within the landmark boundary"
    headers = {"X-Error": "StationOutsideLandmark"}


class LandmarkDistanceLimitExceeded(APIException):
    """
    Raised when the updated landmark boundary is beyond the allowed distance limit.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Landmark centroid movement exceeds allowed limit"
    headers = {"X-Error": "LandmarkDistanceLimitExceeded"}


class InvalidFareVersion(APIException):
    """
    Raised when an invalid fare version is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Invalid dynamic fare version"
    headers = {"X-Error": "InvalidFareVersion"}


class InvalidFareFunction(APIException):
    """
    Raised when an invalid fare function is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Invalid fare function"
    headers = {"X-Error": "InvalidFareFunction"}


class InvalidTicketVersion(APIException):
    """
    Raised when an invalid ticket version is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Invalid ticket version"
    headers = {"X-Error": "InvalidTicketVersion"}


class InvalidDigitalTicket(APIException):
    """
    Raised when an invalid digital ticket is provided.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Invalid digital ticket"
    headers = {"X-Error": "InvalidDigitalTicket"}


class JSTimeLimitExceeded(APIException):
    """
    Raised when JavaScript execution exceeds the time limit.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "JavaScript execution timed out"
    headers = {"X-Error": "JSTimeLimitExceeded"}


class JSMemoryLimitExceeded(APIException):
    """
    Raised when JavaScript execution exceeds the memory limit.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "JavaScript memory limit exceeded"
    headers = {"X-Error": "JSMemoryLimitExceeded"}


class UnknownTicketType(APIException):
    """
    Raised when an unknown ticket type is provided to the fare function.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "Unknown ticket type"
    headers = {"X-Error": "UnknownTicketType"}


class LockAcquireTimeout(APIException):
    """
    Raised when a Redis lock cannot be acquired within the specified timeout.
    """

    status_code = status.HTTP_423_LOCKED
    detail = "Lock acquisition timed out"
    headers = {"X-Error": "LockAcquireTimeout"}
