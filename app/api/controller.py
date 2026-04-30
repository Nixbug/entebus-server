"""
FastAPI application instances for different user domains.

This module creates separate FastAPI apps for each type of user domain
(executive, vendor, operator, public) and tags each app with a corresponding
AppID for contextual request handling.It also includes routers for each app.
"""

from fastapi import FastAPI

from app.api import (
    business,
    bus_stop,
    company,
    duty,
    executive_account,
    executive_image,
    executive_role_map,
    executive_token,
    executive_role,
    fare,
    landmark,
    operator_account,
    operator_image,
    operator_role,
    operator_role_map,
    operator_token,
    paper_ticket,
    route,
    service,
    service_assignment,
    vehicle,
    vehicle_image,
    vendor_account,
    vendor_image,
    vendor_token,
    landmark_in_route,
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
app_executive.include_router(executive_role_map.route_executive)
app_executive.include_router(executive_account.route_executive)
app_executive.include_router(executive_image.route_executive)
app_executive.include_router(landmark.route_executive)
app_executive.include_router(bus_stop.route_executive)
app_executive.include_router(operator_token.route_executive)
app_executive.include_router(company.route_executive)
app_executive.include_router(business.route_executive)
app_executive.include_router(vendor_token.route_executive)
app_executive.include_router(operator_account.route_executive)
app_executive.include_router(vendor_account.route_executive)
app_executive.include_router(operator_role.route_executive)
app_executive.include_router(operator_role_map.route_executive)
app_executive.include_router(operator_image.route_executive)
app_executive.include_router(vendor_image.route_executive)
app_executive.include_router(vehicle.route_executive)
app_executive.include_router(vehicle_image.route_executive)
app_executive.include_router(route.route_executive)
app_executive.include_router(landmark_in_route.route_executive)
app_executive.include_router(fare.route_executive)
app_executive.include_router(duty.route_executive)
app_executive.include_router(service.route_executive)
app_executive.include_router(service_assignment.route_executive)
app_executive.include_router(paper_ticket.route_executive)
app_executive.include_router(duty.route_executive)


# ------------------------------------------------------
# Vendor routers
# ------------------------------------------------------
app_vendor.include_router(vendor_token.route_vendor)
app_vendor.include_router(vendor_account.route_vendor)
app_vendor.include_router(vendor_image.route_vendor)
app_vendor.include_router(business.route_vendor)
app_vendor.include_router(landmark.route_vendor)
app_vendor.include_router(bus_stop.route_vendor)
app_vendor.include_router(vehicle.route_vendor)
app_vendor.include_router(route.route_vendor)
app_vendor.include_router(landmark_in_route.route_vendor)
app_vendor.include_router(fare.route_vendor)
app_vendor.include_router(service.route_vendor)


# ------------------------------------------------------
# Operator routers
# ------------------------------------------------------
app_operator.include_router(operator_token.route_operator)
app_operator.include_router(landmark.route_operator)
app_operator.include_router(bus_stop.route_operator)
app_operator.include_router(company.route_operator)
app_operator.include_router(operator_account.route_operator)
app_operator.include_router(operator_role.route_operator)
app_operator.include_router(operator_role_map.route_operator)
app_operator.include_router(operator_image.route_operator)
app_operator.include_router(vehicle.route_operator)
app_operator.include_router(vehicle_image.route_operator)
app_operator.include_router(route.route_operator)
app_operator.include_router(landmark_in_route.route_operator)
app_operator.include_router(fare.route_operator)
app_operator.include_router(duty.route_operator)
app_operator.include_router(service.route_operator)
app_operator.include_router(service_assignment.route_operator)
app_operator.include_router(paper_ticket.route_operator)
app_operator.include_router(duty.route_operator)


# ------------------------------------------------------
# Public routers
# ------------------------------------------------------
app_public.include_router(landmark.route_public)
app_public.include_router(bus_stop.route_public)
app_public.include_router(company.route_public)
app_public.include_router(business.route_public)
app_public.include_router(vehicle.route_public)
app_public.include_router(vehicle_image.route_public)
app_public.include_router(route.route_public)
app_public.include_router(landmark_in_route.route_public)
app_public.include_router(service.route_public)
