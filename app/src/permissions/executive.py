"""
Executive Role Permissions Schema.

Provides a set of Pydantic models to define the hierarchical structure of permissions
for executives within the system.
"""

from pydantic import BaseModel, Field


## Permission Schemas
class CRUDPermission(BaseModel):
    """Generic CRUD permission set — reused by most entities."""

    create: bool = Field(description="Allow creation")
    update: bool = Field(description="Allow updating")
    delete: bool = Field(description="Allow deletion")


class TokenPermission(BaseModel):
    """Specialized permissions for token management."""

    fetch: bool = Field(description="Allow fetching token details")
    delete: bool = Field(description="Allow deleting token")


class LandmarkPermissions(CRUDPermission):
    """Landmark related permissions."""

    bus_stop: CRUDPermission


class ExecutivePermissions(CRUDPermission):
    """Executive related permissions."""

    role: CRUDPermission
    token: TokenPermission


class VendorPermissions(CRUDPermission):
    """Vendor related permissions."""

    role: CRUDPermission
    token: TokenPermission


class BusinessPermissions(CRUDPermission):
    """Business related permissions."""

    vendor: VendorPermissions


class OperatorPermissions(CRUDPermission):
    """Operator related permissions."""

    role: CRUDPermission
    token: TokenPermission


class ServicePermissions(CRUDPermission):
    """Service related permissions."""

    duty: CRUDPermission


class CompanyPermissions(CRUDPermission):
    """Company related permissions."""

    bus: CRUDPermission
    fare: CRUDPermission
    route: CRUDPermission
    operator: OperatorPermissions
    service: ServicePermissions


class PermissionsModel(BaseModel):
    """Top-level hierarchical permission structure for an ExecutiveRole."""

    landmark: LandmarkPermissions
    fare: CRUDPermission
    executive: ExecutivePermissions
    business: BusinessPermissions
    company: CompanyPermissions
