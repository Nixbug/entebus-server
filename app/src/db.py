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
    Index,
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
from app.src.enums import AccountStatus, GenderType, LandmarkType, PlatformType


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
    permissions = Column(JSONB, nullable=False, default=list)
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


class OperatorToken:
    pass


class OperatorRole:
    pass


class OperatorRoleMap:
    pass


class VendorToken:
    pass


class VendorRole:
    pass


class VendorRoleMap:
    pass


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

    Columns:
        id (Integer, unique, not null):
            Primary identifier for the bus stop.

        name (String(128), not null):
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
            enforced through a unique constraint on (`location`, `landmark_id`).

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

    __table_args__ = (UniqueConstraint(location, landmark_id),)
