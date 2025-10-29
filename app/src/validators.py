"""
This module provides helper functions for validating user permissions and tokens.

It also includes examples for usage, making it easier for developers to integrate
these utilities into their projects.
"""

from datetime import datetime, timedelta, timezone
from typing import Type, Union
from sqlalchemy.orm.session import Session

from app.src import exceptions
from app.src.db import (
    ExecutiveRole,
    ExecutiveRoleMap,
    ExecutiveToken,
    OperatorToken,
    VendorToken,
    OperatorRoleMap,
    VendorRoleMap,
    OperatorRole,
    VendorRole,
)


def verify_token(
    session: Session,
    model_cls: Type[Union[ExecutiveToken, OperatorToken, VendorToken]],
    access_token: str,
) -> Union[ExecutiveToken, OperatorToken, VendorToken]:
    """
    Generic token validation function for user.

    Args:
        session (Session): Active SQLAlchemy session
        model_cls (Type[Union[ExecutiveToken, OperatorToken, VendorToken]]): The valid ORM model class.
        access_token (str): The access token string to validate.

    Returns:
        The valid token model instance

    Raises:
        InvalidToken: If token is invalid, revoked, or expired
    """
    # Get token ensuring it's not revoked
    current_time = datetime.now(timezone.utc)
    token = (
        session.query(model_cls)
        .filter(model_cls.access_token == access_token)
        .filter(model_cls.is_revoked == False)
        .first()
    )
    if token is None:
        raise exceptions.InvalidToken()
    token_expires_on = token.created_on + timedelta(seconds=token.expires_in)
    if token_expires_on < current_time:
        raise exceptions.InvalidToken()
    return token


def verify_permission(
    session: Session,
    user_id: int,
    permission_path: str,
    role_model_cls: Type[Union[ExecutiveRole, OperatorRole, VendorRole]],
    role_map_model_cls: Type[Union[ExecutiveRoleMap, OperatorRoleMap, VendorRoleMap]],
):
    """
    Generic permission checker for Executive, Operator, or Vendor users.

    Args:
        session (Session): Active SQLAlchemy session.
        user_id (int): The ID of the user to check
        permission_path (str): Dot path to check (e.g., "executive.token.delete")
        role_model_cls (Type): Role model class (e.g., ExecutiveRole)
        role_map_model_cls (Type): Role mapping class (e.g., ExecutiveRoleMap)

    Raises:
        NoPermission: If permission is False
    """

    role_ids = (
        session.query(role_map_model_cls.role_id)
        .filter(role_map_model_cls.executive_id == user_id)
        .all()
    )
    role_ids = [r[0] for r in role_ids]
    if not role_ids:
        raise exceptions.NoPermission()

    roles = (
        session.query(role_model_cls.permissions)
        .filter(role_model_cls.id.in_(role_ids))
        .all()
    )

    keys = permission_path.split(".")
    for (permissions_dict,) in roles:
        current = permissions_dict
        try:
            for key in keys:
                current = current[key]
            if current is True:
                return True
        except (KeyError, TypeError):
            continue

    raise exceptions.NoPermission()
