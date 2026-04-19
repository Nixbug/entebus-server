"""
Service API Router for EnteBus.

Provides endpoints for managing services, including creation,
Uses Pydantic schemas for input validation and structured output.
Endpoints for update, deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel, Field
from datetime import timedelta
from fastapi import status, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.urls import URL_SERVICE
from app.src.db import (
    SessionLocal,
    ExecutiveToken,
    OperatorToken,
    Service,
    Route,
    LandmarkInRoute,
    Landmark,
    Fare,
    Vehicle,
    FareInService,
    VehicleInService,
    LandmarkInService,
    Company,
)
from app.src import exceptions
from app.src.functions import (
    get_request_info,
    get_executive_roles,
    get_operator_roles,
    fuse_exception_responses,
    enum_str,
)
from app.src.validators import (
    validate_id,
    verify_token,
    verify_permission,
)
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.enums import (
    VehicleStatus,
    CompanyStatus,
    RouteStatus,
    TicketingMode,
    ServiceStatus,
    FareScope,
)
from app.src.regex import NAME_PATTERN
from app.src.constants import TMZ_SECONDARY
from app.src.digital_ticket.v1 import TicketCreator

route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class ServiceSchema(BaseModel):
    """Schema for service response without revealing all details."""

    id: int
    company_id: int
    name: str
    status: int
    registration_number: str
    starting_landmark_id: int | None
    ending_landmark_id: int | None
    ticket_mode: int
    remark: str | None
    starting_at: datetime
    ending_at: datetime
    started_on: datetime | None
    finished_on: datetime | None
    updated_on: datetime | None
    created_on: datetime


# Input Forms
class CreateFormForOP(BaseModel):
    """Form data  for creating a new service by an operator."""

    route_id: int = Field()
    fare_id: int = Field()
    vehicle_id: int = Field()
    name: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=NAME_PATTERN
    )
    ticket_mode: TicketingMode = Field(
        description=enum_str(TicketingMode), default=TicketingMode.HYBRID
    )
    starting_at: datetime = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data  for creating a new service by an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new service."""

    pass


# Functions
def create_service(
    session: Session,
    route: Route,
    vehicle: Vehicle,
    fare: Fare,
    company: Company,
    form_param: CreateFormForOP,
) -> dict:
    """
    Creates a new service record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        route (Route): The route associated with the service.
        vehicle (Vehicle): The vehicle associated with the service.
        fare (Fare): The fare associated with the service.
        company (Company): The company associated with the service.
        form_param (CreateForm): Form data for creating a service.

    Returns:
        dict: The created service data.

    Raises:
        exceptions.InactiveResource: If the vehicle, company, or route is not active/verified/valid.
        exceptions.InvalidValue: If the starting date is not valid.
        exceptions.InvalidAssociation: If there are invalid associations between vehicle, route, fare, and company.
    """
    if vehicle.status != VehicleStatus.ACTIVE:
        raise exceptions.InactiveResource(Vehicle)
    if company.status != CompanyStatus.VERIFIED:
        raise exceptions.InactiveResource(Company)
    if route.status != RouteStatus.VALID:
        raise exceptions.InactiveResource(Route)

    # Validate starting date (treat naive datetimes as TMZ_SECONDARY)
    starting_at = form_param.starting_at
    if starting_at.tzinfo is None:
        starting_at_local = starting_at.replace(tzinfo=TMZ_SECONDARY)
    else:
        starting_at_local = starting_at.astimezone(TMZ_SECONDARY)
    local_date = starting_at_local.date()
    current_date = datetime.now(TMZ_SECONDARY).date()
    if local_date not in {current_date, current_date + timedelta(days=1)}:
        raise exceptions.InvalidValue(Service.starting_at)

    # Fetch all landmarks for the route ordered by distance from start.
    # Use the first/last entries to determine display names and ending_at.
    landmarks_in_route = (
        session.query(LandmarkInRoute)
        .filter(LandmarkInRoute.route_id == route.id)
        .order_by(LandmarkInRoute.distance_from_start.asc())
        .all()
    )
    first_landmark_in_route = landmarks_in_route[0]
    last_landmark_in_route = landmarks_in_route[-1]
    ending_at = starting_at + timedelta(minutes=last_landmark_in_route.arrival_delta)

    # Prevent assigning the same vehicle to overlapping services (any company)
    overlapping_service = (
        session.query(Service)
        .filter(
            Service.registration_number == vehicle.registration_number,
            Service.starting_at < ending_at,
            Service.ending_at > starting_at,
        )
        .first()
    )
    if overlapping_service:
        raise exceptions.OverlappingService()

    # Use provided name if present, otherwise create service name for display
    if form_param.name is not None:
        name = form_param.name
    else:
        first_landmark = (
            session.query(Landmark)
            .filter(Landmark.id == first_landmark_in_route.landmark_id)
            .first()
        )
        last_landmark = (
            session.query(Landmark)
            .filter(Landmark.id == last_landmark_in_route.landmark_id)
            .first()
        )
        starting_at_str = starting_at_local.strftime("%Y-%m-%d %-I:%M %p")
        name = f"{starting_at_str} {first_landmark.name} -> {last_landmark.name} ({vehicle.registration_number})"

    # Ensure fare snapshot exists in fare_in_service (or increment reference_count)
    fare_snapshot = (
        session.query(FareInService)
        .filter(FareInService.fare_id == fare.id, FareInService.version == fare.version)
        .first()
    )
    if fare_snapshot:
        fare_snapshot.reference_count += 1
    else:
        fare_snapshot = FareInService(
            fare_id=fare.id,
            version=fare.version,
            name=fare.name,
            attributes=fare.attributes,
            function=fare.function,
            reference_count=1,
        )
        session.add(fare_snapshot)
    session.flush()

    # Generate keys
    ticket_creator = TicketCreator()
    private_key = ticket_creator.pem_private_key_string
    public_key = ticket_creator.pem_public_key_string

    # Ensure vehicle snapshot exists in vehicle_in_service (or increment reference_count)
    vehicle_snapshot = (
        session.query(VehicleInService)
        .filter(
            VehicleInService.vehicle_id == vehicle.id,
            VehicleInService.version == vehicle.version,
        )
        .first()
    )
    if vehicle_snapshot:
        vehicle_snapshot.reference_count += 1
    else:
        vehicle_snapshot = VehicleInService(
            vehicle_id=vehicle.id,
            version=vehicle.version,
            registration_number=vehicle.registration_number,
            name=vehicle.name,
            capacity=vehicle.capacity,
            reference_count=1,
        )
        session.add(vehicle_snapshot)
    session.flush()
    service = Service(
        company_id=company.id,
        name=name,
        fare_in_service_id=fare_snapshot.id,
        vehicle_in_service_id=vehicle_snapshot.id,
        registration_number=vehicle.registration_number,
        ticket_mode=form_param.ticket_mode,
        status=ServiceStatus.CREATED,
        starting_at=starting_at,
        ending_at=ending_at,
        private_key=private_key,
        public_key=public_key,
    )
    session.add(service)
    session.flush()

    # Create LandmarkInService entries for this service (snapshot timings)
    landmarks_in_service = []
    for lm in landmarks_in_route:
        arrival_at = (starting_at + timedelta(minutes=lm.arrival_delta)).timetz()
        departure_at = (starting_at + timedelta(minutes=lm.departure_delta)).timetz()
        landmark_snapshot = LandmarkInService(
            service_id=service.id,
            landmark_id=lm.landmark_id,
            arrival_at=arrival_at,
            departure_at=departure_at,
        )
        landmarks_in_service.append(landmark_snapshot)
    first_landmark = landmarks_in_service[0]
    last_landmark = landmarks_in_service[-1]
    service.starting_landmark_id = first_landmark.landmark_id
    service.ending_landmark_id = last_landmark.landmark_id
    session.add_all(landmarks_in_service)

    session.commit()
    session.refresh(service)
    service_data = jsonable_encoder(service, exclude={"private_key"})
    return service_data


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_SERVICE,
    tags=["Service"],
    response_model=ServiceSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidAssociation(
                VehicleInService.vehicle_id, Service.company_id
            ),
            exceptions.InvalidAssociation(LandmarkInRoute.route_id, Service.company_id),
            exceptions.InvalidAssociation(FareInService.fare_id, Service.company_id),
            exceptions.UnknownValue(Service.company_id),
            exceptions.UnknownValue(Vehicle.id),
            exceptions.UnknownValue(Route.id),
            exceptions.UnknownValue(Fare.id),
            exceptions.InactiveResource(Vehicle),
            exceptions.InactiveResource(Company),
            exceptions.InactiveResource(Route),
            exceptions.OverlappingService(),
            exceptions.InvalidValue(Service.starting_at),
        ]
    ),
    description=(
        """
            **Creates a new service for a company.**    
            - Requires a valid access token.    
            - Logged in executive must have `company.service.create` permission.    
            - Validates that the vehicle, route, and fare belong to the specified company.    
            -  Status of vehicle must be ACTIVE, company must be VERIFIED, and route must be VALID.    
            - Starting date must be either current date or next date in TMZ_PRIMARY timezone.    
            - The service name is auto-generated based on the name of the route, vehicle, and starting date.    
            - By default the status of the service is set to CREATED.   
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

        company = validate_id(
            session, Company, form_param.company_id, Service.company_id
        )
        vehicle = validate_id(session, Vehicle, form_param.vehicle_id, "vehicle_id")
        route = validate_id(session, Route, form_param.route_id, "route_id")
        fare = validate_id(session, Fare, form_param.fare_id, "fare_id")

        if vehicle.company_id != company.id:
            raise exceptions.InvalidAssociation(
                VehicleInService.vehicle_id, Service.company_id
            )
        if route.company_id != company.id:
            raise exceptions.InvalidAssociation(
                LandmarkInRoute.route_id, Service.company_id
            )
        if fare.scope != FareScope.GLOBAL:
            if fare.company_id != company.id:
                raise exceptions.InvalidAssociation(
                    FareInService.fare_id, Service.company_id
                )

        service_data = create_service(
            session,
            route,
            vehicle,
            fare,
            company,
            CreateForm(**form_param.model_dump()),
        )

        log_event(token, request_info, service_data)
        return service_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_SERVICE,
    tags=["Service"],
    response_model=ServiceSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Vehicle.id),
            exceptions.UnknownValue(Route.id),
            exceptions.UnknownValue(Fare.id),
            exceptions.InvalidAssociation(FareInService.fare_id, Service.company_id),
            exceptions.InactiveResource(Vehicle),
            exceptions.OverlappingService(),
            exceptions.InvalidValue(Service.starting_at),
        ]
    ),
    description=(
        """
            **Creates a new service for a company.**    
            - Requires a valid access token.    
            - Logged in operator must have `company.service.create` permission.    
            - Operator can only create services for the company they belong to.    
            - Validates that the vehicle, route, and fare belong to the specified company.    
            -  Status of vehicle must be ACTIVE, company must be VERIFIED, and route must be VALID.    
            - Starting date must be either current date or next date in TMZ_PRIMARY timezone.    
            - The service name is auto-generated based on the name of the route, vehicle, and starting date.    
            - By default the status of the service is set to CREATED.   
        """
    ),
)
async def create_service_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.CREATE_COMPANY_SERVICE)

        company = validate_id(session, Company, token.company_id, Service.company_id)
        vehicle = validate_id(
            session,
            Vehicle,
            form_param.vehicle_id,
            "vehicle_id",
            extra_filter=(Vehicle.company_id == token.company_id),
        )
        route = validate_id(
            session,
            Route,
            form_param.route_id,
            "route_id",
            extra_filter=(Route.company_id == token.company_id),
        )
        fare = validate_id(session, Fare, form_param.fare_id, "fare_id")

        if fare.scope != FareScope.GLOBAL:
            if fare.company_id != token.company_id:
                raise exceptions.InvalidAssociation(
                    FareInService.fare_id, Service.company_id
                )

        service_data = create_service(
            session,
            route,
            vehicle,
            fare,
            company,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
        )

        log_event(token, request_info, service_data)
        return service_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
