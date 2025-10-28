from datetime import datetime
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

from app.src.permissions.executive import PermissionsModel

route_executive = APIRouter()


## Output Schema
class ExecutiveRoleSchema(BaseModel):
    id: int
    name: str
    permissions: PermissionsModel
    created_on: datetime
    updated_on: Optional[datetime]
