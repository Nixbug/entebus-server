from typing import Optional
from pydantic import BaseModel, Field


## Permission Schemas
class CRUDPermission(BaseModel):
    """Generic CRUD permission set — reused by most entities."""

    fetch: Optional[bool] = Field(True, description="Allow fetching/viewing")
    create: Optional[bool] = Field(True, description="Allow creation")
    update: Optional[bool] = Field(True, description="Allow updating")
    delete: Optional[bool] = Field(True, description="Allow deletion")


class PartialPermission(BaseModel):
    """Generic partial permissions."""

    fetch: Optional[bool] = Field(True, description="Allow fetching/viewing")
    update: Optional[bool] = Field(True, description="Allow updating")


class FetchPermission(BaseModel):
    """Specialized permission to fetch."""

    fetch: Optional[bool] = Field(True, description="Allow fetching details")


class LandmarkPermission(FetchPermission):
    """Landmark related permissions."""

    bus_stop: Optional[FetchPermission] = None


class OperatorPermission(CRUDPermission):
    """Operator related permissions."""

    role: Optional[CRUDPermission] = None
    token: Optional[FetchPermission] = None


class ServicePermission(CRUDPermission):
    """Service related permissions."""

    duty: Optional[CRUDPermission] = None


class CompanyPermission(PartialPermission):
    """Company related permissions."""

    bus: Optional[CRUDPermission] = None
    fare: Optional[CRUDPermission] = None
    route: Optional[CRUDPermission] = None
    operator: Optional[OperatorPermission] = None
    service: Optional[ServicePermission] = None


class PermissionsModel(BaseModel):
    """Top-level hierarchical permission structure for an OperatorRole."""

    landmark: Optional[LandmarkPermission] = None
    fare: Optional[FetchPermission] = None
    company: Optional[CompanyPermission] = None
