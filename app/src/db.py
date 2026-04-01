"""
Database Setup & ORM Models for Entebus Server.

This module:
- Configures the SQLAlchemy engine, session factory, and base class.
- Defines ORM models for core entities.

All ORM models should inherit from `ORMbase`.
"""

from datetime import datetime, timedelta, timezone
from geoalchemy2 import Geometry
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Index,
    Numeric,
    create_engine,
    Boolean,
    TEXT,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    event,
    Connection,
    func,
    inspect,
    Time,
)
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapper
from secrets import token_hex
from sqlalchemy.dialects.postgresql import JSONB

from app.src import argon2
from app.src.constants import (
    PSQL_DB_DRIVER,
    PSQL_DB_HOST,
    PSQL_DB_PASSWORD,
    PSQL_DB_NAME,
    PSQL_DB_PORT,
    PSQL_DB_USERNAME,
    MAX_REFRESH_TOKEN_VALIDITY,
    MAX_ACCESS_TOKEN_VALIDITY,
)
from app.src.enums import (
    AccountStatus,
    BusinessStatus,
    BusinessType,
    GenderType,
    LandmarkType,
    PlatformType,
    CompanyStatus,
    CompanyType,
    OperatorType,
    VendorType,
    BankAccountType,
    VehicleStatus,
    RouteStatus,
)


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------
def get_db_url() -> str:
    """Return a SQLAlchemy-compatible PostgreSQL connection URL."""
    return (
        f"{PSQL_DB_DRIVER}://{PSQL_DB_USERNAME}:{PSQL_DB_PASSWORD}"
        f"@{PSQL_DB_HOST}:{PSQL_DB_PORT}/{PSQL_DB_NAME}"
    )


db_url = get_db_url()
engine = create_engine(url=db_url, echo=False)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------
class ORMbase(DeclarativeBase):
    """
    Base class for all ORM models.

    Documentation Template
    ----------------------
    Each ORM model must follow this structure to maintain consistency and clarity.
    Keep descriptions concise but meaningful, focusing on the business purpose
    and schema-level details.

    Summary
    -------
    <One-line summary of the entity. Example: Represents a registered company in the system.>

    Description
    -----------
    <Optional longer description with business context, usage scenarios, or
    relationships to other entities.>

    Notes
    -----
    - <Special behavior, caveats, or usage guidelines>
    - <If data lifecycle or retention rules apply, mention here>

    Table
    -----
    table_name

    Columns
    -------
    column_name (Type, Constraints):
        Description of the column's purpose, semantics, and usage.
        - Example: `email (String, unique, nullable=False)`:
          Stores the login email of the user.

    Indexes
    -------
    - index_name: Columns included, purpose of the index.

    Constraints
    -----------
    - constraint_name: Description of constraint, why it exists, and implications.
    """

    pass


# ---------------------------------------------------------------------------
# Executive Models
# ---------------------------------------------------------------------------
class Executive(ORMbase):
    """
    Represents an executive user within the system, typically someone with elevated
    permissions such as admins, supervisors, or marketing members.

    This model stores authentication credentials, profile details, and status metadata
    necessary to manage executive-level access and communication.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the executive.

        username (String(32), unique, not null):
            Username used for login or identification within the system.
            Ideally, the username shouldn't be changed once set.
            It should start with an alphabet (uppercase or lowercase).
            It can contain uppercase and lowercase letters, as well as digits from 0 to 9.
            It should be 4-32 characters long.
            May include hyphen (-), period (.), at symbol (@), and underscore (_).

        password (TEXT, not null):
            Hashed password used for authentication.
            It should be 8-32 characters long.
            Passwords can contain uppercase and lowercase letters, as well as digits from 0 to 9.
            Plaintext should never be stored here. Argon2 is used for secure hashing.
            May include hyphen (-), plus (+), comma (,), period (.), at symbol (@), underscore (_),
            dollar sign ($), percent (%), ampersand (&), asterisk (*), hash (#),
            exclamation mark (!), caret (^), equals (=), forward slash (/), question mark (?).

        gender (Integer, not null, default=GenderType.OTHER):
            Represents the executive's gender. Mapped from the `GenderType` enum.

        full_name (TEXT, nullable):
            Full name of the executive.
            Maximum 32 characters long.

        designation (TEXT, nullable):
            Job title or role description of the executive.
            Maximum 32 characters long.

        status (Integer, not null, default=AccountStatus.ACTIVE):
            Indicates the account status. Mapped from the `AccountStatus` enum.

        phone_number (TEXT, nullable):
            Contact number of the executive.
            Maximum 32 characters long.
            Saved and processed in RFC 3966 format (https://datatracker.ietf.org/doc/html/rfc3966).
            Example: "+1-202-555-0143"

        email_id (TEXT, nullable):
            Email address of the executive.
            Maximum 256 characters long.
            Enforce the format prescribed by RFC 5322 (https://en.wikipedia.org/wiki/Email_address).

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically update whenever the executive's profile record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp of when the executive account was created.
    """

    __tablename__ = "executive"

    id = Column(Integer, primary_key=True)
    username = Column(String(32), nullable=False, unique=True)
    password = Column(TEXT, nullable=False)
    gender = Column(Integer, nullable=False, default=GenderType.OTHER)
    full_name = Column(TEXT)
    designation = Column(TEXT)
    status = Column(Integer, nullable=False, default=AccountStatus.ACTIVE)
    # Contact details
    phone_number = Column(TEXT)
    email_id = Column(TEXT)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


@event.listens_for(Executive, "before_insert")
@event.listens_for(Executive, "before_update")
def preprocess_password(
    mapper: Mapper, connection: Connection, target: Executive
) -> None:
    """Event listener to hash the password before insertion or update."""

    history = inspect(target).attrs.password.history
    if history.has_changes() and target.password:
        target.password = argon2.make_password(target.password)


class ExecutiveRole(ORMbase):
    """
    Represents a role or access level assigned to executives within the system.
    It is used to define permissions and roles for executives, specifying the actions
    they are permitted to perform within the system.

    This model stores information about different roles and their associated permissions,
    enabling fine-grained control over executive access and functionality within the system.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the executive role.

        name (String(32), unique, not null):
            Name or label for the role.
            It should be 4-32 characters long.

        permissions (JSONB, not null):
            List of permissions associated with the role.
            These permissions determine which actions the executive can perform within the system.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp of the last update to the role's permissions.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this role was created.
    """

    __tablename__ = "executive_role"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False, unique=True)
    permissions = Column(JSONB, nullable=False, default=dict)
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExecutiveRoleMap(ORMbase):
    """
    Represents the mapping between executives and their assigned roles,
    enabling a many-to-many relationship between `executive` and `executive_role`.

    This table allows an executive to be assigned multiple roles and a role
    to be assigned to multiple executives. Useful for implementing a flexible
    Role-Based Access Control (RBAC) system.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the executive role mapped.

        role_id (Integer, not null):
            Foreign key referencing `executive_role.id`.
            Specifies the role assigned to the executive.
            Cascades on delete — if the role is removed, related mappings are deleted.

        executive_id (Integer, not null):
            Foreign key referencing `executive.id`.
            Identifies the executive receiving the role.
            Cascades on delete — if the executive is removed, related mappings are deleted.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the mapping record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this mapping was created.
    """

    __tablename__ = "executive_role_map"
    __table_args__ = (UniqueConstraint("role_id", "executive_id"),)

    id = Column(Integer, primary_key=True)
    role_id = Column(
        Integer,
        ForeignKey("executive_role.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    executive_id = Column(
        Integer,
        ForeignKey("executive.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class ExecutiveToken(ORMbase):
    """
    Represents an authentication token issued to an executive,
    enabling secure access to the platform with support for token expiration
    and client metadata tracking.

    This table stores unique access and refresh tokens mapped to executives,
    along with details about the device or client used and timestamps for auditing.
    Useful for session management, device tracking, and implementing token-based authentication.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the executive token.

        executive_id (Integer, not null):
            Foreign key referencing `executive.id`.
            Identifies the executive associated with this token.
            Cascades on delete — if the executive is removed, related tokens are deleted.

        access_token (String, not null, unique, default=lambda: token_hex(32)):
            Securely generated 64-character hexadecimal access token.
            Used to authenticate the executive on subsequent requests.
            In format prescribed by RFC 6749 (https://datatracker.ietf.org/doc/html/rfc6749).

        refresh_token (String, not null, unique, default=lambda: token_hex(32)):
            Securely generated 64-character hexadecimal refresh token.
            Used to refresh the access token when needed.
            In format prescribed by RFC 6749 (https://datatracker.ietf.org/doc/html/rfc6749).

        expires_in (Integer, not null, default=MAX_ACCESS_TOKEN_VALIDITY):
            Access token expiration duration in seconds.
            Defines the duration after which the token becomes invalid.

        refresh_before (DateTime(timezone=True), not null, default=lambda: datetime.now(timezone.utc) + timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY)):
            Defines the UTC timestamp after which the refresh token becomes invalid.

        platform_type (Integer, nullable, default=PlatformType.OTHER):
            Enum value indicating the client platform type.

        client_details (TEXT, nullable):
            Description of the client device or environment where the access token was issued or used.
            May include user agent, app version, IP address, etc.
            Maximum 1024 characters long.

        is_revoked (Boolean, not null, default=False):
            Flag indicating whether the token has been revoked.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the token record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this token was created.
    """

    __tablename__ = "executive_token"

    id = Column(Integer, primary_key=True)
    executive_id = Column(
        Integer,
        ForeignKey("executive.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Tokens
    access_token = Column(
        String(64), unique=True, nullable=False, default=lambda: token_hex(32)
    )
    refresh_token = Column(
        String(64), unique=True, nullable=False, default=lambda: token_hex(32)
    )
    # Expirations
    expires_in = Column(Integer, nullable=False, default=MAX_ACCESS_TOKEN_VALIDITY)
    refresh_before = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
        + timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY),
    )
    is_revoked = Column(Boolean, nullable=False, default=False)
    # Device related details
    platform_type = Column(Integer, default=PlatformType.OTHER)
    client_details = Column(TEXT)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class ExecutiveImage(ORMbase):
    """
    Represents an uploaded image associated with a specific executive.

    Each record stores metadata about an image file uploaded for an executive,
    allowing for management, retrieval, and replacement of profile or related images.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the executive image.

        executive_id (Integer, not null, unique):
            Foreign key referencing `executive.id` to whom this image belongs.
            Cascades on delete — if the executive is removed, related image is deleted.

        file_name (String(128), not null):
            Original name of the uploaded image file, including extension.

        file_size (Integer, not null):
            Size of the uploaded file in bytes.

        file_type (String(128), not null):
            MIME type of the uploaded file (e.g., "image/jpeg", "image/png").

       created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the image record was initially created.
    """

    __tablename__ = "executive_image"

    id = Column(Integer, primary_key=True)
    executive_id = Column(
        Integer,
        ForeignKey("executive.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # File metadata
    file_name = Column(String(128), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_type = Column(String(128), nullable=False)
    # Metadata
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class OperatorToken(ORMbase):
    """
    Represents an authentication token issued to an operator,
    enabling secure access to the platform with support for token expiration
    and client metadata tracking.

    This table stores unique access and refresh tokens mapped to operators,
    along with details about the device or client used and timestamps for auditing.
    Useful for session management, device tracking, and implementing token-based authentication.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the operator token.

        company_id (Integer, not null):
            Foreign key referencing `company.id`.
            Identifies the company to which the token belongs.
            Cascades on delete — if the company is removed, related tokens are deleted.

        operator_id (Integer, not null):
            Foreign key referencing `operator.id`.
            Identifies the operator associated with this token.
            Cascades on delete — if the operator is removed, related tokens are deleted.

        access_token (String(64), not null, unique, default=lambda: token_hex(32)):
            Securely generated 64-character hexadecimal access token.
            Used to authenticate the operator on subsequent requests.

        refresh_token (String(64), not null, unique, default=lambda: token_hex(32)):
            Securely generated 64-character hexadecimal refresh token.
            Used to refresh the access token when needed.

        expires_in (Integer, not null, default=MAX_ACCESS_TOKEN_VALIDITY):
            Access token expiration duration in seconds.

        refresh_before (DateTime(timezone=True), not null, default=lambda: datetime.now(timezone.utc) + timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY)):
            Defines the UTC timestamp after which the refresh token becomes invalid.

        platform_type (Integer, nullable, default=PlatformType.OTHER):
            Enum value indicating the client platform type.

        client_details (TEXT, nullable):
            Description of the client device or environment where the access token was issued or used.
            May include user agent, app version, IP address, etc.
            Maximum 1024 characters long.

        is_revoked (Boolean, not null, default=False):
            Flag indicating whether the token has been revoked.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the token record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this token was created.
    """

    __tablename__ = "operator_token"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operator_id = Column(
        Integer,
        ForeignKey("operator.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Tokens
    access_token = Column(
        String(64), unique=True, nullable=False, default=lambda: token_hex(32)
    )
    refresh_token = Column(
        String(64), unique=True, nullable=False, default=lambda: token_hex(32)
    )
    # Expirations
    expires_in = Column(Integer, nullable=False, default=MAX_ACCESS_TOKEN_VALIDITY)
    refresh_before = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
        + timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY),
    )
    is_revoked = Column(Boolean, nullable=False, default=False)
    # Device related details
    platform_type = Column(Integer, default=PlatformType.OTHER)
    client_details = Column(TEXT)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class OperatorRole(ORMbase):
    """
    Represents a role assigned to operators within a specific company.
    It is used to define permissions and roles for operators, specifying the actions
    they are permitted to perform within the owning company.

    This model stores information about operator roles and their associated permissions,
    enabling fine-grained control over operator access and functionality within the system.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the operator role.

        company_id (Integer, not null):
            Foreign key referencing `company.id`.
            Identifies the company that owns the role.
            Cascades on delete — if the company is removed, related roles are deleted.

        name (String(32), not null):
            Name or label for the role.
            It should be 4-32 characters long.

        permissions (JSONB, not null):
            List of permissions associated with the role.
            These permissions determine which actions the operator can perform within the system.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp of the last update to the role's permissions.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this role was created.
    """

    __tablename__ = "operator_role"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(32), nullable=False)
    permissions = Column(JSONB, nullable=False, default=dict)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OperatorRoleMap(ORMbase):
    """
    Represents the mapping between operators and their assigned company-scoped roles,
    enabling a many-to-many relationship between `operator` and `operator_role`.

    This table allows an operator to be assigned multiple roles and a role
    to be assigned to multiple operators within a company. Useful for implementing
    a flexible Role-Based Access Control (RBAC) system.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the operator role mapping.

        company_id (Integer, not null):
            Foreign key referencing `company.id`.
            Identifies the company that owns the role assignment.
            Cascades on delete — if the company is removed, related mappings are deleted.

        role_id (Integer, not null):
            Foreign key referencing `operator_role.id`.
            Specifies the role assigned to the operator.
            Cascades on delete — if the role is removed, related mappings are deleted.

        operator_id (Integer, not null):
            Foreign key referencing `operator.id`.
            Identifies the operator receiving the role.
            Cascades on delete — if the operator is removed, related mappings are deleted.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the mapping record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this mapping was created.
    """

    __tablename__ = "operator_role_map"
    __table_args__ = (UniqueConstraint("company_id", "role_id", "operator_id"),)

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id = Column(
        Integer,
        ForeignKey("operator_role.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operator_id = Column(
        Integer,
        ForeignKey("operator.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class VendorToken(ORMbase):
    """
    Represents an authentication token issued to a vendor,
    enabling secure access to the platform with support for token expiration
    and client metadata tracking.

    This table stores unique access and refresh tokens mapped to vendors (scoped to a business),
    along with details about the device or client used and timestamps for auditing.
    Useful for session management, device tracking, and implementing token-based authentication.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the vendor token.

        business_id (Integer, not null):
            Foreign key referencing `business.id`.
            Identifies the business context for this vendor token.
            Cascades on delete — if the business is removed, related tokens are deleted.

        vendor_id (Integer, not null):
            Foreign key referencing `vendor.id`.
            Identifies the vendor associated with this token.
            Cascades on delete — if the vendor is removed, related tokens are deleted.

        access_token (String(64), not null, unique, default=lambda: token_hex(32)):
            Securely generated 64-character hexadecimal access token.
            Used to authenticate the vendor on subsequent requests.

        refresh_token (String(64), not null, unique, default=lambda: token_hex(32)):
            Securely generated 64-character hexadecimal refresh token.
            Used to refresh the access token when needed.

        expires_in (Integer, not null, default=MAX_ACCESS_TOKEN_VALIDITY):
            Access token expiration duration in seconds.

        refresh_before (DateTime(timezone=True), not null, default=lambda: datetime.now(timezone.utc) + timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY)):
            Defines the UTC timestamp after which the refresh token becomes invalid.

        is_revoked (Boolean, not null, default=False):
            Flag indicating whether the token has been revoked.

        platform_type (Integer, nullable, default=PlatformType.OTHER):
            Enum value indicating the client platform type.

        client_details (TEXT, nullable):
            Description of the client device or environment where the access token was issued or used.
            May include user agent, app version, IP address, etc.
            Maximum 1024 characters long.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the token record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this token was created.
    """

    __tablename__ = "vendor_token"

    id = Column(Integer, primary_key=True)
    business_id = Column(
        Integer,
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_id = Column(
        Integer,
        ForeignKey("vendor.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Tokens
    access_token = Column(
        String(64), unique=True, nullable=False, default=lambda: token_hex(32)
    )
    refresh_token = Column(
        String(64), unique=True, nullable=False, default=lambda: token_hex(32)
    )
    # Expirations
    expires_in = Column(Integer, nullable=False, default=MAX_ACCESS_TOKEN_VALIDITY)
    refresh_before = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
        + timedelta(seconds=MAX_REFRESH_TOKEN_VALIDITY),
    )
    is_revoked = Column(Boolean, nullable=False, default=False)
    # Device related details
    platform_type = Column(Integer, default=PlatformType.OTHER)
    client_details = Column(TEXT)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class VendorRole(ORMbase):
    """
    Represents a role assigned to vendors within a specific business.
    It is used to define permissions and roles for vendors, specifying the actions
    they are permitted to perform within the owning business.

    This model stores information about vendor roles and their associated permissions,
    enabling fine-grained control over vendor access and functionality within the system.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the vendor role.

        business_id (Integer, not null):
            Foreign key referencing `business.id`.
            Identifies the business that owns the role.
            Cascades on delete — if the business is removed, related roles are deleted.

        name (String(32), not null):
            Name or label for the role.
            Should be 4-32 characters long.

        permissions (JSONB, not null):
            List of permissions associated with the role.
            These permissions determine which actions the vendor can perform within the system.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp of the last update to the role's permissions.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this role was created.
    """

    __tablename__ = "vendor_role"
    __table_args__ = (UniqueConstraint("business_id", "name"),)

    id = Column(Integer, primary_key=True)
    business_id = Column(
        Integer,
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(32), nullable=False)
    permissions = Column(JSONB, nullable=False, default=list)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VendorRoleMap(ORMbase):
    """
    Represents the mapping between vendors and their assigned business-scoped roles,
    enabling a many-to-many relationship between `vendor` and `vendor_role`.

    This table allows a vendor to be assigned multiple roles and a role
    to be assigned to multiple vendors within a business. Useful for implementing
    a flexible Role-Based Access Control (RBAC) system.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the vendor role mapping.

        business_id (Integer, not null):
            Foreign key referencing `business.id`.
            Identifies the business that owns the role assignment.
            Cascades on delete — if the business is removed, related mappings are deleted.

        role_id (Integer, not null):
            Foreign key referencing `vendor_role.id`.
            Specifies the role assigned to the vendor.
            Cascades on delete — if the role is removed, related mappings are deleted.

        vendor_id (Integer, not null):
            Foreign key referencing `vendor.id`.
            Identifies the vendor receiving the role.
            Cascades on delete — if the vendor is removed, related mappings are deleted.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the mapping record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this mapping was created.
    """

    __tablename__ = "vendor_role_map"
    __table_args__ = (UniqueConstraint("business_id", "role_id", "vendor_id"),)

    id = Column(Integer, primary_key=True)
    business_id = Column(
        Integer,
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id = Column(
        Integer,
        ForeignKey("vendor_role.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_id = Column(
        Integer,
        ForeignKey("vendor.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class Landmark(ORMbase):
    """
    Represents a geo-spatial landmark used for mapping, zoning, or location-aware operations.

    Landmarks are stored as named polygonal regions with versioning and type categorization,
    enabling geographic indexing, boundary change tracking, and spatial queries
    (containment, intersection, overlap) using PostGIS.

    Frontend-Backend Note:
        Although circular regions are displayed and drawn in the frontend UI, they are
        **converted to axis-aligned bounding box (AABB) polygons** before being sent to the backend.
        The AABB polygon is a square geofence tightly enclosing the circle, simplifying
        spatial indexing and backend spatial operations.

    Spatial Constraint:
        - `ix_landmark_alias_names_gin` (GIN index):
            Speeds up queries on the `alias_names` array.
        - `ix_landmark_boundary_gist` (GiST index):
            Supports fast spatial queries (overlaps, intersections, containment) on the `boundary` column.
        - `ux_landmark_boundary_hash` (unique BTree index on MD5 of geometry):
            Ensures no two landmarks have identical geometries.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the landmark.

        name (String(32), not null, indexed):
            Official name of the landmark.
            It should be 1-32 characters long.
            May include space ( ), hyphen (-), period (.), and underscore (_).

        version (Integer, not null, default=1):
            Version number incremented on updates.
            Useful for tracking changes and synchronizing updated boundaries.

        alias_names (ARRAY(String(32)), nullable):
            Optional list of alternative or local names for the landmark.
            Each alias can be up to 32 characters long.

        boundary (Geometry(POLYGON, SRID 4326), not null):
            Geo-spatial boundary stored as a PostGIS `POLYGON` using SRID 4326 (WGS 84 longitude/latitude).
            Represents the physical area covered by the landmark.
            No two landmarks can share the same geometry.

        type (Integer, not null, default=LandmarkType.LOCAL, indexed):
            Represents the type of the landmark. Mapped from the `LandmarkType` enum.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the landmark record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this landmark was created.
    """

    __tablename__ = "landmark"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    alias_names = Column(ARRAY(String(32)))
    boundary = Column(Geometry(geometry_type="POLYGON", srid=4326), nullable=False)
    type = Column(Integer, nullable=False, default=LandmarkType.LOCAL, index=True)
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_landmark_alias_names_gin", alias_names, postgresql_using="gin"),
        Index("ix_landmark_boundary_gist", boundary, postgresql_using="gist"),
        Index(
            "ux_landmark_boundary_hash",
            func.md5(func.ST_AsEWKB(boundary)),
            unique=True,
            postgresql_using="btree",
        ),
    )


class BusStop(ORMbase):
    """
    Represents a geo-referenced bus stop associated with a specific landmark.

    Bus stops are stored as point-based spatial entities used for mapping,
    routing, navigation, and proximity-based operations. Each bus stop belongs
    to a landmark region, enabling localized grouping and spatial queries such as
    nearest-stop detection, containment checks, or analytics within a landmark area.

    Spatial Constraint:
        - `uq_bus_stop_location_landmark_id` (unique BTree index on ST_AsBinary(location)):
            Ensures no two bus stops under the same landmark share the exact same
            spatial point. Using ST_AsBinary avoids floating-point comparison issues
            with raw geometry objects.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the bus stop.

        name (TEXT, not null):
            Official name of the bus stop.
            It should be 1-128 characters long.
            May include space ( ), hyphen (-), period (.), and underscore (_).

        landmark_id (Integer, not null):
            Foreign key referencing `landmark.id`.
            Indicates the landmark to which this bus stop belongs.
            Cascades on delete — all bus stops under a landmark are removed
            automatically if the landmark is deleted.

        location (Geometry(POINT, SRID 4326), not null):
            Geo-spatial point representing the exact location of the bus stop.
            Stored as a PostGIS `POINT` geometry using SRID 4326 (WGS 84 longitude/latitude).
            No two bus stops within the same landmark can share the same location,
            with uniqueness enforced via the `uq_bus_stop_location_landmark_id`
            unique index on `ST_AsBinary(location)` and `landmark_id`.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the bus stop record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the bus stop record was created.
    """

    __tablename__ = "bus_stop"

    id = Column(Integer, primary_key=True)
    name = Column(TEXT, nullable=False)
    landmark_id = Column(
        Integer,
        ForeignKey(Landmark.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_bus_stop_location_landmark_id",
            func.ST_AsBinary(location),
            landmark_id,
            unique=True,
        ),
    )


class Company(ORMbase):
    """
    Represents a company registered in the system, along with its status,
    type, contact information, and geographical location.

    This table stores core organizational data and is linked to other entities
    such as operators, roles, and tokens. It supports categorization, status tracking,
    and location-based operations.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the company.

        name (String(32), unique, not null):
            Name of the company.
            Must be unique and is required.
            Maximum 32 characters long.

        status (Integer, not null, default=CompanyStatus.UNDER_VERIFICATION):
            Verification status of the company. Mapped from the `CompanyStatus` enum.

        type (Integer, not null, default=CompanyType.OTHER):
            Type/category of the company. Mapped from the `CompanyType` enum.

        description (TEXT, nullable):
            Optional description or notes about the company.
            Maximum 1024 characters long.

        address (TEXT, not null):
            Physical or mailing address of the company.
            Must not be null.
            Used for communication or locating the company.
            Maximum 512 characters long.

        location (Geometry(POINT, SRID 4326), not null):
            Geographical location of the company represented as a POINT geometry with SRID 4326.
            Required for location-based features.

        settings (JSONB, nullable, default=dict):
            Flexible JSONB field for storing additional configuration, preferences, or custom data.
            This field allows future expansion without altering the database schema.
            Typical uses include feature flags, custom limits, UI preferences, integration keys, etc.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the company record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the company record was created.
    """

    __tablename__ = "company"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False, unique=True)
    status = Column(Integer, nullable=False, default=CompanyStatus.UNDER_VERIFICATION)
    type = Column(Integer, nullable=False, default=CompanyType.OTHER)
    description = Column(TEXT)
    address = Column(TEXT, nullable=False)
    location = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    settings = Column(JSONB, default=dict)  # For future expansion
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class Operator(ORMbase):
    """
    Represents an operator user within the system, typically someone who manages or operates
    under a company, such as owners, legal, HR, managers, or normal staff.

    This model stores authentication credentials, profile details, role type, and status metadata
    necessary to manage operator-level access and communication.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the operator.

        company_id (Integer, not null):
            Foreign key referencing `company.id`.
            Identifies the company to which the operator belongs.
            Cascades on delete — if the company is removed, related operators are deleted.

        username (String(32), not null):
            Username used for login or identification within the company.
            Ideally, the username shouldn't be changed once set.
            It should start with an alphabet (uppercase or lowercase).
            It can contain uppercase and lowercase letters, as well as digits from 0 to 9.
            It should be 4-32 characters long.
            May include hyphen (-), period (.), at symbol (@), and underscore (_).

        password (TEXT, not null):
            Hashed password used for authentication.
            It should be 8-32 characters long.
            Passwords can contain uppercase and lowercase letters, as well as digits from 0 to 9.
            Plaintext should never be stored here. Argon2 is used for secure hashing.
            May include hyphen (-), plus (+), comma (,), period (.), at symbol (@), underscore (_),
            dollar sign ($), percent (%), ampersand (&), asterisk (*), hash (#),
            exclamation mark (!), caret (^), equals (=), forward slash (/), question mark (?).

        gender (Integer, not null, default=GenderType.OTHER):
            Represents the operator's gender. Mapped from the `GenderType` enum.

        description (TEXT, nullable):
            Optional description or notes about the operator.
            Maximum 1024 characters long.

        type (Integer, not null, default=OperatorType.NORMAL):
            Role type of the operator. Mapped from the `OperatorType` enum.

        full_name (TEXT, nullable):
            Full name of the operator.
            Maximum 32 characters long.

        status (Integer, not null, default=AccountStatus.ACTIVE):
            Indicates the account status. Mapped from the `AccountStatus` enum.

        phone_number (TEXT, nullable):
            Contact number of the operator.
            Maximum 32 characters long.
            Saved and processed in RFC 3966 format (https://datatracker.ietf.org/doc/html/rfc3966).
            Example: "+1-202-555-0143"

        email_id (TEXT, nullable):
            Email address of the operator.
            Maximum 256 characters long.
            Enforce the format prescribed by RFC 5322 (https://en.wikipedia.org/wiki/Email_address).

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the operator's profile record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp of when the operator account was created.
    """

    __tablename__ = "operator"
    __table_args__ = (UniqueConstraint("username", "company_id"),)

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    username = Column(String(32), nullable=False)
    password = Column(TEXT, nullable=False)
    gender = Column(Integer, nullable=False, default=GenderType.OTHER)
    description = Column(TEXT)
    type = Column(Integer, nullable=False, default=OperatorType.NORMAL)
    full_name = Column(TEXT)
    status = Column(Integer, nullable=False, default=AccountStatus.ACTIVE)
    phone_number = Column(TEXT)
    email_id = Column(TEXT)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


@event.listens_for(Operator, "before_insert")
@event.listens_for(Operator, "before_update")
def preprocess_operator_password(
    mapper: Mapper, connection: Connection, target: Operator
) -> None:
    """Event listener to hash the operator password before insertion or update."""

    history = inspect(target).attrs.password.history
    if history.has_changes() and target.password:
        target.password = argon2.make_password(target.password)


class OperatorImage(ORMbase):
    """
    Represents an uploaded image associated with a specific operator.

    Each record stores metadata about an image file uploaded for an operator,
    allowing for management, retrieval, and replacement of profile or related images.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the operator image.

        company_id (Integer, not null):
            Foreign key referencing `company.id` to whom this image belongs.
            Cascades on delete — if the company is removed, related image is deleted.

        operator_id (Integer, not null, unique):
            Foreign key referencing `operator.id` to whom this image belongs.
            Cascades on delete — if the operator is removed, related image is deleted.

        file_name (String(128), not null):
            Original name of the uploaded image file, including extension.

        file_size (Integer, not null):
            Size of the uploaded file in bytes.

        file_type (String(128), not null):
            MIME type of the uploaded file (e.g., "image/jpeg", "image/png").

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the image record was initially created.
    """

    __tablename__ = "operator_image"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operator_id = Column(
        Integer,
        ForeignKey("operator.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # File metadata
    file_name = Column(String(128), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_type = Column(String(128), nullable=False)
    # Metadata
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class Business(ORMbase):
    """
    Represents a business registered in the system, along with its status,
    type, description, and geographical location.

    This table stores core business data and is linked to other entities
    such as vendors, roles, and tokens. It supports categorization, status tracking,
    and location-based operations.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the business.

        name (String(32), unique, not null):
            Name of the business.
            Must be unique and is required.
            Maximum 32 characters long.

        status (Integer, not null, default=BusinessStatus.ACTIVE):
            Verification/status of the business. Mapped from the `BusinessStatus` enum.

        type (Integer, not null, default=BusinessType.OTHER):
            Type/category of the business. Mapped from the `BusinessType` enum.

        description (TEXT, nullable):
            Optional description or notes about the business.
            Maximum 1024 characters long.

        address (TEXT, not null):
            Physical or mailing address of the business.
            Must not be null.
            Used for communication or locating the business.
            Maximum 512 characters long.

        location (Geometry(POINT, SRID 4326), not null):
            Geographical location of the business represented as a POINT geometry with SRID 4326.
            Required for location-based features.

        settings (JSONB, nullable, default=dict):
            Flexible JSONB field for storing additional configuration, preferences, or custom data.
            This field allows future expansion without altering the database schema.
            Typical uses include feature flags, custom limits, UI preferences, integration keys, etc.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the business record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the business record was created.
    """

    __tablename__ = "business"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False, unique=True)
    status = Column(Integer, nullable=False, default=BusinessStatus.ACTIVE)
    type = Column(Integer, nullable=False, default=BusinessType.OTHER)
    description = Column(TEXT)
    address = Column(TEXT, nullable=False)
    location = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    settings = Column(JSONB, default=dict)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class Vendor(ORMbase):
    """
    Represents a vendor user within the system, typically someone who operates
    under a business, such as suppliers or service providers.

    This model stores authentication credentials, profile details, role type, and status metadata
    necessary to manage vendor-level access and communication.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the vendor.

        business_id (Integer, not null):
            Foreign key referencing `business.id`.
            Identifies the business to which the vendor belongs.
            Cascades on delete — if the business is removed, related vendors are deleted.

        username (String(32), not null):
            Username used for login or identification within the business.
            Ideally, the username shouldn't be changed once set.
            It should start with an alphabet (uppercase or lowercase).
            It can contain uppercase and lowercase letters, as well as digits from 0 to 9.
            It should be 4-32 characters long.
            May include hyphen (-), period (.), at symbol (@), and underscore (_).

        password (TEXT, not null):
            Hashed password used for authentication.
            It should be 8-32 characters long.
            Passwords can contain uppercase and lowercase letters, as well as digits from 0 to 9.
            Plaintext should never be stored here. Argon2 is used for secure hashing.
            May include hyphen (-), plus (+), comma (,), period (.), at symbol (@), underscore (_),
            dollar sign ($), percent (%), ampersand (&), asterisk (*), hash (#),
            exclamation mark (!), caret (^), equals (=), forward slash (/), question mark (?).

        gender (Integer, not null, default=GenderType.OTHER):
            Represents the vendor's gender. Mapped from the `GenderType` enum.

        description (TEXT, nullable):
            Optional description or notes about the vendor.
            Maximum 1024 characters long.

        type (Integer, not null, default=VendorType.NORMAL):
            Role type of the vendor. Mapped from the `VendorType` enum.

        full_name (TEXT, nullable):
            Full name of the vendor.
            Maximum 32 characters long.

        status (Integer, not null, default=AccountStatus.ACTIVE):
            Indicates the account status. Mapped from the `AccountStatus` enum.

        phone_number (TEXT, nullable):
            Contact number of the vendor.
            Maximum 32 characters long.
            Saved and processed in RFC 3966 format (https://datatracker.ietf.org/doc/html/rfc3966).
            Example: "+1-202-555-0143"

        email_id (TEXT, nullable):
            Email address of the vendor.
            Maximum 256 characters long.
            Enforce the format prescribed by RFC 5322 (https://en.wikipedia.org/wiki/Email_address).

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the vendor's profile record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp of when the vendor account was created.
    """

    __tablename__ = "vendor"
    __table_args__ = (UniqueConstraint("username", "business_id"),)

    id = Column(Integer, primary_key=True)
    business_id = Column(
        Integer,
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    username = Column(String(32), nullable=False)
    password = Column(TEXT, nullable=False)
    gender = Column(Integer, nullable=False, default=GenderType.OTHER)
    description = Column(TEXT)
    type = Column(Integer, nullable=False, default=VendorType.NORMAL)
    full_name = Column(TEXT)
    status = Column(Integer, nullable=False, default=AccountStatus.ACTIVE)
    phone_number = Column(TEXT)
    email_id = Column(TEXT)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


@event.listens_for(Vendor, "before_insert")
@event.listens_for(Vendor, "before_update")
def preprocess_vendor_password(
    mapper: Mapper, connection: Connection, target: Vendor
) -> None:
    """Event listener to hash the vendor password before insertion or update."""

    history = inspect(target).attrs.password.history
    if history.has_changes() and target.password:
        target.password = argon2.make_password(target.password)


class VendorImage(ORMbase):
    """
    Represents an uploaded image associated with a specific vendor.

    Each record stores metadata about an image file uploaded for a vendor,
    allowing for management, retrieval, and replacement of profile or related images.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the vendor image.

        business_id (Integer, not null):
            Foreign key referencing `business.id` to whom this image belongs.
            Cascades on delete — if the business is removed, related image is deleted.

        vendor_id (Integer, not null, unique):
            Foreign key referencing `vendor.id` to whom this image belongs.
            Cascades on delete — if the vendor is removed, related image is deleted.

        file_name (String(128), not null):
            Original name of the uploaded image file, including extension.

        file_size (Integer, not null):
            Size of the uploaded file in bytes.

        file_type (String(128), not null):
            MIME type of the uploaded file (e.g., "image/jpeg", "image/png").

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the image record was initially created.
    """

    __tablename__ = "vendor_image"

    id = Column(Integer, primary_key=True)
    business_id = Column(
        Integer,
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_id = Column(
        Integer,
        ForeignKey("vendor.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # File metadata
    file_name = Column(String(128), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_type = Column(String(128), nullable=False)
    # Metadata
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class Wallet(ORMbase):
    """
    Represents a digital wallet tied to an associated object (e.g: company, business).

    Wallet lifecycle is governed by application logic, foreign-keys, and DB triggers.
    Deletion is blocked if balance not zero or if transfers reference the wallet.
    Remove unused wallets explicitly (or via a data-cleaner).

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the wallet.

        name (String(32), not null):
            Name of the wallet.
            Must not be null.
            Maximum 32 characters.

        balance (Numeric(10, 2), not null):
            The current balance of the wallet.
            Must be zero before deletion is permitted.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the wallet record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the wallet was created.
    """

    __tablename__ = "wallet"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_wallet_balance_non_negative"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False)
    balance = Column(Numeric(10, 2), nullable=False, default=0)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class CompanyWallet(ORMbase):
    """
    Represents a company's wallet association used for company-level financial operations.

    This table links a company to a wallet. Each company may have one wallet.
    Deletions cascade to maintain referential integrity; wallet deletion should
    be guarded by business rules (balance must be zero).

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the company-wallet entry.

        wallet_id (Integer, not null):
            Foreign key referencing `wallet.id`.
            Cascades on delete — if the wallet is removed, related entries are deleted.

        company_id (Integer, not null, unique):
            Foreign key referencing `company.id`.
            Each company can have only one wallet.
            Cascades on delete — if the company is removed, related mappings are deleted.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the record was created.
    """

    __tablename__ = "company_wallet"

    id = Column(Integer, primary_key=True)
    wallet_id = Column(
        Integer,
        ForeignKey("wallet.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Metadata
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class BusinessWallet(ORMbase):
    """
    Represents a business' wallet association used for business-level financial operations.

    This table links a business to a wallet. Each business may have one wallet.
    Deletions cascade to maintain referential integrity; wallet deletion should
    be guarded by business rules (balance must be zero).

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the business-wallet entry.

        wallet_id (Integer, not null):
            Foreign key referencing `wallet.id`.
            Cascades on delete — if the wallet is removed, related entries are deleted.

        business_id (Integer, not null, unique):
            Foreign key referencing `business.id`.
            Each business can have only one wallet.
            Cascades on delete — if the business is removed, related mappings are deleted.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the record was created.
    """

    __tablename__ = "business_wallet"

    id = Column(Integer, primary_key=True)
    wallet_id = Column(
        Integer,
        ForeignKey("wallet.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_id = Column(
        Integer,
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Metadata
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class BankAccount(ORMbase):
    """
    Represents a bank account used for financial transactions and settlements.

    This table stores essential details of a bank account, such as the account holder's name,
    account number, IFSC code, and bank/branch details. It can be associated with operators,
    companies, or businesses depending on the use case.

    Deleting a Company or Business removes related association entries via ON DELETE CASCADE,
    but does not delete the underlying BankAccount record. BankAccount entries must be deleted
    separately.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the bank account.

        bank_name (TEXT, not null):
            Name of the bank.
            Must not be null.
            Maximum 32 characters.

        branch_name (TEXT, nullable):
            Name of the bank branch.
            Maximum 32 characters.

        account_number (TEXT, not null):
            The bank account number.
            Must not be null.
            Maximum 32 characters.

        holder_name (TEXT, not null):
            Full name of the account holder.
            Must not be null.
            Maximum 32 characters.

        ifsc (TEXT, not null):
            IFSC code of the branch.
            Must not be null.
            Maximum 16 characters.

        account_type (Integer, not null, default=BankAccountType.OTHER):
            Type of the bank account; maps to `BankAccountType` enum.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the bank account record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the bank account record was created.
    """

    __tablename__ = "bank_account"

    id = Column(Integer, primary_key=True)
    bank_name = Column(TEXT, nullable=False)
    branch_name = Column(TEXT)
    account_number = Column(TEXT, nullable=False)
    holder_name = Column(TEXT, nullable=False)
    ifsc = Column(TEXT, nullable=False)
    account_type = Column(Integer, nullable=False, default=BankAccountType.OTHER)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class CompanyBankAccount(ORMbase):
    """
    Represents the association between a company and a bank account.

    This table maps a single bank account to a company for settlements and payouts.
    A bank account may be assigned to at most one company. Deletions cascade to
    preserve referential integrity.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the company-bank-account mapping.

        bank_account_id (Integer, not null, unique):
            Foreign key referencing `bank_account.id`.
            Each bank account may be assigned to only one company.
            Cascades on delete — if the bank account is removed, related mappings are deleted.

        company_id (Integer, not null):
            Foreign key referencing `company.id`.
            Identifies the owning company for this bank account.
            Cascades on delete — if the company is removed, related mappings are deleted.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the mapping record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the mapping record was created.
    """

    __tablename__ = "company_bank_account"

    id = Column(Integer, primary_key=True)
    bank_account_id = Column(
        Integer,
        ForeignKey("bank_account.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class BusinessBankAccount(ORMbase):
    """
    Represents the association between a business and a bank account.

    This table maps a single bank account to a business for settlements and payouts.
    A bank account may be assigned to at most one business. Deletions cascade to
    preserve referential integrity.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the business-bank-account mapping.

        bank_account_id (Integer, not null, unique):
            Foreign key referencing `bank_account.id`.
            Each bank account may be assigned to only one business.
            Cascades on delete — if the bank account is removed, related mappings are deleted.

        business_id (Integer, not null):
            Foreign key referencing `business.id`.
            Identifies the owning business for this bank account.
            Cascades on delete — if the business is removed, related mappings are deleted.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the mapping record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the mapping record was created.
    """

    __tablename__ = "business_bank_account"

    id = Column(Integer, primary_key=True)
    bank_account_id = Column(
        Integer,
        ForeignKey("bank_account.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    business_id = Column(
        Integer,
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class Vehicle(ORMbase):
    """
    Represents a Vehicle that is part of a company's fleet.

    This table stores registration and operational details and is uniquely
    identified by a combination of its registration number and company ID.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the vehicle.

        company_id (Integer, not null):
            Foreign key referencing `company.id` to whom this vehicle belongs.
            Cascades on delete — if the company is removed, related vehicles are deleted.

        registration_number (String(16), not null):
            This should be an immutable value.
            Vehicle registration number.
            Must be unique per company and non-null.
            Indexed for fast lookup.

        name (String(32),  not null):
            Name or model of the vehicle.
            Maximum 32 characters long.

        capacity (Integer, not null):
            Seating or passenger capacity of the vehicle.

        manufactured_on (DateTime, nullable):
            Manufacture date of the vehicle.

        insurance_upto (DateTime, nullable):
            Date until which the vehicle is insured.

        pollution_upto (DateTime, nullable):
            Date until which the vehicle's pollution certificate is valid

        fitness_upto (DateTime, nullable):
            Date until which the vehicle's fitness certificate is valid.

        road_tax_upto (DateTime, nullable):
            Date until which the vehicle's road tax is paid.

        status (Integer, not null, default=VehicleStatus.ACTIVE):
            Verification status of the vehicle. Mapped from the `VehicleStatus` enum.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the vehicle record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the vehicle record was created.
    """

    __tablename__ = "vehicle"
    __table_args__ = (UniqueConstraint("registration_number", "company_id"),)

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    registration_number = Column(String(16), nullable=False, index=True)
    name = Column(String(32), nullable=False, index=True)
    capacity = Column(Integer, nullable=False)
    manufactured_on = Column(DateTime(timezone=True))
    insurance_upto = Column(DateTime(timezone=True))
    pollution_upto = Column(DateTime(timezone=True))
    fitness_upto = Column(DateTime(timezone=True))
    road_tax_upto = Column(DateTime(timezone=True))
    status = Column(Integer, nullable=False, default=VehicleStatus.ACTIVE)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class VehicleImage(ORMbase):
    """
    Represents an uploaded image associated with a specific vehicle.

    Each record stores metadata about an image file uploaded for a vehicle,
    allowing for management, retrieval, and storage of vehicle photos.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the vehicle image.

        company_id (Integer, not null):
            Foreign key referencing `company.id` to whom this image belongs.
            Cascades on delete — if the company is removed, related images are deleted.

        vehicle_id (Integer, not null):
            Foreign key referencing `vehicle.id` to whom this image belongs.
            Cascades on delete — if the vehicle is removed, related images are deleted.

        file_name (String(128), not null):
            Original name of the uploaded image file, including extension.

        file_size (Integer, not null):
            Size of the uploaded file in bytes.

        file_type (String(128), not null):
            MIME type of the uploaded file (e.g., "image/jpeg", "image/png").

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the image record was initially created.
    """

    __tablename__ = "vehicle_image"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vehicle_id = Column(
        Integer,
        ForeignKey("vehicle.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # File metadata
    file_name = Column(String(128), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_type = Column(String(128), nullable=False)
    # Metadata
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class Route(ORMbase):
    """
    Represents a route associated with a company.

    This table stores high-level metadata about a route, such as its name,
    owning company, scheduled start time, and status. Relationships to
    specific landmarks or stops, if any, are managed outside this model.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the route.

        company_id (Integer, not null):
            Foreign key referencing `company.id` to whom this route belongs.
            Cascades on delete — if the company is removed, related routes are deleted.

        name (String(4096), not null):
            Name of the route.
            Maximum 4096 characters long.

        start_time (Time, not null):
            The time of day when the route operation starts.
            Used for scheduling and time-based operations.

        status (Integer, not null, default=RouteStatus.INVALID):
            Route validation/status. Mapped from the `RouteStatus` enum.


        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the route record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the route record was created.
    """

    __tablename__ = "route"
    __table_args__ = (UniqueConstraint("name", "company_id"),)

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(4096), nullable=False)
    start_time = Column(Time(timezone=True), nullable=False)
    status = Column(Integer, nullable=False, default=RouteStatus.INVALID)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())


class LandmarkInRoute(ORMbase):
    """
    Represents a landmark positioned within a specific route.

    This table defines the sequence and timing metadata of landmarks along a route.
    It helps determine the structure and scheduling of transportation or logistics operations.

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the landmark-in-route

        company_id (Integer, not null):
            Foreign key referencing `company.id` that operates on the route.
            Cascades on delete — if the company is removed, related landmark in routes are deleted.

        route_id (Integer, not null):
            Foreign key referencing `route.id` that this landmark is part of.
            Cascades on delete — if the route is removed, related landmarks in routes are deleted.

        landmark_id (Integer, not null):
            Foreign key referencing `landmark.id` that this landmark is part of.
            Cascades on delete — if the landmark is removed, related landmarks in routes are deleted.

        distance_from_start (Integer, unique, not null):
            Distance in meters from the starting landmark of the route.
            Used to determine ordering and physical spacing.

        arrival_delta (Integer):
            Time in minutes expected to arrive at this landmark from the start of the route.
            Helps in estimating arrival schedules for route traversal.

        departure_delta (Integer):
            Time in minutes expected to depart from this landmark after the route starts.
            Used to define dwell times or stop durations.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp automatically updated whenever the landmark in route record is modified.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when the landmark in route record was created..
    """

    __tablename__ = "landmark_in_route"
    __table_args__ = (UniqueConstraint("route_id", "distance_from_start"),)

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer,
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    route_id = Column(
        Integer, ForeignKey("route.id", ondelete="CASCADE"), nullable=False, index=True
    )
    landmark_id = Column(Integer, ForeignKey("landmark.id"))
    distance_from_start = Column(Integer, nullable=False)
    arrival_delta = Column(Integer, nullable=False)
    departure_delta = Column(Integer, nullable=False)
    # Metadata
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())
    created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())
