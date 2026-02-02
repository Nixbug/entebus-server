"""
Operator Token API Router for EnteBus.

Provides an endpoint for managing operator access tokens, including creation.
Uses Pydantic schemas for input validation and structured output.
Endpoints for refresh, deletion, and retrieval are planned for future implementation.
"""

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.src.db import Operator, SessionLocal
from app.src.urls import URL_OPERATOR_TOKEN
from app.src.enums import AccountStatus
from app.src import argon2

route_operator = APIRouter()
route_executive = APIRouter()