"""
Operator Role Permissions.

Provides Pydantic schemas to define the hierarchical structure of permissions
for operators within the system and permission paths for specific actions.
"""

from pydantic import BaseModel, Field
from enum import StrEnum


## Permission Paths
class PermissionPath(StrEnum):
    """Permission paths for operators."""

    UPDATE_COMPANY = "company.update"

    CREATE_COMPANY_VEHICLE = "company.vehicle.create"
    UPDATE_COMPANY_VEHICLE = "company.vehicle.update"
    DELETE_COMPANY_VEHICLE = "company.vehicle.delete"

    CREATE_COMPANY_FARE = "company.fare.create"
    UPDATE_COMPANY_FARE = "company.fare.update"
    DELETE_COMPANY_FARE = "company.fare.delete"

    CREATE_COMPANY_ROUTE = "company.route.create"
    UPDATE_COMPANY_ROUTE = "company.route.update"
    DELETE_COMPANY_ROUTE = "company.route.delete"

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

    CREATE_COMPANY_SERVICE_DUTY = "company.service.duty.create"
    UPDATE_COMPANY_SERVICE_DUTY = "company.service.duty.update"
    DELETE_COMPANY_SERVICE_DUTY = "company.service.duty.delete"

    CREATE_COMPANY_SCHEDULE = "company.schedule.create"
    UPDATE_COMPANY_SCHEDULE = "company.schedule.update"
    DELETE_COMPANY_SCHEDULE = "company.schedule.delete"


class CRUDPermission(BaseModel):
    """Generic CRUD permission set — reused by most entities."""

    create: bool = Field(description="Allow creation")
    update: bool = Field(description="Allow updating")
    delete: bool = Field(description="Allow deletion")


class TokenPermission(BaseModel):
    """Specialized permissions for token management."""

    fetch: bool = Field(description="Allow fetching token details")
    delete: bool = Field(description="Allow deleting token")


class CompanyOperatorPermissions(CRUDPermission):
    """Operator related permissions."""

    role: CRUDPermission
    token: TokenPermission


class ServicePermissions(CRUDPermission):
    """Service related permissions."""

    duty: CRUDPermission


class CompanyPermission(CRUDPermission):
    """Company related permissions (includes company-level CRUD)."""

    vehicle: CRUDPermission
    fare: CRUDPermission
    route: CRUDPermission
    operator: CompanyOperatorPermissions
    service: ServicePermissions
    schedule: CRUDPermission


class PermissionSchema(BaseModel):
    """Top-level hierarchical permission structure for an OperatorRole."""

    company: CompanyPermission
