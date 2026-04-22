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

    UPDATE_COMPANY_SERVICE_DUTY = "company.service.duty.update"

    CREATE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.create"
    UPDATE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.update"
    DELETE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.delete"

    CREATE_COMPANY_SERVICE_TICKET = "company.service.ticket.create"

    CREATE_COMPANY_SERVICE_STATEMENT = "company.service.statement.create"

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


class DutyPermission(BaseModel):
    """Duty related permissions."""

    update: bool = Field(description="Allow updating duties")


class CreatePermission(BaseModel):
    """Single action create permission."""

    create: bool = Field(description="Allow creation")


class OperatorPermissions(CRUDPermission):
    """Operator related permissions."""

    role: CRUDPermission
    token: TokenPermission


class ServicePermissions(CRUDPermission):
    """Service related permissions."""

    duty: DutyPermission
    assignment: CRUDPermission
    ticket: CreatePermission
    statement: CreatePermission


class CompanyPermission(BaseModel):
    """Company related permissions."""

    update: bool = Field(description="Allow updating company details")
    vehicle: CRUDPermission
    fare: CRUDPermission
    route: CRUDPermission
    operator: OperatorPermissions
    service: ServicePermissions
    schedule: CRUDPermission


class PermissionSchema(BaseModel):
    """Top-level hierarchical permission structure for an OperatorRole."""

    company: CompanyPermission
