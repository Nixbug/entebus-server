"""
Vendor Role Permissions.

Provides Pydantic schemas to define the hierarchical structure of permissions
for vendors within the system and permission paths for specific actions.
"""

from pydantic import BaseModel, Field
from enum import StrEnum


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


class CRUDPermission(BaseModel):
    """Generic CRUD permission set — reused by most entities."""

    create: bool = Field(description="Allow creation")
    update: bool = Field(description="Allow updating")
    delete: bool = Field(description="Allow deletion")


class TokenPermission(BaseModel):
    """Specialized permissions for token management."""

    fetch: bool = Field(description="Allow fetching token details")
    delete: bool = Field(description="Allow deleting token")


class VendorPermissions(CRUDPermission):
    """Vendor related permissions."""

    role: CRUDPermission
    token: TokenPermission


class BusinessPermission(BaseModel):
    """Business related permissions."""

    update: bool = Field(description="Allow updating business details")
    vendor: VendorPermissions


class PermissionSchema(BaseModel):
    """Top-level hierarchical permission structure for a VendorRole."""

    business: BusinessPermission
