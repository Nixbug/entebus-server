"""
This module provides helper functions for validating.

It also includes examples for usage, making it easier for developers to integrate
these utilities into their projects.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Type, Union
from sqlalchemy.orm.session import Session

from app.src import argon2, exceptions
from app.src.enums import AccountStatus, GrantType
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


def authenticate_user(
    session: Session,
    model_cls: Type[Union[ExecutiveToken, OperatorToken, VendorToken]],
    form_param: Any,
) -> Union[ExecutiveToken, OperatorToken, VendorToken]:
    """
    Generic user authentication function for Executive, Operator, Vendor.

    This generic function handles authentication for different account types.
    It validates the username, password and ensures the account is active.
    Authenticate a user using the grant type.

    Args:
        session (Session): Active SQLAlchemy session.
        model_cls Type[Union[ExecutiveToken, OperatorToken, VendorToken]]: The valid ORM model class.
        form_param (Any): Form parameters containing username, password, and grant_type.

    Returns:
        user: The valid user object from the database.

    Raises:
        InvalidGrantType: If the grant_type is not PASSWORD.
        InvalidCredentials: If the username or password is invalid.
        InactiveAccount: If the user account is not active.
    """
    if form_param.grant_type != GrantType.PASSWORD:
        raise exceptions.InvalidGrantType()
    user = (
        session.query(model_cls)
        .filter(model_cls.username == form_param.username)
        .first()
    )
    if user is None:
        raise exceptions.InvalidCredentials()
    if not argon2.check_password(form_param.password, user.password):
        raise exceptions.InvalidCredentials()
    if user.status != AccountStatus.ACTIVE:
        raise exceptions.InactiveAccount()
    return user


def validate_and_revoke_refresh_token(
    session: Session,
    model_cls: Type[Union[ExecutiveToken, OperatorToken, VendorToken]],
    form_param: Any,
) -> Union[ExecutiveToken, OperatorToken, VendorToken]:
    """
    Validates a refresh token and revokes it.

    This function ensures the provided refresh token exists, is valid,
    not revoked, and not expired. Once validated, the token is revoked
    to prevent reuse. It can be used across different token models
    (ExecutiveToken, OperatorToken, VendorToken).

    Args:
        session (Session): Active SQLAlchemy session.
        model_cls Type[Union[ExecutiveToken, OperatorToken, VendorToken]]: The valid ORM model class.
        form_param (Any): Form parameters containing refresh_token and grant_type.

    Returns:
        token: The valid token object from the database.

    Raises:
        InvalidGrantType: If the grant_type is not REFRESH_TOKEN.
        InvalidToken: If the token does not exist, is revoked, or has expired.
    """
    if form_param.grant_type != GrantType.REFRESH_TOKEN:
        raise exceptions.InvalidGrantType()
    token = (
        session.query(model_cls)
        .filter(model_cls.refresh_token == form_param.refresh_token)
        .first()
    )
    if token is None or token.is_revoked:
        raise exceptions.InvalidToken()
    # TODO: Optionally suspend account if revoked token reuse detected
    if token.refresh_before < datetime.now(timezone.utc):
        raise exceptions.InvalidToken()
    # Revoke the current token
    token.is_revoked = True
    session.flush()
    return token


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
