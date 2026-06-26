"""
This module provides helper functions for validation.

It includes reusable utilities to handle common operations,
making it easier for developers to integrate them into their projects.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, List, Type, TypeVar, Union
from dns.enum import IntEnum
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.elements import ClauseElement
import math
import mimetypes
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from shapely.geometry.base import BaseGeometry
from shapely import Polygon, wkt, errors
from dateutil.rrule import rrulestr

from app.src.functions import (
    get_by_path,
    get_executive_roles,
    get_operator_roles,
    get_vendor_roles,
)
from app.src import argon2, exceptions
from app.src.enums import AccountStatus, BusinessStatus, CompanyStatus, GrantType
from app.src.db import (
    Executive,
    ExecutiveRole,
    ExecutiveToken,
    ORMbase,
    Operator,
    OperatorToken,
    Vendor,
    VendorToken,
    OperatorRole,
    VendorRole,
    Company,
    Business,
    LandmarkInRoute,
)
from app.src.constants import (
    DYNAMIC_FARE_VERSION,
    MIN_LANDMARKS_PER_ROUTE,
    MAX_IMAGE_FILE_SIZE,
    MIN_IMAGE_FILE_SIZE,
    MAX_IMAGE_RESOLUTION,
    MIN_IMAGE_RESOLUTION,
    TMZ_PRIMARY,
)
from app.src.dynamic_fare.v1 import DynamicFare


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
        InactiveAccount: If the company account is not verified or under verification.
        UnknownValue: If the provided company_id does not exist.
    """
    company = session.query(Company).filter(Company.id == form_param.company_id).first()
    if company is None:
        raise exceptions.UnknownValue(Operator.company_id)
    if company.status not in (CompanyStatus.VERIFIED, CompanyStatus.UNDER_VERIFICATION):
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
        InactiveAccount: If the business account is not active.
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


# Set decompression bomb guard
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_RESOLUTION * MAX_IMAGE_RESOLUTION


def validate_image(file_bytes: bytes, filename: str) -> None:
    """
    Validate an image file based on its content type and size.

    Args:
        file_bytes (bytes): The bytes of the image file.
        filename (str): The filename of the image file.

    Raises:
        InvalidImageFile: If the image file is invalid.
    """
    try:
        guessed_mime, _ = mimetypes.guess_type(filename)
        if not guessed_mime or not guessed_mime.startswith("image/"):
            raise exceptions.InvalidImageFile()

        size = len(file_bytes)
        if size > MAX_IMAGE_FILE_SIZE or size < MIN_IMAGE_FILE_SIZE:
            raise exceptions.InvalidImageFile()

        with Image.open(BytesIO(file_bytes)) as image:
            image.load()
            width, height = image.size
            if not (MIN_IMAGE_RESOLUTION <= width <= MAX_IMAGE_RESOLUTION) or not (
                MIN_IMAGE_RESOLUTION <= height <= MAX_IMAGE_RESOLUTION
            ):
                raise exceptions.InvalidImageFile()
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ):
        raise exceptions.InvalidImageFile()


def validate_srid_4326(geometry: BaseGeometry) -> bool:
    """
    Validate that a Shapely geometry contains WGS84 (SRID 4326) compatible coordinates.

    This function checks if all coordinates within the geometry fall within the
    valid WGS84 lon/lat ranges. The validation supports both singular and composite geometries and inspects:
        - Exterior coordinates for polygons
        - Direct coordinates for simple geometries
        - Coordinates of each geometry in multi-geometries (recursively)

    Args:
        geometry (BaseGeometry): Shapely geometry instance.

    Returns:
        bool: True if all coordinates fall within valid WGS84 lon/lat ranges.

    Raises:
        InvalidSRID4326: If any coordinate lies outside SRID 4326 bounds.
    """

    def check_coords(coords):
        # Handle variable-length coordinate tuples to support 3D geometries (e.g., POINT Z)
        for coord in coords:
            try:
                if len(coord) < 2:
                    raise exceptions.InvalidSRID4326()
                longitude, latitude = coord[0], coord[1]
                if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
                    raise exceptions.InvalidSRID4326()
            except (TypeError, ValueError):
                raise exceptions.InvalidSRID4326()
        return True

    # Check single geometries
    if hasattr(geometry, "exterior"):
        check_coords(geometry.exterior.coords)
    elif hasattr(geometry, "coords"):
        check_coords(geometry.coords)

    # Check Multi* geometries recursively
    if hasattr(geometry, "geoms"):
        for geom in geometry.geoms:
            validate_srid_4326(geom)

    return True


def validate_rrule_string(rrule_string: str) -> bool:
    """
    Validate a recurrence rule (RRULE) string.

    Args:
        rrule_string (str): The RRULE string to validate.

    Returns:
        bool: True if the RRULE string is valid.

    Raises:
        InvalidRRULEString: If the RRULE string is invalid.
    """
    try:
        rrulestr(rrule_string, dtstart=datetime.now(tz=TMZ_PRIMARY), ignoretz=False)
    except Exception:
        raise exceptions.InvalidRRULEString()
    return True


def validate_wkt_string(
    wkt_string: str, expected_type: Type[BaseGeometry]
) -> BaseGeometry:
    """
    Validate and parse a WKT string into a Shapely geometry of the expected type.

    Args:
        wkt_string (str): Well-Known Text (WKT) geometry string.
        expected_type (Type[BaseGeometry]): Expected Shapely geometry class.

    Returns:
        BaseGeometry: Parsed Shapely geometry instance.

    Raises:
        InvalidWKTStringOrType: If WKT parsing fails or type does not match `expected_type`.
    """
    try:
        geom = wkt.loads(wkt_string)
    except errors.ShapelyError:
        raise exceptions.InvalidWKTStringOrType()

    if not isinstance(geom, expected_type):
        raise exceptions.InvalidWKTStringOrType()

    return geom


def validate_AABB(geometry: BaseGeometry) -> bool:
    """
    Validate that the provided geometry is a valid Axis-Aligned Bounding Box (AABB).

    Args:
        geometry (BaseGeometry): Shapely geometry instance to validate.

    Returns:
        bool: True if the geometry is a valid AABB.

    Raises:
        InvalidAABB: If the geometry violates AABB structural or alignment rules.
    """
    if not isinstance(geometry, Polygon):
        raise exceptions.InvalidAABB()

    coords = list(geometry.exterior.coords)
    if len(coords) != 5:
        raise exceptions.InvalidAABB()

    rect = coords[:-1]  # Remove duplicate closing coordinate

    for i in range(4):
        x1, y1 = rect[i]
        x2, y2 = rect[(i + 1) % 4]
        if not (x1 == x2 or y1 == y2):
            raise exceptions.InvalidAABB()

    return True


def is_valid_transition(
    transitions: dict[Any, list[Any]], old_state: Any, new_state: Any
) -> bool:
    """
    Check if a state transition is valid.

    Args:
        transitions (dict[Any, list[Any]]): A mapping of valid state transitions.
        old_state (Any): The current state before the transition.
        new_state (Any): The desired state after the transition.

    Returns:
        bool: True if the transition is valid.
    """
    if new_state is None or old_state == new_state:
        return True
    if not transitions:
        return False
    if old_state not in transitions:
        return False
    return new_state in transitions[old_state]


T = TypeVar("T", bound=ORMbase)


def validate_id(
    session: Session,
    model_cls: Type[T],
    unique_id: int,
    column: Union[InstrumentedAttribute, str],
    extra_filter: ClauseElement[bool] | None = None,
) -> T:
    """
    Generic function to validate an ID based on a given model class.

    Args:
        session (Session): Active SQLAlchemy session.
        model_cls (Type[T]): The ORM model class.
        unique_id (int): The ID of the record to fetch.
        column (InstrumentedAttribute | str): ORM column or field name for error messages.
        extra_filter (ClauseElement[bool] | None): Additional filters to apply, defaults to None.

    Returns:
        T: The instance of the model class matching the given ID.

    Raises:
        UnknownValue: If no instance with the provided ID exists.
    """
    query = session.query(model_cls).filter(model_cls.id == unique_id)
    if extra_filter is not None:
        query = query.filter(extra_filter)
    result = query.first()

    if result is None:
        raise exceptions.UnknownValue(column)
    return result


def validate_route(route_id: int, session: Session) -> bool:
    """
    Validate that a route has a correct sequence of landmarks.

    Conditions:
        - Must contain at least MIN_LANDMARKS_PER_ROUTE landmarks.
        - The first landmark must start at distance 0.
        - The first landmark cannot have arrival/departure deltas set.
        - The last landmark must have matching arrival and departure deltas.
        - Arrival deltas must be non-decreasing and unique.
        - Departure deltas must be unique.

    Args:
        route_id (int): The route ID to validate.
        session (Session): Active SQLAlchemy session.

    Returns:
        bool: True if the route passes validation, False otherwise.
    """
    landmarks = (
        session.query(LandmarkInRoute)
        .filter(LandmarkInRoute.route_id == route_id)
        .order_by(LandmarkInRoute.distance_from_start.asc())
        .all()
    )

    # Minimum landmarks & must start at 0
    if (
        len(landmarks) < MIN_LANDMARKS_PER_ROUTE
        or landmarks[0].distance_from_start != 0
    ):
        return False

    # First landmark must not have deltas
    # The last landmark must have matching arrival and departure deltas.
    if (
        landmarks[0].arrival_delta
        or landmarks[0].departure_delta
        or landmarks[-1].arrival_delta != landmarks[-1].departure_delta
    ):
        return False

    seen_arrivals, seen_departures = set(), set()

    for i in range(1, len(landmarks)):
        # Arrival must not be earlier than previous departure
        if landmarks[i].arrival_delta < landmarks[i - 1].departure_delta:
            return False

        # Arrival and departure deltas must be unique
        if landmarks[i].arrival_delta in seen_arrivals:
            return False
        if landmarks[i].departure_delta in seen_departures:
            return False

        seen_arrivals.add(landmarks[i].arrival_delta)
        seen_departures.add(landmarks[i].departure_delta)

    return True


def validate_fare_function(function: str, attributes: dict) -> DynamicFare:
    """
    Validate and build a dynamic fare function against system rules.

    Validation rules:
        - The fare function must use the current dynamic fare version.
        - It must return valid (>= 0) fares for all known ticket types.

    Args:
        function (str): String expression of the fare function.
        attributes (dict): Fare configuration, expected keys:
            - "df_version" (int): Dynamic fare version.
            - "ticket_types" (list[dict]): List of ticket types with "name" fields.

    Returns:
        DynamicFare: A validated `DynamicFare` object that can be used
        to compute fares at runtime.

    Raises:
        exceptions.InvalidFareVersion: If the dynamic fare version is unsupported.
        exceptions.UnknownTicketType: If a known ticket type produces invalid fares.
    """
    df_version = attributes.get("df_version")
    if df_version != DYNAMIC_FARE_VERSION:
        raise exceptions.InvalidFareVersion()

    extras = attributes.get("extras", {})
    ticket_types = attributes.get("ticket_types", [])

    fare_function = DynamicFare(function)

    for ticket_type in ticket_types:
        name = ticket_type.get("name")
        result = fare_function.evaluate(name, 1, extras)
        if (
            not isinstance(result, (int, float))
            or isinstance(result, bool)
            or not math.isfinite(result)
            or result < 0
        ):
            raise exceptions.UnknownTicketType(
                detail=f"Ticket type '{name}' cannot be validated using the function"
            )

    return fare_function


def validate_state_transition(
    transitions: dict[IntEnum, list[IntEnum]],
    old_state: IntEnum,
    new_state: IntEnum,
    column: InstrumentedAttribute,
) -> bool:
    """
    Validate whether a state transition is allowed.

    Args:
        transitions (dict[IntEnum, list[IntEnum]]): A mapping of valid state transitions.
        old_state (IntEnum): The current state before the transition.
        new_state (IntEnum): The desired state after the transition.
        column (InstrumentedAttribute): The ORM column associated with the state, used for exception messages.

    Returns:
        bool: True if the transition is valid.

    Raises:
        exceptions.InvalidStateTransition: If the transition from old_state to new_state is not allowed.
    """
    if not is_valid_transition(transitions, old_state, new_state):
        raise exceptions.InvalidStateTransition(column)
    return True


def authorize_executive(
    session: Session, token_value: str, permissions: List[str]
) -> ExecutiveToken:
    """
    Authorize an executive based on their access token and required permissions.

    Authorization succeeds if the executive has any of the provided permissions.

    Args:
        session (Session): Active SQLAlchemy session.
        token_value (str): The access token value to be verified.
        permissions (List[str]): A list of permission path strings. Authorization succeeds if
                                the executive has at least one of these permissions.

    Returns:
        ExecutiveToken: The token object if the executive has at least one of the required permissions.

    Raises:
        exceptions.InvalidToken: If the token is invalid or cannot be verified.
        exceptions.NoPermission: If the executive does not have any of the provided permissions.
    """
    token = verify_token(session, ExecutiveToken, token_value)
    roles = get_executive_roles(session, token)
    for permission in permissions:
        if verify_permission(roles, permission, raise_exception=False):
            return token
    raise exceptions.NoPermission()


def authorize_operator(
    session: Session, token_value: str, permissions: List[str]
) -> OperatorToken:
    """
    Authorize an operator based on their access token and required permissions.

    Authorization succeeds if the operator has any of the provided permissions.

    Args:
        session (Session): Active SQLAlchemy session.
        token_value (str): The access token value to be verified.
        permissions (List[str]): A list of permission path strings. Authorization succeeds if
                                the operator has at least one of these permissions.

    Returns:
        OperatorToken: The token object if the operator has at least one of the required permissions.

    Raises:
        exceptions.InvalidToken: If the token is invalid or cannot be verified.
        exceptions.NoPermission: If the operator does not have any of the provided permissions.
    """
    token = verify_token(session, OperatorToken, token_value)
    roles = get_operator_roles(session, token)
    for permission in permissions:
        if verify_permission(roles, permission, raise_exception=False):
            return token
    raise exceptions.NoPermission()


def authorize_vendor(
    session: Session, token_value: str, permissions: List[str]
) -> VendorToken:
    """
    Authorize a vendor based on their access token and required permissions.

    Authorization succeeds if the vendor has any of the provided permissions.

    Args:
        session (Session): Active SQLAlchemy session.
        token_value (str): The access token value to be verified.
        permissions (List[str]): A list of permission path strings. Authorization succeeds if
                                the vendor has at least one of these permissions.

    Returns:
        VendorToken: The token object if the vendor has at least one of the required permissions.

    Raises:
        exceptions.InvalidToken: If the token is invalid or cannot be verified.
        exceptions.NoPermission: If the vendor does not have any of the provided permissions.
    """
    token = verify_token(session, VendorToken, token_value)
    roles = get_vendor_roles(session, token)
    for permission in permissions:
        if verify_permission(roles, permission, raise_exception=False):
            return token
    raise exceptions.NoPermission()
