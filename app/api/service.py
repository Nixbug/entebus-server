"""
Service API Router for EnteBus.

Provides endpoints for managing services, including creation,
Uses Pydantic schemas for input validation and structured output.
Endpoints for update, deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime
from alembic.environment import Any, Dict
from fastapi import APIRouter
from pydantic import BaseModel, Field



route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class MaskedServiceSchema(BaseModel):
    """Schema for service response without revealing all details."""

    id : int
    company_id : int
    name : str
    status : int 
    registration_number : str
    starting_at : datetime
    ending_at : datetime


class ServiceSchema(MaskedServiceSchema):
    """Detailed schema for service response."""

    route : Dict[str, Any]
    fare : Dict[str, Any]
    vehicle_id : int
    ticket_mode : int
    remark : str | None
    started_on : datetime | None
    finished_on : datetime | None
    updated_on : datetime | None
    created_on : datetime