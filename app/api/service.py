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
from datetime import timedelta
from fastapi import status, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive
from app.src.db import (
    SessionLocal,
    ExecutiveToken,
    Service,
    Route,
    LandmarkInRoute,
    Landmark,
    Fare,
    Vehicle,
    Company,
)
from app.src import exceptions
from app.src.functions import get_request_info, get_executive_roles, enum_str
from app.src.validators import validate_id, verify_token, verify_permission
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.enums import (
    VehicleStatus,
    CompanyStatus,
    RouteStatus,
    TicketingMode,
)
from app.src.regex import VEHICLE_NUMBER_PATTERN
from app.src.constants import TMZ_PRIMARY
from app.src.digital_ticket.v1 import TicketCreator

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


# Input Forms
class CreateFormForOP(BaseModel):
    route: int = Field()
    fare: int = Field()
    bus_id: int = Field()
    ticket_mode: TicketingMode = Field(description=enum_str(TicketingMode),default=TicketingMode.HYBRID)
    registration_number: str = Field(pattern=VEHICLE_NUMBER_PATTERN, max_length=16)
    starting_at: datetime = Field()


class CreateFormForEX(CreateFormForOP):
    company_id: int = Field()

# Functions
def create_service(session: Session, route: Route, vehicle: Vehicle, fare: Fare, company: Company, fParam: CreateFormForOP):
    # Verify status
    if vehicle.status != VehicleStatus.ACTIVE:
        raise exceptions.InactiveResource(Vehicle)
    if company.status != CompanyStatus.VERIFIED:
        raise exceptions.InactiveResource(Company)
    if route.status != RouteStatus.VALID:
        raise exceptions.InactiveResource(Route)

    # Validate starting date
    ISTStartingAt = fParam.starting_at.astimezone(TMZ_PRIMARY)
    ISTDate = ISTStartingAt.date()
    currentDate = datetime.now(TMZ_PRIMARY).date()
    if ISTDate not in {currentDate, currentDate + timedelta(days=1)}:
        raise exceptions.InvalidValue(Service.starting_at)

    # Get starting_at and ending_at
    landmarksInRoute = (
        session.query(LandmarkInRoute)
        .filter(LandmarkInRoute.route_id == route.id)
        .order_by(LandmarkInRoute.distance_from_start.desc())
        .all()
    )
    if not landmarksInRoute:
        raise exceptions.InvalidRoute()
    lastLandmark = landmarksInRoute[0]
    ending_at = fParam.starting_at + timedelta(seconds=lastLandmark.arrival_delta)

    firstLandmark = (
        session.query(Landmark)
        .join(LandmarkInRoute, Landmark.id == LandmarkInRoute.landmark_id)
        .filter(LandmarkInRoute.route_id == route.id)
        .order_by(LandmarkInRoute.distance_from_start.asc())
        .first()
    )
    lastLandmarkObj = (
        session.query(Landmark)
        .join(LandmarkInRoute, Landmark.id == LandmarkInRoute.landmark_id)
        .filter(LandmarkInRoute.route_id == route.id)
        .order_by(LandmarkInRoute.distance_from_start.desc())
        .first()
    )
    if not firstLandmark or not lastLandmarkObj:
        raise exceptions.InvalidAssociation(LandmarkInRoute.landmark_id, Service.route)

    # Create service name using TMZ_PRIMARY time for display
    startingAt = ISTStartingAt.strftime("%Y-%m-%d %-I:%M %p")
    name = f"{startingAt} {firstLandmark.name} -> {lastLandmarkObj.name} ({vehicle.registration_number})"

    # Generate route data
    routeData = jsonable_encoder(route)
    routeData["landmark"] = []
    for lm in landmarksInRoute:
        routeData["landmark"].append(jsonable_encoder(lm))

    # Generate fare data
    fareData = jsonable_encoder(fare)

    # Generate keys
    ticket_Creator = TicketCreator()
    privateKey = ticket_Creator.getPEMprivateKeyBytes()
    publicKey = ticket_Creator.getPEMpublicKeyBytes()

    service = Service(
        company_id=fParam.company_id,
        ticket_mode=fParam.ticket_mode,
        vehicle_id=fParam.bus_id,
        name=name,
        starting_at=fParam.starting_at,
        ending_at=ending_at,
        route=routeData,
        fare=fareData,
        private_key=privateKey,
        public_key=publicKey,
        registration_number=fParam.registration_number,
    )
    session.add(service)
    session.commit()
    session.refresh(service)

    serviceData = jsonable_encoder(service, exclude={"private_key"})
    return serviceData


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    "/company/service",
    tags=["Service"],
    response_model=ServiceSchema,
    status_code=status.HTTP_201_CREATED,
    responses={},
    description=(
        """
        Create a new service for a specified company.
        """
    ),
)
async def create_service_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY_SERVICE)

        company = session.query(Company).filter(Company.id == form_param.company_id).first()
        if company is None:
            raise exceptions.UnknownValue(Service.company_id)
        vehicle = session.query(Vehicle).filter(Vehicle.id == form_param.bus_id).first()
        if vehicle is None:
            raise exceptions.UnknownValue(Service.vehicle_id)
        route = session.query(Route).filter(Route.id == form_param.route).first()
        if route is None:
            raise exceptions.UnknownValue(Service.route)
        fare = session.query(Fare).filter(Fare.id == form_param.fare).first()
        if fare is None:
            raise exceptions.UnknownValue(Service.fare)

        # Associations
        if vehicle.company_id != company.id:
            raise exceptions.InvalidAssociation(Service.vehicle_id, Service.company_id)
        if route.company_id != company.id:
            raise exceptions.InvalidAssociation(Service.route, Service.company_id)
        if fare.scope != 1:  # FareScope.GLOBAL == 1
            if fare.company_id != company.id:
                raise exceptions.InvalidAssociation(Service.fare, Service.company_id)

        serviceData = create_service(session, route, vehicle, fare, company, form_param)

        # Log and return
        log_event(token, request_info, serviceData)
        return serviceData
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()