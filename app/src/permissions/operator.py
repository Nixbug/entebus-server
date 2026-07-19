"""
Operator Role Permissions.

Provides Pydantic schemas to define the hierarchical structure of permissions
for operators within the system and permission paths for specific actions.
"""

from pydantic import Field
from enum import StrEnum

from app.src.permissions.base import PermissionBase


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

    CREATE_COMPANY_TRACE = "company.trace.create"
    UPDATE_COMPANY_TRACE = "company.trace.update"
    DELETE_COMPANY_TRACE = "company.trace.delete"

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

    UPDATE_COMPANY_SERVICE_STATUS_TRANSITION = "company.service.status.update"

    UPDATE_COMPANY_SERVICE_DUTY = "company.service.duty.update"

    CREATE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.create"
    UPDATE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.update"
    DELETE_COMPANY_SERVICE_ASSIGNMENT = "company.service.assignment.delete"

    CREATE_COMPANY_SERVICE_TICKET = "company.service.ticket.create"

    CREATE_COMPANY_SERVICE_STATEMENT = "company.service.statement.create"

    CREATE_COMPANY_JOB = "company.job.create"
    UPDATE_COMPANY_JOB = "company.job.update"
    DELETE_COMPANY_JOB = "company.job.delete"


class CRUDPermission(PermissionBase):
    """Generic CRUD permission set — reused by most entities."""

    create: bool = Field(description="Allow creation")
    update: bool = Field(description="Allow updating")
    delete: bool = Field(description="Allow deletion")


class TokenPermission(PermissionBase):
    """Specialized permissions for token management."""

    fetch: bool = Field(description="Allow fetching token details")
    delete: bool = Field(description="Allow deleting token")


class DutyPermission(PermissionBase):
    """Duty related permissions."""

    update: bool = Field(description="Allow updating duties")


class CreatePermission(PermissionBase):
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
    status_transition: bool


class CompanyPermission(PermissionBase):
    """Company related permissions."""

    update: bool = Field(description="Allow updating company details")
    vehicle: CRUDPermission
    fare: CRUDPermission
    route: CRUDPermission
    trace: CRUDPermission
    operator: OperatorPermissions
    service: ServicePermissions
    job: CRUDPermission


class PermissionSchema(PermissionBase):
    """Top-level hierarchical permission structure for an OperatorRole."""

    company: CompanyPermission
