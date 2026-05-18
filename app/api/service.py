"""
Service API Router for EnteBus.

Provides endpoints for managing services, including creation,
update, deletion and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime, timezone
from enum import StrEnum
from fastapi.encoders import jsonable_encoder
from typing import Any, Dict, List
from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field
from datetime import timedelta
from fastapi import status, Depends
from sqlalchemy import String, and_, func, or_
from sqlalchemy.orm.session import Session
from sqlalchemy.orm import aliased

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.api.fare import FareAttributes
from app.src.urls import URL_SERVICE
from app.src.db import (
    SessionLocal,
    ExecutiveToken,
    OperatorToken,
    Service,
    Duty,
    Route,
    LandmarkInRoute,
    Landmark,
    Fare,
    Vehicle,
    FareInService,
    VehicleInService,
    LandmarkInService,
    Company,
    PaperTicket,
    VendorToken,
)
from app.src import exceptions
from app.src.functions import (
    apply_id_filters,
    resolve_model_defaults,
    get_request_info,
    get_executive_roles,
    get_operator_roles,
    fuse_exception_responses,
    enum_str,
    apply_name_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_status_filters,
    update_if_changed,
    normalize_timestamp,
)
from app.src.validators import (
    validate_id,
    validate_state_transition,
    verify_token,
    verify_permission,
)
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.enums import (
    DutyStatus,
    OrderIn,
    VehicleStatus,
    CompanyStatus,
    RouteStatus,
    TicketingMode,
    ServiceStatus,
    FareScope,
)
from app.src.filters import (
    IDFilter,
    CreatedOnFilter,
    UpdatedOnFilter,
    PaginationFilter,
)
from app.src.regex import NAME_PATTERN
from app.src.constants import TMZ_SECONDARY
from app.src.digital_ticket.v1 import TicketCreator
from app.src.constants import SERVICE_CREATION_LEAD_TIME_DAYS, TMZ_PRIMARY

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


## Output Schema
class ServiceSchema(BaseModel):
    """Schema for service response without revealing all details."""

    id: int
    company_id: int
    name: str
    status: int
    registration_number: str
    fare_id: int | None
    vehicle_id: int | None
    route_id: int | None
    starting_landmark_id: int
    ending_landmark_id: int
    ticket_mode: int
    remark: str | None
    starting_at: datetime
    ending_at: datetime
    updated_on: datetime | None = None
    created_on: datetime


class FareInServiceSchema(BaseModel):
    """Schema for fare in service response."""

    id: int
    fare_id: int
    version: int
    name: str
    attributes: FareAttributes
    function: str


class VehicleInServiceSchema(BaseModel):
    """Schema for vehicle in service response."""

    id: int
    vehicle_id: int
    version: int
    registration_number: str
    name: str
    capacity: int


class LandmarkInServiceSchema(BaseModel):
    """Schema for landmark in service response."""

    service_id: int
    landmark_id: int
    distance_from_start: int
    arrival_at: datetime
    departure_at: datetime


class PublicServiceSchema(ServiceSchema):
    """Schema for service response with masked details."""

    fare: FareInServiceSchema
    vehicle: VehicleInServiceSchema
    route: List[LandmarkInServiceSchema]


class PrivateServiceSchema(PublicServiceSchema):
    """Schema for service response with detailed information."""

    public_key: str


# Input Forms
class CreateFormForOP(BaseModel):
    """Form data for creating a new service by an operator."""

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
    """Form data for creating a new service by an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new service."""

    pass


class UpdateForm(BaseModel):
    """Form data for updating an existing service."""

    name: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=NAME_PATTERN
    )
    ticket_mode: TicketingMode = Field(
        default=None, description=enum_str(TicketingMode)
    )
    status: ServiceStatus = Field(default=None, description=enum_str(ServiceStatus))
    remark: str | None = Field(default=None, min_length=1, max_length=1024)
    vehicle_id: int = Field(default=None)
    route_id: int = Field(default=None)
    fare_id: int = Field(default=None)
    starting_at: datetime = Field(default=None)


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering service results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    STARTING_AT = "starting_at"
    ENDING_AT = "ending_at"


class QueryParamsForPU(IDFilter, CreatedOnFilter, UpdatedOnFilter, PaginationFilter):
    """Query parameters for public users."""

    search: str | None = Field(Query(default=None))
    name: str | None = Field(Query(default=None))
    registration_number: str | None = Field(Query(default=None))
    ticket_mode: TicketingMode | None = Field(
        Query(default=None, description=enum_str(TicketingMode))
    )
    status_list: List[ServiceStatus] | None = Field(
        Query(default=None, description=enum_str(ServiceStatus))
    )
    starting_at_ge: datetime | None = Field(Query(default=None))
    starting_at_le: datetime | None = Field(Query(default=None))
    ending_at_ge: datetime | None = Field(Query(default=None))
    ending_at_le: datetime | None = Field(Query(default=None))
    starting_landmark_id: int | None = Field(Query(default=None))
    ending_landmark_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForOP(QueryParamsForPU):
    """Query parameters for operator users."""

    id_excluding: List[int] | None = Field(Query(default=None))
    fare_id: int | None = Field(Query(default=None))
    vehicle_id: int | None = Field(Query(default=None))
    route_id: int | None = Field(Query(default=None))


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executive users."""

    company_id: int | None = Field(Query(default=None))


class QueryParamsForVE(QueryParamsForEX):
    """Query parameters for vendor users."""

    pass


class QueryParams(QueryParamsForEX):
    """Generic combined query parameters for services."""

    pass


class ServiceQueryParams(BaseModel):
    """Query parameters for retrieving a service."""

    marked_as_cached: bool = Field(Query(default=False))


# Functions
def validate_starting_at(starting_at: datetime) -> datetime:
    """
    Normalize a datetime to UTC and validate it is within the allowed creation window.

    Returns the normalized `starting_at` in UTC.
    """
    starting_at = normalize_timestamp(starting_at)
    utc_now = datetime.now(TMZ_PRIMARY)
    if (
        starting_at > (utc_now + timedelta(days=SERVICE_CREATION_LEAD_TIME_DAYS))
        or starting_at < utc_now
    ):
        raise exceptions.InvalidValue(Service.starting_at)
    return starting_at


def validate_service_timing(
    session: Session,
    starting_at: datetime,
    ending_at: datetime,
    registration_number: str,
    exclude_service_id: int | None = None,
) -> None:
    """
    Validates that there are no overlapping services for the same vehicle registration number.

    Args:
        session (Session): SQLAlchemy database session.
        starting_at (datetime): Proposed starting time of the service.
        ending_at (datetime): Proposed ending time of the service.
        registration_number (str): Vehicle registration number to check for overlaps.
        exclude_service_id (int | None): Optional service ID to exclude from the check (useful when updating a service).

    Raises:
        exceptions.OverlappingService: If there is an overlapping service with the same vehicle registration number.
    """
    query = session.query(Service).filter(
        Service.registration_number == registration_number,
        Service.starting_at < ending_at,
        Service.ending_at > starting_at,
    )
    if exclude_service_id is not None:
        query = query.filter(Service.id != exclude_service_id)
    if query.first():
        raise exceptions.OverlappingService()


def create_landmarks_in_service(
    service_id: int,
    landmarks_in_route: List[LandmarkInRoute],
    starting_at: datetime,
) -> List[LandmarkInService]:
    """
    Create landmark snapshot rows for a service based on route timing deltas.

    Args:
        service_id (int): The ID of the service.
        landmarks_in_route (List[LandmarkInRoute]): The landmarks in the route.
        starting_at (datetime): The starting time of the service.

    Returns:
        List[LandmarkInService]: The list of landmarks in the service.
    """
    landmarks_in_service = []
    for landmark_in_route in landmarks_in_route:
        landmarks_in_service.append(
            LandmarkInService(
                service_id=service_id,
                landmark_id=landmark_in_route.landmark_id,
                distance_from_start=landmark_in_route.distance_from_start,
                arrival_at=starting_at
                + timedelta(minutes=landmark_in_route.arrival_delta),
                departure_at=starting_at
                + timedelta(minutes=landmark_in_route.departure_delta),
            )
        )
    return landmarks_in_service


def create_fare_in_service(session: Session, fare: Fare) -> FareInService:
    """
    If a FareInService snapshot exists for the given fare/version, increment its reference_count and return it.
    Otherwise, create a new snapshot with reference_count=1 and return that.

    Args:
        session (Session): SQLAlchemy database session.
        fare (Fare): The Fare object for which to create or update the FareInService snapshot.

    Returns:
        FareInService: The existing or newly created FareInService snapshot associated with the given fare.
    """
    fare_in_service = (
        session.query(FareInService)
        .filter(FareInService.fare_id == fare.id, FareInService.version == fare.version)
        .first()
    )
    if fare_in_service:
        fare_in_service.reference_count += 1
    else:
        fare_in_service = FareInService(
            fare_id=fare.id,
            version=fare.version,
            name=fare.name,
            attributes=fare.attributes,
            function=fare.function,
            reference_count=1,
        )
        session.add(fare_in_service)
    session.flush()
    return fare_in_service


def create_vehicle_in_service(session: Session, vehicle: Vehicle) -> VehicleInService:
    """
    If a VehicleInService snapshot exists for the given vehicle/version, increment its reference_count and return it.
    Otherwise, create a new snapshot with reference_count=1 and return that.

    Args:
        session (Session): SQLAlchemy database session.
        vehicle (Vehicle): The Vehicle object for which to create or update the VehicleInService snapshot.

    Returns:
        VehicleInService: The existing or newly created VehicleInService snapshot associated with the given vehicle.
    """
    vehicle_in_service = (
        session.query(VehicleInService)
        .filter(
            VehicleInService.vehicle_id == vehicle.id,
            VehicleInService.version == vehicle.version,
        )
        .first()
    )
    if vehicle_in_service:
        vehicle_in_service.reference_count += 1
    else:
        vehicle_in_service = VehicleInService(
            vehicle_id=vehicle.id,
            version=vehicle.version,
            registration_number=vehicle.registration_number,
            name=vehicle.name,
            capacity=vehicle.capacity,
            reference_count=1,
        )
        session.add(vehicle_in_service)
    session.flush()
    return vehicle_in_service


def fetch_landmarks_in_service(
    session: Session, service: Service
) -> List[LandmarkInServiceSchema]:
    """
    Fetch and return landmark snapshots (`LandmarkInService`) for a service.

    Args:
        session (Session): SQLAlchemy session.
        service (Service): Service object to lookup.

    Returns:
        List[LandmarkInServiceSchema]: List of `LandmarkInServiceSchema` objects.
    """
    landmarks = (
        session.query(LandmarkInService)
        .filter(LandmarkInService.service_id == service.id)
        .order_by(LandmarkInService.distance_from_start.asc())
        .all()
    )
    return jsonable_encoder(landmarks)


def fetch_fare_in_service(session: Session, service: Service) -> FareInServiceSchema:
    """
    Fetch and return the `FareInService` snapshot for a service.

    Args :
        session (Session): SQLAlchemy session.
        service (Service): Service object to lookup.

    Returns:
        FareInServiceSchema: `FareInServiceSchema` object.
    """
    fare_in_service = (
        session.query(FareInService)
        .filter(FareInService.id == service.fare_in_service_id)
        .first()
    )
    return jsonable_encoder(fare_in_service, exclude={"reference_count"})


def fetch_vehicle_in_service(
    session: Session, service: Service
) -> VehicleInServiceSchema:
    """
    Fetch and return the `VehicleInService` snapshot for a service.

    Args :
        session (Session): SQLAlchemy session.
        service (Service): Service object to lookup.

    Returns:
        VehicleInServiceSchema: `VehicleInServiceSchema` object.

    """
    vehicle_in_service = (
        session.query(VehicleInService)
        .filter(VehicleInService.id == service.vehicle_in_service_id)
        .first()
    )
    return jsonable_encoder(vehicle_in_service, exclude={"reference_count"})


def delete_landmarks_in_service(session: Session, service: Service) -> None:
    """
    Delete all `LandmarkInService` rows associated with a `Service`.

    Args:
        session (Session): SQLAlchemy session.
        service (Service): Service whose landmark snapshots should be removed.

    Returns:
        None
    """
    session.query(LandmarkInService).filter(
        LandmarkInService.service_id == service.id
    ).delete(synchronize_session=False)
    session.flush()


def delete_fare_in_service(session: Session, fare_in_service_id: int) -> None:
    """
    Decrements the reference count of the `FareInService` snapshot referenced by the
    given `Service` and deletes it if the count reaches zero.

    Args:
        session (Session): SQLAlchemy database session.
        fare_in_service_id (int): ID of the `FareInService` snapshot to be decremented/cleaned up.

    Returns:
        None
    """
    fare_in_service = (
        session.query(FareInService)
        .filter(FareInService.id == fare_in_service_id)
        .first()
    )
    fare_in_service.reference_count -= 1
    if fare_in_service.reference_count == 0:
        session.delete(fare_in_service)
    session.flush()


def delete_vehicle_in_service(session: Session, vehicle_in_service_id: int) -> None:
    """
    Decrements the reference count of the `VehicleInService` snapshot referenced by the
    given `Service` and deletes it if the count reaches zero.

    Args:
        session (Session): SQLAlchemy database session.
        vehicle_in_service_id (int): ID of the `VehicleInService` snapshot to be decremented/cleaned up.

    Returns:
        None
    """
    vehicle_in_service = (
        session.query(VehicleInService)
        .filter(VehicleInService.id == vehicle_in_service_id)
        .first()
    )
    vehicle_in_service.reference_count -= 1
    if vehicle_in_service.reference_count == 0:
        session.delete(vehicle_in_service)
    session.flush()


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
    # validations
    if vehicle.status != VehicleStatus.ACTIVE:
        raise exceptions.InactiveResource(Vehicle)
    if company.status != CompanyStatus.VERIFIED:
        raise exceptions.InactiveResource(Company)
    if route.status != RouteStatus.VALID:
        raise exceptions.InactiveResource(Route)

    # Normalize and validate starting_at
    starting_at = validate_starting_at(form_param.starting_at)

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
    validate_service_timing(
        session, starting_at, ending_at, vehicle.registration_number
    )

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
        starting_at_str = starting_at.astimezone(TMZ_SECONDARY).strftime(
            "%Y-%m-%d %-I:%M %p"
        )
        name = f"{starting_at_str} {first_landmark.name} -> {last_landmark.name} ({vehicle.registration_number})"

    fare_in_service = create_fare_in_service(session, fare)

    vehicle_in_service = create_vehicle_in_service(session, vehicle)

    # Generate keys
    ticket_creator = TicketCreator()
    private_key = ticket_creator.pem_private_key_string
    public_key = ticket_creator.pem_public_key_string

    service = Service(
        company_id=company.id,
        name=name,
        fare_in_service_id=fare_in_service.id,
        fare_id=fare.id,
        vehicle_in_service_id=vehicle_in_service.id,
        vehicle_id=vehicle.id,
        registration_number=vehicle.registration_number,
        route_id=route.id,
        ticket_mode=form_param.ticket_mode,
        status=ServiceStatus.CREATED,
        starting_at=starting_at,
        ending_at=ending_at,
        private_key=private_key,
        public_key=public_key,
        starting_landmark_id=first_landmark_in_route.landmark_id,
        ending_landmark_id=last_landmark_in_route.landmark_id,
    )
    session.add(service)
    session.flush()

    landmarks_in_service = create_landmarks_in_service(
        service.id, landmarks_in_route, starting_at
    )
    session.add_all(landmarks_in_service)

    session.commit()
    session.refresh(service)
    service_data = jsonable_encoder(service, exclude={"private_key"})
    return service_data


def update_service(
    session: Session,
    service: Service,
    form_param: UpdateForm,
    vehicle: Vehicle | None,
    route: Route | None,
    fare: Fare | None,
) -> tuple[bool, dict]:
    """
    Updates an existing service record.

    Supports service status transitions CREATED -> CACHED, CACHED -> ENDED,
    STARTED -> ENDED, and ENDED -> STARTED. When a service is ended, all STARTED duties on that
    service are ended at the same UTC timestamp and their collection totals are
    finalized from related paper tickets. Reactivating a service does not
    reactivate duties.

    Args:
        session (Session): SQLAlchemy database session.
        service (Service): Existing service to update.
        form_param (UpdateForm): Form data for updating the service.
        vehicle (Vehicle | None): New vehicle if vehicle_id was provided.
        route (Route | None): New route if route_id was provided.
        fare (Fare | None): New fare if fare_id was provided.

    Returns:
        tuple[bool, dict]: (have_updates, service_data)
    """
    _allowed_service_status_transitions = {
        ServiceStatus.CREATED: [ServiceStatus.CACHED],
        ServiceStatus.CACHED: [ServiceStatus.ENDED],
        ServiceStatus.STARTED: [ServiceStatus.ENDED],
        ServiceStatus.ENDED: [ServiceStatus.STARTED],
    }

    update_data = form_param.model_dump(exclude_unset=True)
    duties = []

    if "status" in update_data:
        new_status = update_data.pop("status")
        if new_status != service.status:
            validate_state_transition(
                _allowed_service_status_transitions,
                service.status,
                new_status,
                Service.status,
            )

            if new_status == ServiceStatus.ENDED:
                utc_now = datetime.now(timezone.utc)
                duties = (
                    session.query(Duty)
                    .filter(
                        Duty.service_id == service.id,
                        Duty.status == DutyStatus.STARTED,
                    )
                    .all()
                )
                duty_ids = [duty.id for duty in duties]
                collections_by_duty_id = {}
                if duty_ids:
                    collections_by_duty_id = dict(
                        session.query(
                            PaperTicket.duty_id,
                            func.sum(PaperTicket.amount),
                        )
                        .filter(PaperTicket.duty_id.in_(duty_ids))
                        .group_by(PaperTicket.duty_id)
                        .all()
                    )
                for duty in duties:
                    duty.collection = collections_by_duty_id.get(duty.id)
                    duty.finished_on = utc_now
                    duty.status = DutyStatus.ENDED

            service.status = new_status

    vehicle_id = update_data.pop("vehicle_id", None)
    route_id = update_data.pop("route_id", None)
    fare_id = update_data.pop("fare_id", None)
    starting_at = update_data.pop("starting_at", None)
    need_critical_change = (
        vehicle_id is not None
        or route_id is not None
        or fare_id is not None
        or starting_at is not None
    )
    have_critical_change = False

    if need_critical_change and service.status != ServiceStatus.CREATED:
        raise exceptions.DataInUse(Service)

    if starting_at is not None:
        starting_at = validate_starting_at(starting_at)
        if starting_at != service.starting_at:
            old_starting_at = service.starting_at
            service.starting_at = starting_at
            time_change = service.starting_at - old_starting_at
            session.query(LandmarkInService).filter(
                LandmarkInService.service_id == service.id
            ).update(
                {
                    LandmarkInService.arrival_at: LandmarkInService.arrival_at
                    + time_change,
                    LandmarkInService.departure_at: LandmarkInService.departure_at
                    + time_change,
                },
                synchronize_session=False,
            )
            service.ending_at = service.ending_at + time_change
            session.flush()
            have_critical_change = True

    if route_id is not None:
        if route.status != RouteStatus.VALID:
            raise exceptions.InactiveResource(Route)
        landmarks_in_route = (
            session.query(LandmarkInRoute)
            .filter(LandmarkInRoute.route_id == route.id)
            .order_by(LandmarkInRoute.distance_from_start.asc())
            .all()
        )
        first_landmark_in_route = landmarks_in_route[0]
        last_landmark_in_route = landmarks_in_route[-1]
        ending_at = service.starting_at + timedelta(
            minutes=last_landmark_in_route.arrival_delta
        )

        delete_landmarks_in_service(session, service)
        landmarks_in_service = create_landmarks_in_service(
            service.id, landmarks_in_route, service.starting_at
        )
        session.add_all(landmarks_in_service)
        session.flush()
        service.ending_at = ending_at
        service.starting_landmark_id = first_landmark_in_route.landmark_id
        service.ending_landmark_id = last_landmark_in_route.landmark_id
        service.route_id = route.id
        have_critical_change = True

    if fare_id is not None:
        old_fare_in_service = (
            session.query(FareInService)
            .filter(FareInService.id == service.fare_in_service_id)
            .first()
        )
        if (
            old_fare_in_service is None
            or old_fare_in_service.fare_id != fare.id
            or old_fare_in_service.version != fare.version
        ):
            old_fare_in_service_id = service.fare_in_service_id
            fare_in_service = create_fare_in_service(session, fare)
            service.fare_in_service_id = fare_in_service.id
            service.fare_id = fare.id
            session.flush()
            delete_fare_in_service(session, old_fare_in_service_id)
            session.flush()
            have_critical_change = True

    if vehicle_id is not None:
        if vehicle.status != VehicleStatus.ACTIVE:
            raise exceptions.InactiveResource(Vehicle)
        old_vehicle_in_service = (
            session.query(VehicleInService)
            .filter(VehicleInService.id == service.vehicle_in_service_id)
            .first()
        )
        if (
            old_vehicle_in_service is None
            or old_vehicle_in_service.vehicle_id != vehicle.id
            or old_vehicle_in_service.version != vehicle.version
        ):
            old_vehicle_in_service_id = service.vehicle_in_service_id
            vehicle_in_service = create_vehicle_in_service(session, vehicle)
            service.vehicle_in_service_id = vehicle_in_service.id
            service.registration_number = vehicle.registration_number
            service.vehicle_id = vehicle.id
            session.flush()
            delete_vehicle_in_service(session, old_vehicle_in_service_id)
            have_critical_change = True

    if vehicle_id is not None or route_id is not None or starting_at is not None:
        session.flush()
        validate_service_timing(
            session,
            service.starting_at,
            service.ending_at,
            service.registration_number,
            exclude_service_id=service.id,
        )

    update_if_changed(service, update_data)
    have_updates = (
        have_critical_change
        or session.is_modified(service)
        or any(session.is_modified(duty) for duty in duties)
    )
    if have_updates:
        session.commit()
        session.refresh(service)

    service_data = jsonable_encoder(service, exclude={"private_key"})
    return have_updates, service_data


def search_service(session: Session, query_params: QueryParams) -> List[Service]:
    """
    Search for Services based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve services that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Service]: List of Services that match the search criteria.
    """
    svcs_to_consider = None

    if (
        query_params.starting_landmark_id is not None
        and query_params.ending_landmark_id is not None
    ):
        # Find services with both landmarks where starting arrives before ending
        # Use aliases to differentiate between starting and ending landmarks
        starting_lmk = aliased(LandmarkInService)
        ending_lmk = aliased(LandmarkInService)

        svcs_to_consider = (
            session.query(starting_lmk.service_id)
            .join(
                ending_lmk,
                and_(
                    starting_lmk.service_id == ending_lmk.service_id,
                    starting_lmk.arrival_at < ending_lmk.arrival_at,
                ),
            )
            .filter(starting_lmk.landmark_id == query_params.starting_landmark_id)
            .filter(ending_lmk.landmark_id == query_params.ending_landmark_id)
            .distinct()
        )

    elif query_params.starting_landmark_id is not None:
        # Find services with the starting landmark only
        svcs_to_consider = (
            session.query(LandmarkInService.service_id)
            .filter(LandmarkInService.landmark_id == query_params.starting_landmark_id)
            .distinct()
        )

    elif query_params.ending_landmark_id is not None:
        # Find services with the ending landmark only
        svcs_to_consider = (
            session.query(LandmarkInService.service_id)
            .filter(LandmarkInService.landmark_id == query_params.ending_landmark_id)
            .distinct()
        )

    query = session.query(Service)
    if svcs_to_consider is not None:
        query = query.filter(Service.id.in_(svcs_to_consider))
    if query_params.company_id is not None:
        query = query.filter(Service.company_id == query_params.company_id)
    if query_params.fare_id is not None:
        query = query.filter(Service.fare_id == query_params.fare_id)
    if query_params.vehicle_id is not None:
        query = query.filter(Service.vehicle_id == query_params.vehicle_id)
    if query_params.route_id is not None:
        query = query.filter(Service.route_id == query_params.route_id)
    if query_params.id_excluding:
        query = query.filter(Service.id.notin_(query_params.id_excluding))
    if query_params.registration_number is not None:
        query = query.filter(
            Service.registration_number.ilike(f"%{query_params.registration_number}%")
        )
    if query_params.ticket_mode is not None:
        query = query.filter(Service.ticket_mode == query_params.ticket_mode)
    if query_params.starting_at_ge is not None:
        query = query.filter(Service.starting_at >= query_params.starting_at_ge)
    if query_params.starting_at_le is not None:
        query = query.filter(Service.starting_at <= query_params.starting_at_le)
    if query_params.ending_at_ge is not None:
        query = query.filter(Service.ending_at >= query_params.ending_at_ge)
    if query_params.ending_at_le is not None:
        query = query.filter(Service.ending_at <= query_params.ending_at_le)
    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Service.id.cast(String).ilike(search),
                Service.name.ilike(search),
                Service.registration_number.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Service, query_params)
    query = apply_name_filters(query, Service, query_params)
    query = apply_created_on_filters(query, Service, query_params)
    query = apply_updated_on_filters(query, Service, query_params)
    query = apply_status_filters(query, Service, query_params)

    # Ordering and pagination
    ordering_attr = getattr(Service, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    services = query.all()
    return services


def fetch_service_details(session: Session, service: Service) -> PrivateServiceSchema:
    """
    Returns details of a service along with related entities like landmarks, fare, and vehicle in service.

    Args:
        session (Session): SQLAlchemy session.
        service (Service): Service object to lookup.

    Returns:
        Dict[str, Any]: Dict containing `service`, `landmarks_in_service`,
        `fare_in_service`, and `vehicle_in_service` serialized for JSON.
    """

    landmarks_in_service_data = fetch_landmarks_in_service(session, service)
    fare_in_service_data = fetch_fare_in_service(session, service)
    vehicle_in_service_data = fetch_vehicle_in_service(session, service)

    service_data = jsonable_encoder(service, exclude={"private_key"})

    return {
        **service_data,
        "route": landmarks_in_service_data,
        "fare": fare_in_service_data,
        "vehicle": vehicle_in_service_data,
    }


def delete_service(session: Session, service: Service) -> dict:
    """
    Deletes a service from the database and decrements/cleans up related snapshot reference counts.

    This function ensures that when a service is deleted:
    1. The FareInService snapshot reference_count is decremented
    2. If FareInService reference_count reaches 0, the snapshot is deleted
    3. The VehicleInService snapshot reference_count is decremented
    4. If VehicleInService reference_count reaches 0, the snapshot is deleted
    5. The service and related LandmarkInService entries are deleted

    Args:
        session (Session): SQLAlchemy database session.
        service (Service): Service object to delete.

    Returns:
        dict: JSON-encoded representation of the deleted service.
    """
    service_data = jsonable_encoder(service, exclude={"private_key", "public_key"})

    # remove landmark snapshots first
    delete_landmarks_in_service(session, service)

    # capture snapshot ids before removing the service row
    old_fare_in_service_id = service.fare_in_service_id
    old_vehicle_in_service_id = service.vehicle_in_service_id

    # delete the service row so snapshots are no longer referenced
    session.delete(service)
    session.flush()

    # decrement/delete snapshots referenced by the (now-deleted) service
    delete_fare_in_service(session, old_fare_in_service_id)
    delete_vehicle_in_service(session, old_vehicle_in_service_id)

    session.commit()
    return service_data


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_SERVICE,
    summary="Create service",
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
            exceptions.UnknownValue("vehicle_id"),
            exceptions.UnknownValue("route_id"),
            exceptions.UnknownValue("fare_id"),
            exceptions.InactiveResource(Vehicle),
            exceptions.InactiveResource(Company),
            exceptions.InactiveResource(Route),
            exceptions.OverlappingService(),
            exceptions.InvalidValue(Service.starting_at),
        ]
    ),
    description=(
        f"""
            **Creates a new service for a company.**    
            - Requires a valid access token.    
            - Logged in executive must have `company.service.create` permission.    
            - Validates that the vehicle, route, and fare belong to the specified company.    
            - Status of vehicle must be ACTIVE, company must be VERIFIED, and route must be VALID.    
            - `starting_at` must be between now and `{SERVICE_CREATION_LEAD_TIME_DAYS}` days from now.    
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


@route_executive.patch(
    f"{URL_SERVICE}/{{id}}",
    summary="Update service",
    tags=["Service"],
    response_model=ServiceSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Service.id),
            exceptions.UnknownValue("vehicle_id"),
            exceptions.UnknownValue("route_id"),
            exceptions.UnknownValue("fare_id"),
            exceptions.InvalidStateTransition(Service.status),
            exceptions.InvalidAssociation(
                VehicleInService.vehicle_id, Service.company_id
            ),
            exceptions.InvalidAssociation(LandmarkInRoute.route_id, Service.company_id),
            exceptions.InvalidAssociation(FareInService.fare_id, Service.company_id),
            exceptions.InactiveResource(Vehicle),
            exceptions.InactiveResource(Route),
            exceptions.OverlappingService(),
            exceptions.DataInUse(Service),
            exceptions.InvalidValue(Service.starting_at),
        ]
    ),
    description=(
        f"""
            **Updates an existing service.**    
            - Requires a valid access token.    
            - Logged in executive must have `company.service.update` permission.    
            - Allowed status transitions:    
                - CREATED -> CACHED    
                - CACHED -> ENDED    
                - STARTED -> ENDED    
                - ENDED -> STARTED    
            - When status transitions to ENDED, all STARTED duties on the service are ended at the same time.    
            - `vehicle_id`, `route_id`, `fare_id`, and `starting_at` can only be updated when service status is CREATED.    
            - `starting_at` must be between now and `{SERVICE_CREATION_LEAD_TIME_DAYS}` days from now.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_service_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_SERVICE)

        service = validate_id(session, Service, id, Service.id)

        update_data = form_param.model_dump(exclude_unset=True)
        vehicle = None
        route = None
        fare = None
        if "vehicle_id" in update_data:
            vehicle = validate_id(session, Vehicle, form_param.vehicle_id, "vehicle_id")
            if vehicle.company_id != service.company_id:
                raise exceptions.InvalidAssociation(
                    VehicleInService.vehicle_id, Service.company_id
                )
        if "route_id" in update_data:
            route = validate_id(session, Route, form_param.route_id, "route_id")
            if route.company_id != service.company_id:
                raise exceptions.InvalidAssociation(
                    LandmarkInRoute.route_id, Service.company_id
                )
        if "fare_id" in update_data:
            fare = validate_id(session, Fare, form_param.fare_id, "fare_id")
            if fare.scope != FareScope.GLOBAL and fare.company_id != service.company_id:
                raise exceptions.InvalidAssociation(
                    FareInService.fare_id, Service.company_id
                )

        have_updates, service_data = update_service(
            session,
            service,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            vehicle=vehicle,
            route=route,
            fare=fare,
        )

        if have_updates:
            log_event(token, request_info, service_data)
        return service_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_SERVICE,
    summary="Fetch service",
    tags=["Service"],
    response_model=List[ServiceSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of services.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_service_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_service(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    f"{URL_SERVICE}/{{id}}",
    summary="Fetch service details",
    tags=["Service"],
    response_model=PublicServiceSchema,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(Service.id)]
    ),
    description=(
        """
            **Fetch a service by its ID.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_service_details_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        service = validate_id(session, Service, id, Service.id)
        return fetch_service_details(session, service)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_SERVICE}/{{id}}",
    summary="Delete service",
    tags=["Service"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.DataInUse(Service),
        ]
    ),
    description=(
        """
            **Deletes an existing service.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have the `company.service.delete` permission.    
            - Service can only be deleted if it is in CREATED status.    
            - Returns 204 No Content even if the specified service does not exist.    
        """
    ),
)
async def delete_service_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.DELETE_COMPANY_SERVICE)

        service = session.query(Service).filter(Service.id == id).first()
        if service and service.status != ServiceStatus.CREATED:
            raise exceptions.DataInUse(Service)
        if service is not None:
            service_data = delete_service(session, service)
            log_event(token, request_info, service_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_SERVICE,
    summary="Create service",
    tags=["Service"],
    response_model=ServiceSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue("vehicle_id"),
            exceptions.UnknownValue("route_id"),
            exceptions.UnknownValue("fare_id"),
            exceptions.InvalidAssociation(FareInService.fare_id, Service.company_id),
            exceptions.InactiveResource(Vehicle),
            exceptions.InactiveResource(Company),
            exceptions.InactiveResource(Route),
            exceptions.OverlappingService(),
            exceptions.InvalidValue(Service.starting_at),
        ]
    ),
    description=(
        f"""
            **Creates a new service for a company.**    
            - Requires a valid access token.    
            - Logged in operator must have `company.service.create` permission.    
            - Operator can only create services for the company they belong to.    
            - Validates that the vehicle, route, and fare belong to the specified company.    
            - Status of vehicle must be ACTIVE, company must be VERIFIED, and route must be VALID.    
            - `starting_at` must be between now and `{SERVICE_CREATION_LEAD_TIME_DAYS}` days from now.    
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


@route_operator.patch(
    f"{URL_SERVICE}/{{id}}",
    summary="Update service",
    tags=["Service"],
    response_model=ServiceSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Service.id),
            exceptions.UnknownValue("vehicle_id"),
            exceptions.UnknownValue("route_id"),
            exceptions.UnknownValue("fare_id"),
            exceptions.InvalidStateTransition(Service.status),
            exceptions.InvalidAssociation(FareInService.fare_id, Service.company_id),
            exceptions.InactiveResource(Vehicle),
            exceptions.InactiveResource(Route),
            exceptions.OverlappingService(),
            exceptions.DataInUse(Service),
            exceptions.InvalidValue(Service.starting_at),
        ]
    ),
    description=(
        f"""
            **Updates an existing service for a company.**    
            - Requires a valid access token.    
            - Logged in operator must have `company.service.update` permission.    
            - Allowed status transitions:    
                - CREATED -> CACHED    
                - CACHED -> ENDED    
                - STARTED -> ENDED    
                - ENDED -> STARTED    
            - When status transitions to ENDED, all STARTED duties on the service are ended at the same time.    
            - `vehicle_id`, `route_id`, `fare_id`, and `starting_at` can only be updated when service status is CREATED.    
            - `starting_at` must be between now and `{SERVICE_CREATION_LEAD_TIME_DAYS}` days from now.    
            - Empty PATCH requests are allowed and will result in no changes.    
        """
    ),
)
async def update_service_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_SERVICE)

        service = validate_id(
            session,
            Service,
            id,
            Service.id,
            extra_filter=(Service.company_id == token.company_id),
        )

        update_data = form_param.model_dump(exclude_unset=True)
        vehicle = None
        route = None
        fare = None
        if "vehicle_id" in update_data:
            vehicle = validate_id(
                session,
                Vehicle,
                form_param.vehicle_id,
                "vehicle_id",
                extra_filter=(Vehicle.company_id == token.company_id),
            )
        if "route_id" in update_data:
            route = validate_id(
                session,
                Route,
                form_param.route_id,
                "route_id",
                extra_filter=(Route.company_id == token.company_id),
            )
        if "fare_id" in update_data:
            fare = validate_id(session, Fare, form_param.fare_id, "fare_id")
            if fare.scope != FareScope.GLOBAL and fare.company_id != token.company_id:
                raise exceptions.InvalidAssociation(
                    FareInService.fare_id, Service.company_id
                )

        have_updates, service_data = update_service(
            session,
            service,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            vehicle=vehicle,
            route=route,
            fare=fare,
        )

        if have_updates:
            log_event(token, request_info, service_data)
        return service_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_SERVICE,
    summary="Fetch service",
    tags=["Service"],
    response_model=List[ServiceSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of services.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_service_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_service(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    f"{URL_SERVICE}/{{id}}",
    summary="Fetch service details",
    tags=["Service"],
    response_model=PrivateServiceSchema,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(Service.id)]
    ),
    description=(
        """
            **Fetch a service by its ID.**    
            - Requires a valid access token for authentication.    
            - If `marked_as_cached` query parameter is set to true, and the service status is currently CREATED, the status will be updated to CACHED.    
        """
    ),
)
async def fetch_service_details_for_operator(
    id: int,
    query_params: ServiceQueryParams = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        service = validate_id(
            session,
            Service,
            id,
            Service.id,
            extra_filter=(Service.company_id == token.company_id),
        )
        if query_params.marked_as_cached and service.status == ServiceStatus.CREATED:
            service.status = ServiceStatus.CACHED
            session.commit()
            session.refresh(service)
        return fetch_service_details(session, service)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_SERVICE}/{{id}}",
    summary="Delete service",
    tags=["Service"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.DataInUse(Service),
        ]
    ),
    description=(
        """
            **Deletes an existing service.**    
            - Requires a valid access token for authentication.    
            - The logged-in operator must have the `company.service.delete` permission.    
            - Operator can only delete services within their company.    
            - Service can only be deleted if it is in CREATED status.    
            - Returns 204 No Content even if the specified service does not exist.    
        """
    ),
)
async def delete_service_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.DELETE_COMPANY_SERVICE)

        service = (
            session.query(Service)
            .filter(
                Service.id == id,
                Service.company_id == token.company_id,
            )
            .first()
        )
        if service and service.status != ServiceStatus.CREATED:
            raise exceptions.DataInUse(Service)
        if service is not None:
            service_data = delete_service(session, service)
            log_event(token, request_info, service_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_SERVICE,
    summary="Fetch service",
    tags=["Service"],
    response_model=List[ServiceSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of services.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_service_vendor(
    query_params: QueryParamsForVE = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        verify_token(session, VendorToken, access_token.credentials)

        return search_service(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.get(
    f"{URL_SERVICE}/{{id}}",
    summary="Fetch service details",
    tags=["Service"],
    response_model=PublicServiceSchema,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(Service.id)]
    ),
    description=(
        """
            **Fetch a service by its ID.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_service_details_for_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
):
    try:
        session = SessionLocal()
        verify_token(session, VendorToken, access_token.credentials)

        service = validate_id(session, Service, id, Service.id)
        return fetch_service_details(session, service)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_SERVICE,
    summary="Fetch service",
    tags=["Service"],
    response_model=List[ServiceSchema],
    description=(
        """
            **Fetches a list of services for public users.**    
        """
    ),
)
async def fetch_service_public(query_params: QueryParamsForPU = Depends()):
    try:
        session = SessionLocal()

        query_params = resolve_model_defaults(QueryParams, **query_params.model_dump())
        return search_service(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_public.get(
    f"{URL_SERVICE}/{{id}}",
    summary="Fetch service details",
    tags=["Service"],
    response_model=PublicServiceSchema,
    responses=fuse_exception_responses([exceptions.UnknownValue(Service.id)]),
    description=(
        """
            **Fetches a service by its ID.**    
        """
    ),
)
async def fetch_service_details_public(id: int):
    try:
        session = SessionLocal()

        service = validate_id(session, Service, id, Service.id)
        return fetch_service_details(session, service)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
