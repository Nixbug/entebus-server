"""
Commonly used query filters for the API.

This module defines reusable Pydantic models for filtering database queries.
"""

from datetime import datetime
from typing import List
from fastapi import Query
from pydantic import BaseModel, Field

from app.src.enums import GenderType, PlatformType
from app.src.functions import enum_str


class IDFilter(BaseModel):
    """Filter by ID."""

    id: int | None = Field(Query(default=None))
    id_ge: int | None = Field(Query(default=None))
    id_le: int | None = Field(Query(default=None))
    id_list: List[int] | None = Field(Query(default=None))


class CreatedOnFilter(BaseModel):
    """Filter by creation date."""

    created_on_ge: datetime | None = Field(Query(default=None))
    created_on_le: datetime | None = Field(Query(default=None))


class UpdatedOnFilter(BaseModel):
    """Filter by update date."""

    updated_on_ge: datetime | None = Field(Query(default=None))
    updated_on_le: datetime | None = Field(Query(default=None))


class PaginationFilter(BaseModel):
    """Query parameters for pagination."""

    offset: int = Field(Query(default=0, ge=0))
    limit: int = Field(Query(default=20, gt=0, le=100))


class ClientDataFilter(BaseModel):
    """Query parameters for client data."""

    platform_type: PlatformType | None = Field(
        Query(default=None, description=enum_str(PlatformType))
    )
    platform_type_list: List[PlatformType] | None = Field(
        Query(default=None, description=enum_str(PlatformType))
    )
    client_details: str | None = Field(Query(default=None))


class NameFilter(BaseModel):
    """Query parameters for name."""

    name: str | None = Field(Query(default=None))


class AccountDataFilter(BaseModel):
    """Query parameters for account data."""

    username: str | None = Field(Query(default=None))
    gender: GenderType | None = Field(
        Query(default=None, description=enum_str(GenderType))
    )
    full_name: str | None = Field(Query(default=None))
    email_id: str | None = Field(Query(default=None))
    phone_number: str | None = Field(Query(default=None))


class PictureFilter(BaseModel):
    """Query parameters for picture."""

    file_name: str | None = Field(Query(default=None))
    file_type: str | None = Field(Query(default=None))
    file_size_ge: int | None = Field(Query(default=None))
    file_size_le: int | None = Field(Query(default=None))
