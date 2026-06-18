# class Job(ORMbase):
#     """
#     Represents a scheduled background job.

#     This table stores information about background jobs that are scheduled to run at specific times.

#     Columns:
#         id (Integer, unique, not null):
#             Primary identifier for the job record.

#         company_id (Integer, not null):
#             Foreign key referencing `company.id`.
#             Indicates the company associated with this job record.
#             Cascades on delete — if the company is removed, related job records are deleted.

#         name(String(32), not null):
#             Name of the job.
#             Maximum 32 characters long.

#         description (TEXT, nullable):
#             Optional description or notes about the job.
#             Maximum 1024 characters long.

#         job_type (Integer, not null, default=JobType.SERVICE_CREATION):
#             Type of the job. Mapped from the `JobType` enum.

#         recurrence_rule (TEXT, not null):
#             Required recurrence rule for the job, expressed in iCalendar RRULE (RFC5545) format.
#             Maximum 256 characters long.

#         trigger_at (Time(timezone=True), not null):
#             Time of the day when the job should be triggered.

#         triggering_mode (Integer, not null, default=TriggeringMode.AUTO):
#             Mode of triggering the job. Mapped from the `TriggeringMode` enum.

#         next_trigger_on (DateTime, nullable):
#             Timestamp indicating the next scheduled trigger time for the job.
#             Next trigger can become null if the job is completed or disabled, or if the recurrence rule does not allow for future triggers.

#         last_trigger_on (DateTime, nullable):
#             Timestamp indicating the last time the job was triggered.

#         trigger_from (DateTime, nullable):
#             Timestamp indicating the start of the period during which the job should be triggered.

#         trigger_till (DateTime, nullable):
#             Timestamp indicating the end of the period during which the job should be triggered.

#         updated_on (DateTime, nullable, onupdate=func.now()):
#         Timestamp automatically updated whenever the job record is modified.

#         created_on (DateTime, not null, default=func.now()):
#             Timestamp indicating when the job record was created.
#     """

#     __tablename__ = "job"
#     __table_args__ = (UniqueConstraint("name", "company_id"),)

#     id = Column(Integer, primary_key=True)
#     company_id = Column(
#         Integer,
#         ForeignKey("company.id", ondelete="CASCADE"),
#         nullable=False,
#         index=True,
#     )
#     name = Column(String(32), nullable=False)
#     description = Column(TEXT)
#     job_type = Column(Integer, nullable=False, default=JobType.SERVICE_CREATION)
#     recurrence_rule = Column(TEXT, nullable=False)
#     trigger_at = Column(Time(timezone=True), nullable=False)
#     triggering_mode = Column(Integer, nullable=False, default=TriggeringMode.AUTO)
#     next_trigger_on = Column(DateTime(timezone=True))
#     last_trigger_on = Column(DateTime(timezone=True))
#     trigger_from = Column(DateTime(timezone=True))
#     trigger_till = Column(DateTime(timezone=True))
#     # Metadata
#     updated_on = Column(DateTime(timezone=True), onupdate=func.now())
#     created_on = Column(DateTime(timezone=True), nullable=False, default=func.now())

"""
Job API Router for EnteBus.

Provides endpoints for managing jobs within the EnteBus system, including creating,
updating, deleting, and retrieving job information. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from sqlalchemy import or_, String
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from typing import List, Tuple
from sqlalchemy.orm import Session

from app.src.db import Company, ExecutiveToken, OperatorToken, Trace, SessionLocal
from app.src.description import Description
from app.src import exceptions
from app.src.enums import JobType, OrderIn, TriggeringMode
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    NameFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_name_filters,
    apply_updated_on_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    update_if_changed,
)
from app.src.openobserve import log_event
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_ROUTE_TRACE
from app.src.validators import (
    authorize_executive,
    authorize_operator,
    validate_id,
    verify_token,
)
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.api.bearer import oauth2_executive, bearer_operator

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class JobSchema(BaseModel):
    """Pydantic schema for job response."""

    id: int
    company_id: int
    name: str
    description: str | None = None
    job_type: int
    recurrence_rule: str
    trigger_at: str
    triggering_mode: int
    next_trigger_on: datetime | None = None
    last_trigger_on: datetime | None = None
    trigger_from: datetime | None = None
    trigger_till: datetime | None = None
    updated_on: datetime | None = None
    created_on: datetime | None = None


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new job for an operator."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    job_type: JobType = Field(default=JobType.SERVICE_CREATION)
    recurrence_rule: str = Field(min_length=1, max_length=256)
    trigger_at: datetime = Field()
    triggering_mode: TriggeringMode = Field(default=TriggeringMode.AUTO)
    trigger_from: datetime | None = None
    trigger_till: datetime | None = None


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new job for an executive."""

    company_id: int = Field()
