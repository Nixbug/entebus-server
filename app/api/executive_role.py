from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# Permissions models
class Action(str, Enum):
    """Enumerated actions used across permission sets."""

    fetch = "fetch"
    create = "create"
    update = "update"
    delete = "delete"
    sudo = "sudo"


class CRUDPermission(BaseModel):
    """Generic CRUD permission set — reused by most entities."""

    fetch: Optional[bool] = Field(False, description="Allow fetching/viewing")
    create: Optional[bool] = Field(False, description="Allow creation")
    update: Optional[bool] = Field(False, description="Allow updating")
    delete: Optional[bool] = Field(False, description="Allow deletion")
    sudo: Optional[bool] = Field(False, description="Superuser-like elevated access")


class TokenPermission(BaseModel):
    """Specialized permissions for tokens (only fetch)."""

    fetch: Optional[bool] = Field(False, description="Allow fetching access tokens")


class PermissionsModel(BaseModel):
    """Top-level permission model for an ExecutiveRole.

    Each attribute represents permissions for a domain/resource in the system.
    Fields are optional; absence implies no permissions granted. Use this
    model to validate and document the structure stored in the
    `executive_role.permissions` JSONB column.
    """

    # Token-related
    executive_token: Optional[TokenPermission] = None
    operator_token: Optional[TokenPermission] = None
    vendor_token: Optional[TokenPermission] = None

    # Executive management
    executive: Optional[CRUDPermission] = None
    executive_role: Optional[CRUDPermission] = None

    # Operations / Transit
    landmark: Optional[CRUDPermission] = None
    bus_stop: Optional[CRUDPermission] = None
    global_fare: Optional[CRUDPermission] = None
    company: Optional[CRUDPermission] = None
    operator: Optional[CRUDPermission] = None
    operator_role: Optional[CRUDPermission] = None
    bus: Optional[CRUDPermission] = None
    local_fare: Optional[CRUDPermission] = None
    route: Optional[CRUDPermission] = None
    schedule: Optional[CRUDPermission] = None
    service: Optional[CRUDPermission] = None
    duty: Optional[CRUDPermission] = None
    business: Optional[CRUDPermission] = None
    vendor: Optional[CRUDPermission] = None
    vendor_role: Optional[CRUDPermission] = None
