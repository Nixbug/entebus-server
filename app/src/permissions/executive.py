"""
Executive Role Permissions.

Provides Pydantic schemas to define the hierarchical structure of permissions
for executives within the system and permission paths for specific actions.
"""

from pydantic import Field
from enum import StrEnum

from app.src.permissions.base import PermissionBase


## Permission Paths
class PermissionPath(StrEnum):
    """Permission paths for executives."""

    CREATE_LANDMARK = "landmark.create"
    UPDATE_LANDMARK = "landmark.update"
    DELETE_LANDMARK = "landmark.delete"

    CREATE_BUS_STOP = "landmark.bus_stop.create"
    UPDATE_BUS_STOP = "landmark.bus_stop.update"
    DELETE_BUS_STOP = "landmark.bus_stop.delete"

    CREATE_FARE = "fare.create"
    UPDATE_FARE = "fare.update"
    DELETE_FARE = "fare.delete"

    CREATE_EXECUTIVE = "executive.create"
    UPDATE_EXECUTIVE = "executive.update"
    DELETE_EXECUTIVE = "executive.delete"

    CREATE_EXECUTIVE_ROLE = "executive.role.create"
    UPDATE_EXECUTIVE_ROLE = "executive.role.update"
    DELETE_EXECUTIVE_ROLE = "executive.role.delete"

    FETCH_EXECUTIVE_TOKEN = "executive.token.fetch"
    DELETE_EXECUTIVE_TOKEN = "executive.token.delete"

    CREATE_BUSINESS = "business.create"
    UPDATE_BUSINESS = "business.update"
    DELETE_BUSINESS = "business.delete"

    CREATE_BUSINESS_VENDOR = "business.vendor.create"
    UPDATE_BUSINESS_VENDOR = "business.vendor.update"
    DELETE_BUSINESS_VENDOR = "business.vendor.delete"

    CREATE_BUSINESS_VENDOR_ROLE = "business.vendor.role.create"
    UPDATE_BUSINESS_VENDOR_ROLE = "business.vendor.role.update"
    DELETE_BUSINESS_VENDOR_ROLE = "business.vendor.role.delete"

    FETCH_BUSINESS_VENDOR_TOKEN = "business.vendor.token.fetch"
    DELETE_BUSINESS_VENDOR_TOKEN = "business.vendor.token.delete"

    CREATE_COMPANY = "company.create"
    UPDATE_COMPANY = "company.update"
    DELETE_COMPANY = "company.delete"

    CREATE_COMPANY_VEHICLE = "company.vehicle.create"
    UPDATE_COMPANY_VEHICLE = "company.vehicle.update"
    DELETE_COMPANY_VEHICLE = "company.vehicle.delete"

    CREATE_COMPANY_FARE = "company.fare.create"
    UPDATE_COMPANY_FARE = "company.fare.update"
    DELETE_COMPANY_FARE = "company.fare.delete"

    CREATE_COMPANY_ROUTE = "company.route.create"
    UPDATE_COMPANY_ROUTE = "company.route.update"
    DELETE_COMPANY_ROUTE = "company.route.delete"

    CREATE_COMPANY_ROUTE_TRACE = "company.route.trace.create"
    UPDATE_COMPANY_ROUTE_TRACE = "company.route.trace.update"
    DELETE_COMPANY_ROUTE_TRACE = "company.route.trace.delete"

    CREATE_COMPANY_ROUTE_LOCATION_TRACE = "company.route.location.trace.create"

    CREATE_COMPANY_OPERATOR = "company.operator.create"
    UPDATE_COMPANY_OPERATOR = "company.operator.update"
    DELETE_COMPANY_OPERATOR = "company.operator.delete"

    CREATE_COMPANY_OPERATOR_ROLE = "company.operator.role.create"
    UPDATE_COMPANY_OPERATOR_ROLE = "company.operator.role.update"
    DELETE_COMPANY_OPERATOR_ROLE = "company.operator.role.delete"

    FETCH_COMPANY_OPERATOR_TOKEN = "company.operator.token.fetch"
    DELETE_COMPANY_OPERATOR_TOKEN = "company.operator.token.delete"

    CREATE_COMPANY_SERVICE = "company.service.create"
    UPDATE_COMPANY_SERVICE = "company.service.update"
    DELETE_COMPANY_SERVICE = "company.service.delete"

    UPDATE_COMPANY_SERVICE_DUTY = "company.service.duty.update"

    CREATE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.create"
    UPDATE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.update"
    DELETE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.delete"

    CREATE_COMPANY_SERVICE_STATEMENT = "company.service.statement.create"

    CREATE_COMPANY_SCHEDULE = "company.schedule.create"
    UPDATE_COMPANY_SCHEDULE = "company.schedule.update"
    DELETE_COMPANY_SCHEDULE = "company.schedule.delete"


## Permission Schemas
class CRUDPermission(PermissionBase):
    """Generic CRUD permission set — reused by most entities."""

    create: bool = Field(description="Allow creation")
    update: bool = Field(description="Allow updating")
    delete: bool = Field(description="Allow deletion")


class TokenPermission(PermissionBase):
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


class DutyPermission(PermissionBase):
    """Duty related permissions."""

    update: bool = Field(description="Allow updating duties")


class CreatePermission(PermissionBase):
    """Single action create permission."""

    create: bool = Field(description="Allow creation")


class ServicePermissions(CRUDPermission):
    """Service related permissions."""

    duty: DutyPermission
    assignment: CRUDPermission
    statement: CreatePermission


class CompanyPermissions(CRUDPermission):
    """Company related permissions."""

    vehicle: CRUDPermission
    fare: CRUDPermission
    route: CRUDPermission
    trace: CRUDPermission
    location_trace: CreatePermission
    operator: OperatorPermissions
    service: ServicePermissions
    schedule: CRUDPermission


class PermissionSchema(PermissionBase):
    """Top-level hierarchical permission structure for an ExecutiveRole."""

    landmark: LandmarkPermissions
    fare: CRUDPermission
    executive: ExecutivePermissions
    business: BusinessPermissions
    company: CompanyPermissions
