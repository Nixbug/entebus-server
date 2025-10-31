"""
Executive Role Permissions Schema.

Provides a set of Pydantic models to define the hierarchical structure of permissions
for executives within the system.
"""

from pydantic import BaseModel, Field
from enum import StrEnum


## Permission Paths
class PermissionPath(StrEnum):
    LANDMARK_CREATE = "landmark.create"
    LANDMARK_UPDATE = "landmark.update"
    LANDMARK_DELETE = "landmark.delete"

    LANDMARK_BUS_STOP_CREATE = "landmark.bus_stop.create"
    LANDMARK_BUS_STOP_UPDATE = "landmark.bus_stop.update"
    LANDMARK_BUS_STOP_DELETE = "landmark.bus_stop.delete"

    FARE_CREATE = "fare.create"
    FARE_UPDATE = "fare.update"
    FARE_DELETE = "fare.delete"

    EXECUTIVE_CREATE = "executive.create"
    EXECUTIVE_UPDATE = "executive.update"
    EXECUTIVE_DELETE = "executive.delete"

    EXECUTIVE_ROLE_CREATE = "executive.role.create"
    EXECUTIVE_ROLE_UPDATE = "executive.role.update"
    EXECUTIVE_ROLE_DELETE = "executive.role.delete"

    EXECUTIVE_TOKEN_FETCH = "executive.token.fetch"
    EXECUTIVE_TOKEN_DELETE = "executive.token.delete"

    BUSINESS_CREATE = "business.create"
    BUSINESS_UPDATE = "business.update"
    BUSINESS_DELETE = "business.delete"

    BUSINESS_VENDOR_CREATE = "business.vendor.create"
    BUSINESS_VENDOR_UPDATE = "business.vendor.update"
    BUSINESS_VENDOR_DELETE = "business.vendor.delete"

    BUSINESS_VENDOR_ROLE_CREATE = "business.vendor.role.create"
    BUSINESS_VENDOR_ROLE_UPDATE = "business.vendor.role.update"
    BUSINESS_VENDOR_ROLE_DELETE = "business.vendor.role.delete"

    BUSINESS_VENDOR_TOKEN_FETCH = "business.vendor.token.fetch"
    BUSINESS_VENDOR_TOKEN_DELETE = "business.vendor.token.delete"

    COMPANY_CREATE = "company.create"
    COMPANY_UPDATE = "company.update"
    COMPANY_DELETE = "company.delete"

    COMPANY_BUS_CREATE = "company.bus.create"
    COMPANY_BUS_UPDATE = "company.bus.update"
    COMPANY_BUS_DELETE = "company.bus.delete"

    COMPANY_FARE_CREATE = "company.fare.create"
    COMPANY_FARE_UPDATE = "company.fare.update"
    COMPANY_FARE_DELETE = "company.fare.delete"

    COMPANY_ROUTE_CREATE = "company.route.create"
    COMPANY_ROUTE_UPDATE = "company.route.update"
    COMPANY_ROUTE_DELETE = "company.route.delete"

    COMPANY_OPERATOR_CREATE = "company.operator.create"
    COMPANY_OPERATOR_UPDATE = "company.operator.update"
    COMPANY_OPERATOR_DELETE = "company.operator.delete"

    COMPANY_OPERATOR_ROLE_CREATE = "company.operator.role.create"
    COMPANY_OPERATOR_ROLE_UPDATE = "company.operator.role.update"
    COMPANY_OPERATOR_ROLE_DELETE = "company.operator.role.delete"

    COMPANY_OPERATOR_TOKEN_FETCH = "company.operator.token.fetch"
    COMPANY_OPERATOR_TOKEN_DELETE = "company.operator.token.delete"

    COMPANY_SERVICE_CREATE = "company.service.create"
    COMPANY_SERVICE_UPDATE = "company.service.update"
    COMPANY_SERVICE_DELETE = "company.service.delete"

    COMPANY_SERVICE_DUTY_CREATE = "company.service.duty.create"
    COMPANY_SERVICE_DUTY_UPDATE = "company.service.duty.update"
    COMPANY_SERVICE_DUTY_DELETE = "company.service.duty.delete"


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


class PermissionsSchema(BaseModel):
    """Top-level hierarchical permission structure for an ExecutiveRole."""

    landmark: LandmarkPermissions
    fare: CRUDPermission
    executive: ExecutivePermissions
    business: BusinessPermissions
    company: CompanyPermissions
