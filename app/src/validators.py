"""
Validation and permission checks for EnteBus API.

This module centralizes guard logic such as:
- Token validation

All functions raise appropriate exceptions from `app.src.exceptions`
when validation fails, ensuring consistent error handling.
"""

from datetime import datetime, timezone
from sqlalchemy.orm.session import Session
from typing import Type
from sqlalchemy.orm import DeclarativeMeta

from app.src.db import ExecutiveToken
from app.src import exceptions
from app.src import exceptions


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------
def _validate_token(
    model_cls: Type[DeclarativeMeta], access_token: str, session: Session
) -> DeclarativeMeta:
    """
    Generic token validator for any token model.

    Args:
        model_cls: The SQLAlchemy model class (e.g., ExecutiveToken).
        access_token (str): The bearer token string provided by the client.
        session (Session): Active SQLAlchemy session for DB lookup.

    Returns:
        model_cls: The valid token object from the database.

    Raises:
        exceptions.InvalidToken: If the token is not found or has expired.
    """
    current_time = datetime.now(timezone.utc)

    token = (
        session.query(model_cls)
        .filter(
            model_cls.access_token == access_token,
            model_cls.expires_at > current_time,
        )
        .first()
    )

    if token is None:
        raise exceptions.InvalidToken()

    return token


def executive_token(access_token: str, session: Session) -> ExecutiveToken:
    """Validate an executive access token."""
    return _validate_token(ExecutiveToken, access_token, session)
