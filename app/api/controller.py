"""
FastAPI application instances for different user domains.

This module creates separate FastAPI apps for each type of user domain
(executive, vendor, operator, public) and tags each app with a corresponding
AppID for contextual request handling.It also includes routers for each app.
"""

from fastapi import FastAPI

from app.api import (
    executive_token,
    executive_role,
)
from app.src.enums import AppID


# ------------------------------------------------------
# Create separate FastAPI apps for each user domain
# ------------------------------------------------------
app_executive = FastAPI(title="Executive APP")
app_vendor = FastAPI(title="Vendor APP")
app_operator = FastAPI(title="Operator APP")
app_public = FastAPI(title="Public APP")

# Tag each app with its AppID
app_executive.state.id = AppID.EXECUTIVE
app_vendor.state.id = AppID.VENDOR
app_operator.state.id = AppID.OPERATOR
app_public.state.id = AppID.PUBLIC


# ------------------------------------------------------
# Executive routers
# ------------------------------------------------------
app_executive.include_router(executive_token.route_executive)
app_executive.include_router(executive_role.route_executive)
