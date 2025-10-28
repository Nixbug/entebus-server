from typing import Optional
from pydantic import BaseModel, Field


## Permission Schemas
class CRUDPermission(BaseModel):
    """Generic CRUD permission set — reused by most entities."""

    create: Optional[bool] = Field(False, description="Allow creation")
    update: Optional[bool] = Field(False, description="Allow updating")
    delete: Optional[bool] = Field(False, description="Allow deletion")


class TokenPermission(BaseModel):
    """Specialized permissions for token management."""

    fetch: Optional[bool] = Field(False, description="Allow fetching token details")
    delete: Optional[bool] = Field(False, description="Allow deleting token")


class LandmarkPermissions(CRUDPermission):
    """Landmark related permissions."""

    bus_stop: Optional[CRUDPermission] = None


class ExecutivePermissions(CRUDPermission):
    """Executive related permissions."""

    role: Optional[CRUDPermission] = None
    token: Optional[TokenPermission] = None


class VendorPermissions(CRUDPermission):
    """Vendor related permissions."""

    role: Optional[CRUDPermission] = None
    token: Optional[TokenPermission] = None


class BusinessPermissions(CRUDPermission):
    """Business related permissions."""

    vendor: Optional[VendorPermissions] = None


class OperatorPermissions(CRUDPermission):
    """Operator related permissions."""

    role: Optional[CRUDPermission] = None
    token: Optional[TokenPermission] = None


class ServicePermissions(CRUDPermission):
    """Service related permissions."""

    duty: Optional[CRUDPermission] = None


class CompanyPermissions(CRUDPermission):
    """Company related permissions."""

    bus: Optional[CRUDPermission] = None
    fare: Optional[CRUDPermission] = None
    route: Optional[CRUDPermission] = None
    operator: Optional[OperatorPermissions] = None
    service: Optional[ServicePermissions] = None


class PermissionsModel(BaseModel):
    """Top-level hierarchical permission structure for an ExecutiveRole."""

    landmark: Optional[LandmarkPermissions] = None
    fare: Optional[CRUDPermission] = None
    executive: Optional[ExecutivePermissions] = None
    business: Optional[BusinessPermissions] = None
    company: Optional[CompanyPermissions] = None
