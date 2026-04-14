"""
Service API Router for EnteBus.

Provides endpoints for managing services, including creation,
Uses Pydantic schemas for input validation and structured output.
Endpoints for update, deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime
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
    starting_at : datetime
    ending_at : datetime