"""
Duty API Router for EnteBus.

Provides endpoints for managing duties, including update.
Uses Pydantic schemas for input validation and structured output.
Endpoints for retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session


from app.api.bearer import bearer_operator, oauth2_executive
from app.src.db import (
    SessionLocal,
    OperatorToken,
    Duty,
    ExecutiveToken,
)
from app.src.enums import DutyStatus
from app.src.urls import URL_DUTY
from app.src.validators import verify_token, verify_permission, validate_id
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.openobserve import log_event
from app.src.functions import (
    fuse_exception_responses,
    get_operator_roles,
    get_request_info,
)
from app.src import exceptions

route_executive = APIRouter()
route_operator = APIRouter()
