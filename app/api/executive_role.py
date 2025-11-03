"""
Executive Role API Router for EnteBus.

Provides endpoints for managing executive roles, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

from app.src.permissions.executive import PermissionSchema

route_executive = APIRouter()


## Output Schema
class ExecutiveRoleSchema(BaseModel):
    id: int
    name: str
    permissions: PermissionSchema
    created_on: datetime
    updated_on: Optional[datetime]
