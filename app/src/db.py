"""
Database Setup & ORM Models for Entebus Server.

This module:
- Configures the SQLAlchemy engine, session factory, and base class.
- Defines ORM models for core entities.

All ORM models should inherit from `ORMbase`.
"""

from sqlalchemy import (
    create_engine,
    Boolean,
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

        permissions (ARRAY(String), not null):
            List of permissions associated with the role.
            Each permission should be a string representing a specific action or resource.

        updated_on (DateTime, nullable, onupdate=func.now()):
            Timestamp of the last update to the role's permissions.

        created_on (DateTime, not null, default=func.now()):
            Timestamp indicating when this role was created.
    """

    __tablename__ = "executive_role"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False, unique=True)
    permissions = Column(ARRAY(String), nullable=False, default=list)
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

    This table stores unique access tokens mapped to executives along with
    details about the device or client used and timestamps for auditing.
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
            OAuth access tokens are enforce the format prescribed by RFC 6749 format (https://datatracker.ietf.org/doc/html/rfc6749)

        refresh_token (String, not null, unique, default=lambda: token_hex(32)):
            Securely generated 64-character hexadecimal access token.
            Used to refresh the access token when needed.
            OAuth refresh tokens are enforce the format prescribed by RFC 6749 format (https://datatracker.ietf.org/doc/html/rfc6749)

        expires_in (Integer, not null):
            Token expiration time in seconds.
            Defines the duration after which the token becomes invalid.

        expires_at (DateTime, not null):
            Defines the date and time after which the token becomes invalid.

        platform_type (Integer, nullable, default=PlatformType.OTHER):
            Enum value indicating the client platform type.

        client_details (TEXT, nullable):
            Description of the client device or environment.
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
    expires_in = Column(Integer, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
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


class OperatorToken:
    pass


class VendorToken:
    pass
