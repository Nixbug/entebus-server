from typing import Optional
from pydantic import BaseModel, Field


## Permission Schemas
class CRUDPermission(BaseModel):
    """Generic CRUD permission set — reused by most entities."""

    fetch: Optional[bool] = Field(False, description="Allow fetching/viewing")
    create: Optional[bool] = Field(False, description="Allow creation")
    update: Optional[bool] = Field(False, description="Allow updating")
    delete: Optional[bool] = Field(False, description="Allow deletion")


class PartialPermission(BaseModel):
    """Generic partial permissions."""

    fetch: Optional[bool] = Field(False, description="Allow fetching/viewing")
    update: Optional[bool] = Field(False, description="Allow updating")


class FetchPermission(BaseModel):
    """Specialized permission to fetch."""

    fetch: Optional[bool] = Field(False, description="Allow fetching details")


class LandmarkPermission(FetchPermission):
    """Landmark related permissions."""

    bus_stop: Optional[FetchPermission] = None


class VendorPermissions(CRUDPermission):
    """Vendor related permissions."""

    role: Optional[CRUDPermission] = None
    token: Optional[FetchPermission] = None


class BusinessPermission(PartialPermission):
    """Permissions for vendor entity."""

    vendor: Optional[VendorPermissions] = None


class CompanyPermission(FetchPermission):
    """Company related permissions."""

    bus: Optional[FetchPermission] = None
    fare: Optional[FetchPermission] = None
    route: Optional[FetchPermission] = None
    service: Optional[FetchPermission] = None


class PermissionsModel(BaseModel):
    """Top-level hierarchical permission structure for an VendorRole."""

    landmark: Optional[LandmarkPermission] = None
    fare: Optional[FetchPermission] = None
    business: Optional[BusinessPermission] = None
    company: Optional[CompanyPermission] = None
