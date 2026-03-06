"""
This module provides helper functions for validation.

It includes reusable utilities to handle common operations,
making it easier for developers to integrate them into their projects.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Type, Union
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm.session import Session

from app.src.functions import get_by_path
from app.src import argon2, exceptions
from app.src.enums import AccountStatus, BusinessStatus, CompanyStatus, GrantType
from app.src.db import (
    Executive,
    ExecutiveRole,
    ExecutiveToken,
    Operator,
    OperatorToken,
    Vendor,
    VendorToken,
    OperatorRole,
    VendorRole,
    Company,
    Business,
)


def user_credentials(
    user: Union[Executive, Operator, Vendor],
    credentials: OAuth2PasswordRequestForm,
) -> Union[Executive, Operator, Vendor]:
    """
    Generic user authentication function for Executive, Operator, and Vendor.

    This function assumes the user has already been fetched from the database.
    It validates the grant_type, verifies the provided password, and ensures
    the account is active.

    Args:
        user (Union[Executive, Operator, Vendor]): The already fetched user instance.
        credentials (OAuth2PasswordRequestForm): Credentials containing password, and grant_type.

    Returns:
        Union[Executive, Operator, Vendor]: The authenticated user instance.

    Raises:
        InvalidGrantType: If the grant_type is not PASSWORD.
        InvalidCredentials: If password is invalid.
        InactiveAccount: If the user account is not active.
    """
    if credentials.grant_type != GrantType.PASSWORD:
        raise exceptions.InvalidGrantType()
    if not argon2.check_password(credentials.password, user.password):
        raise exceptions.InvalidCredentials()
    if user.status != AccountStatus.ACTIVE:
        raise exceptions.InactiveAccount()
    return user


def authenticate_executive(
    session: Session,
    credentials: OAuth2PasswordRequestForm,
) -> Executive:
    """
    Authenticate an Executive by username.

    Args:
        session (Session): Active SQLAlchemy session.
        credentials (OAuth2PasswordRequestForm): Credentials containing username, password and grant_type.

    Returns:
        Executive: The authenticated executive instance.

    Raises:
        InvalidCredentials: If the username is not found or credentials are invalid.
        InvalidGrantType: If credentials.grant_type is not GrantType.PASSWORD.
        InactiveAccount: If the executive account is not ACTIVE.
    """
    executive = (
        session.query(Executive)
        .filter(Executive.username == credentials.username)
        .first()
    )
    if executive is None:
        raise exceptions.InvalidCredentials()
    return user_credentials(executive, credentials)


def authenticate_operator(
    session: Session,
    credentials: OAuth2PasswordRequestForm,
    form_param: Any,
) -> Operator:
    """
    Authenticate an Operator by username and company_id.

    Args:
        session (Session): Active SQLAlchemy session.
        credentials (OAuth2PasswordRequestForm): Credentials containing username, password and grant_type.
        form_param (Any): Form parameters containing company_id.

    Returns:
        Operator: The authenticated Operator instance.

    Raises:
        InvalidCredentials: If the username/company lookup or password validation fails.
        InvalidGrantType: If credentials.grant_type is not GrantType.PASSWORD.
        InactiveAccount: If the company or operator account is suspended.
        UnknownValue: If the provided company_id does not exist.
    """
    company = session.query(Company).filter(Company.id == form_param.company_id).first()
    if company is None:
        raise exceptions.UnknownValue(Operator.company_id)
    if company.status == CompanyStatus.SUSPENDED:
        raise exceptions.InactiveAccount()

    operator = (
        session.query(Operator)
        .filter(
            Operator.username == credentials.username,
            Operator.company_id == form_param.company_id,
        )
        .first()
    )
    if operator is None:
        raise exceptions.InvalidCredentials()
    if operator.status == AccountStatus.SUSPENDED:
        raise exceptions.InactiveAccount()
    return user_credentials(operator, credentials)


def authenticate_vendor(
    session: Session,
    credentials: OAuth2PasswordRequestForm,
    form_param: Any,
) -> Vendor:
    """
    Authenticate a Vendor by username and business_id.

    Args:
        session (Session): Active SQLAlchemy session.
        credentials (OAuth2PasswordRequestForm): Credentials containing username, password and grant_type.
        form_param (Any): Form parameters containing business_id.

    Returns:
        Vendor: The authenticated Vendor instance.

    Raises:
        InvalidCredentials: If the username/business lookup or password validation fails.
        InvalidGrantType: If credentials.grant_type is not GrantType.PASSWORD.
        InactiveAccount: If the business or vendor account is suspended.
        UnknownValue: If the provided business_id does not exist.
    """
    business = (
        session.query(Business).filter(Business.id == form_param.business_id).first()
    )
    if business is None:
        raise exceptions.UnknownValue(Vendor.business_id)
    if business.status != BusinessStatus.ACTIVE:
        raise exceptions.InactiveAccount()

    vendor = (
        session.query(Vendor)
        .filter(
            Vendor.username == credentials.username,
            Vendor.business_id == form_param.business_id,
        )
        .first()
    )
    if vendor is None:
        raise exceptions.InvalidCredentials()
    if vendor.status == AccountStatus.SUSPENDED:
        raise exceptions.InactiveAccount()
    return user_credentials(vendor, credentials)


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
        model_cls (Type[Union[ExecutiveToken, OperatorToken, VendorToken]]): The ORM model class.
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
        session (Session): Active SQLAlchemy session.
        model_cls (Type[Union[ExecutiveToken, OperatorToken, VendorToken]]): The ORM model class.
        access_token (str): The access token string to validate.

    Returns:
        The valid token model instance.

    Raises:
        InvalidToken: If token is invalid, revoked, or expired.
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
    role_list: list[ExecutiveRole | VendorRole | OperatorRole],
    permission_path: str,
    raise_exception: bool = True,
) -> bool:
    """
    Validate if a user has a specific permission based on their roles.

    Args:
        role_list (list[ExecutiveRole | VendorRole | OperatorRole]): List of roles.
        permission_path (str): Permission path.
        raise_exception (bool): Whether to raise `NoPermission` if permission is not found, defaults to True.

    Returns:
        bool:
            - True if the permission is found.
            - False if not found and `raise_exception=False`.

    Raises:
        NoPermission: If the user lacks the required permission and `raise_exception=True`.
    """
    for role in role_list or []:
        permissions = role.permissions
        if get_by_path(permissions, permission_path):
            return True

    if raise_exception:
        raise exceptions.NoPermission()
    return False


def validate_company_id(session: Session, company_id: int) -> Company:
    """
    Validate the existence of a Company by its ID.

    Args:
        session (Session): Active SQLAlchemy session.
        company_id (int): The ID of the company to fetch.

    Returns:
        Company: The Company instance matching the given ID.

    Raises:
        UnknownValue: If no Company with the provided ID exists.
    """
    company = session.query(Company).filter(Company.id == company_id).first()
    if company is None:
        raise exceptions.UnknownValue(Company.id)
    return company
