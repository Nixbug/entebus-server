"""
Vendor Role Permissions.

Provides Pydantic schemas to define the hierarchical structure of permissions
for vendors within the system and permission paths for specific actions.
"""

from pydantic import Field
from enum import StrEnum

from app.src.permissions.base import PermissionBase


## Permission Paths
class PermissionPath(StrEnum):
    """Permission paths for vendors."""

    UPDATE_BUSINESS = "business.update"

    CREATE_BUSINESS_VENDOR = "business.vendor.create"
    UPDATE_BUSINESS_VENDOR = "business.vendor.update"
    DELETE_BUSINESS_VENDOR = "business.vendor.delete"

    CREATE_BUSINESS_VENDOR_ROLE = "business.vendor.role.create"
    UPDATE_BUSINESS_VENDOR_ROLE = "business.vendor.role.update"
    DELETE_BUSINESS_VENDOR_ROLE = "business.vendor.role.delete"

    FETCH_BUSINESS_VENDOR_TOKEN = "business.vendor.token.fetch"
    DELETE_BUSINESS_VENDOR_TOKEN = "business.vendor.token.delete"


class CRUDPermission(PermissionBase):
    """Generic CRUD permission set — reused by most entities."""

    create: bool = Field(description="Allow creation")
    update: bool = Field(description="Allow updating")
    delete: bool = Field(description="Allow deletion")


class TokenPermission(PermissionBase):
    """Specialized permissions for token management."""

    fetch: bool = Field(description="Allow fetching token details")
    delete: bool = Field(description="Allow deleting token")


class VendorPermissions(CRUDPermission):
    """Vendor related permissions."""

    role: CRUDPermission
    token: TokenPermission


class BusinessPermission(PermissionBase):
    """Business related permissions."""

    update: bool = Field(description="Allow updating business details")
    vendor: VendorPermissions


class PermissionSchema(PermissionBase):
    """Top-level hierarchical permission structure for a VendorRole."""

    business: BusinessPermission
