"""
Database Setup & ORM Models for Entebus Server.

This module:
- Configures the SQLAlchemy engine, session factory, and base class.
- Defines ORM models for core entities.

All ORM models should inherit from `ORMbase`.
"""

from sqlalchemy import (
    create_engine,
    ARRAY,
    TEXT,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from secrets import token_hex

from app.src.constants import (
    PSQL_DB_DRIVER,
    PSQL_DB_HOST,
    PSQL_DB_PASSWORD,
    PSQL_DB_NAME,
    PSQL_DB_PORT,
    PSQL_DB_USERNAME,
)
from app.src.enums import AccountStatus, GenderType, PlatformType


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
session_local = sessionmaker(bind=engine, expire_on_commit=False)


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
        id (Integer):
            Primary key. Unique identifier for the executive.

        username (String(32)):
            Unique username used for login or identification within the system.
            Ideally, the username shouldn't be changed once set.
            It should start with an alphabet (uppercase or lowercase).
            It can contain uppercase and lowercase letters, as well as digits from 0 to 9.
            It should be 4-32 characters long.
            May include hyphen (-), period (.), at symbol (@), and underscore (_).
            Must not be null and unique.

        password (TEXT):
            Hashed password used for authentication.
            It should be 8-32 characters long.
            Passwords can contain uppercase and lowercase letters, as well as digits from 0 to 9.
            Plaintext should never be stored here. Argon2 is used for secure hashing.
            May include hyphen (-), plus (+), comma (,), period (.), at symbol (@), underscore (_),
            dollar sign ($), percent (%), ampersand (&), asterisk (*), hash (#),
            exclamation mark (!), caret (^), equals (=), forward slash (/), question mark (?).

        gender (Integer):
            Represents the executive's gender. Mapped from the `GenderType` enum.
            Defaults to `GenderType.OTHER`.

        full_name (TEXT):
            Full name of the executive. Optional field used for display and communication.
            Maximum 32 characters long.

        designation (TEXT):
            Job title or role description of the executive. Optional field used for display and communication.
            Maximum 32 characters long.

        status (Integer):
            Indicates the account status.
            Mapped from the `AccountStatus` enum. Defaults to `AccountStatus.ACTIVE`.

        phone_number (TEXT):
            Optional contact number of the executive.
            Maximum 32 characters long.
            Saved and processed in RFC3966 format (https://datatracker.ietf.org/doc/html/rfc3966).
            Phone number start with a plus sign followed by country code and local number.

        email_id (TEXT):
            Optional email address for communication and recovery purposes.
            Maximum 256 characters long.
            Enforce the format prescribed by RFC 5322 (https://en.wikipedia.org/wiki/Email_address).

        updated_on (DateTime):
            Timestamp of the last update to the executive's profile or credentials.
            Timestamp automatically updated whenever the executive's profile is modified.

        created_on (DateTime):
            Timestamp of when the executive account was created.
            Defaults to the current timestamp at insertion, Must be non-null.
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


class ExecutiveRole(ORMbase):
    """
    Represents a role or access level assigned to executives within the system.
    It is used to define permissions and roles for executives, specifying the actions
    they are permitted to perform within the system.

    This model stores information about different roles and their associated permissions,
    enabling fine-grained control over executive access and functionality within the system.

    Columns:
        id (Integer):
            Primary key. Unique identifier for this executive role record.

        name (String(32)):
            Name or label for the role.
            It should be unique and should be 4-32 characters long.
            Must not be null and unique.

        permissions (ARRAY(String)):
            List of permissions associated with the role.
            Each permission should be a string representing a specific action or resource.
            Must not be null.

        created_on (DateTime):
            Timestamp indicating when this role was created.
            Defaults to the current timestamp at insertion, must be non-null.

        updated_on (DateTime):
            Timestamp of the last update to the role's permissions.
            Timestamp automatically updated whenever the role's permissions are modified.
    """

    __tablename__ = "executive_role"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False, unique=True)
    permissions = Column(ARRAY(String), nullable=False, default=list)
    created_on = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_on = Column(DateTime(timezone=True), onupdate=func.now())


class ExecutiveRoleMap(ORMbase):
    """
    Represents the mapping between executives and their assigned roles,
    enabling a many-to-many relationship between `executive` and `executive_role`.

    This table allows an executive to be assigned multiple roles and a role
    to be assigned to multiple executives. Useful for implementing a flexible
    Role-Based Access Control (RBAC) system.

    Columns:
        id (Integer):
            Primary key. Unique identifier for this role mapping record.

        role_id (Integer):
            Foreign key referencing `executive_role.id`.
            Specifies the role assigned to the executive.
            Cascades on delete — if the role is removed, related mappings are deleted.

        executive_id (Integer):
            Foreign key referencing `executive.id`.
            Identifies the executive receiving the role.
            Cascades on delete — if the executive is removed, related mappings are deleted.

        updated_on (DateTime):
            Timestamp automatically updated whenever the mapping record is modified.
            Useful for auditing or synchronization.

        created_on (DateTime):
            Timestamp indicating when this mapping was created.
            Defaults to the current timestamp at insertion, must be non-null.
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

    This table stores unique access tokens mapped to executives along with
    details about the device or client used and timestamps for auditing.
    Useful for session management, device tracking, and implementing token-based authentication.

    Columns:
        id (Integer):
            Primary key. Unique identifier for this executive token record.

        executive_id (Integer):
            Foreign key referencing `executive.id`.
            Identifies the executive associated with this token.
            Cascades on delete — if the executive is removed, related tokens are deleted.

        access_token (String):
            Unique, securely generated 64-character hexadecimal access token.
            Automatically generated using a secure random function.
            Used to authenticate the executive on subsequent requests.

        expires_in (Integer):
            Token expiration time in seconds.
            Defines the duration after which the token becomes invalid.

        expires_at (DateTime):
            Token expiration date and time.
            Defines the date and time after which the token becomes invalid.

        platform_type (Integer):
            Enum value indicating the client platform type.
            Defaults to `PlatformType.OTHER`.

        client_details (TEXT):
            Optional description of the client device or environment.
            May include user agent, app version, IP address, etc.
            Maximum 1024 characters long.

        updated_on (DateTime):
            Timestamp automatically updated whenever the token record is modified.
            Useful for auditing or tracking last usage.

        created_on (DateTime):
            Timestamp indicating when this token was created.
            Defaults to the current timestamp at insertion, must be non-null.
    """

    __tablename__ = "executive_token"

    id = Column(Integer, primary_key=True)
    executive_id = Column(
        Integer,
        ForeignKey("executive.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_token = Column(
        String(64), unique=True, nullable=False, default=lambda: token_hex(32)
    )
    expires_in = Column(Integer, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
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
        id (Integer):
            Primary key. Unique identifier for the executive image.

        executive_id (Integer):
            Foreign key referencing `executive.id` to whom this image belongs.
            Must be non-null and unique.
            Cascades on delete — if the executive is removed, related image is deleted.

        file_name (String(128)):
            Original name of the uploaded image file, including extension.
            Must be non-null.

        file_size (Integer):
            Size of the uploaded file in bytes.
            Must be non-null.

        file_type (String(128)):
            MIME type of the uploaded file (e.g., "image/jpeg", "image/png").
            Must be non-null.

        created_on (DateTime):
            Timestamp indicating when the image record was initially created.
            Defaults to the current timestamp at insertion, must be non-null.
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
