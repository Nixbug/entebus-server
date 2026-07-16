"""
Service API Router.

Provides endpoints for managing services:
    - POST (executive, operator)
    - GET (public, executive, operator, vendor)
    - PATCH (executive, operator)
    - DELETE (executive, operator)
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from fastapi.encoders import jsonable_encoder
from typing import Annotated, Any, Union
from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field
from datetime import timedelta
from fastapi import status, Depends
from sqlalchemy import String, and_, func, or_
from sqlalchemy.orm.session import Session
from sqlalchemy.orm import aliased

from app.api.bearer import oauth2_executive, bearer_operator, bearer_vendor
from app.api.fare import FareAttributes
from app.src import schemas
from app.src.schemas import PatchForm
from app.src.urls import URL_SERVICE
from app.src.db import (
    ExecutiveToken,
    OperatorToken,
    Service,
    Duty,
    Route,
    LandmarkInRoute,
    Fare,
    Vehicle,
    FareInService,
    VehicleInService,
    LandmarkInService,
    Company,
    PaperTicket,
    VendorToken,
    ServiceLocation,
    get_db_session,
)
from app.src import exceptions
from app.src.description import Description
from app.src.functions import (
    apply_id_filters,
    get_by_id,
    resolve_model_defaults,
    get_request_info,
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
    authorize_executive,
    authorize_operator,
)
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.enums import (
    AppID,
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
from app.src.digital_ticket.v1 import TicketCreator
from app.src.constants import SERVICE_CREATION_LEAD_TIME_DAYS, TMZ_PRIMARY
from app.src.redis import acquire_lock, release_lock
from app.api.fare import construct_fare_reference_lock
from app.api.vehicle import construct_vehicle_reference_lock

route_executive = APIRouter()
route_operator = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
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
    route_version: int | None
    starting_landmark_id: int
    ending_landmark_id: int
    ticket_mode: int
    remark: str | None
    starting_at: datetime
    ending_at: datetime
    collection: Decimal | None
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
    route: list[LandmarkInServiceSchema]


class PrivateServiceSchema(PublicServiceSchema):
    """Schema for service response with detailed information."""

    public_key: str


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateFormForOP(BaseModel):
    """Form data for creating a new service for an operator."""

    route_id: int = Field()
    fare_id: int = Field()
    vehicle_id: int = Field()
    name: str = Field(min_length=1, max_length=128, pattern=NAME_PATTERN)
    ticket_mode: TicketingMode = Field(
        description=enum_str(TicketingMode), default=TicketingMode.HYBRID
    )
    starting_at: datetime = Field()


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new service for an executive."""

    company_id: int = Field()


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new service."""

    pass


class UpdateForm(PatchForm):
    """Form data for updating a service."""

    name: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=NAME_PATTERN
    )
    ticket_mode: TicketingMode | None = Field(
        default=None, description=enum_str(TicketingMode)
    )
    status: ServiceStatus | None = Field(
        default=None, description=enum_str(ServiceStatus)
    )
    remark: Annotated[str | None, "nullable"] = Field(
        default=None, min_length=1, max_length=1024
    )
    vehicle_id: int | None = Field(default=None)
    route_id: int | None = Field(default=None)
    fare_id: int | None = Field(default=None)
    starting_at: datetime | None = Field(default=None)


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
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
    status_list: list[ServiceStatus] | None = Field(
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

    id_excluding: list[int] | None = Field(Query(default=None))
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


# ---------------------------------------------------------------------------
## Lock Generator
# ---------------------------------------------------------------------------
def construct_service_transition_lock(service_id: int) -> str:
    """
    Creates a Redis lock key for a service.

    Prevents concurrent service transitions, duty state transitions,
    duty creation, and paper ticket creation for the same service.

    Args:
        service_id (int): ID of the service for which to create the lock.
    """
    return f"lk_service_:{service_id}"


def construct_service_creation_lock(registration_number: str) -> str:
    """
    Creates a Redis lock key for service creation.

    Prevents overlapping services from being created concurrently
    for the same vehicle registration number.

    Args:
        registration_number (str): Vehicle registration number.
    """
    return f"lk_service_:{registration_number}"


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
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


def fetch_landmarks_in_route(session: Session, route: Route) -> list[LandmarkInRoute]:
    """
    Fetch and return all landmarks in a given route, ordered by distance from start.

    Args:
        session (Session): SQLAlchemy database session.
        route (Route): The route object for which to fetch landmarks.

    Returns:
        List[LandmarkInRoute]: List of `LandmarkInRoute` objects ordered by distance from start.
    """
    return (
        session.query(LandmarkInRoute)
        .filter(LandmarkInRoute.route_id == route.id)
        .order_by(LandmarkInRoute.distance_from_start.asc())
        .all()
    )


def create_landmarks_in_service(
    session: Session,
    service: Service,
    landmarks_in_route: list[LandmarkInRoute],
) -> list[LandmarkInService]:
    """
    Create landmark snapshot rows for a service based on route timing deltas.

    Args:
        session (Session): SQLAlchemy database session.
        service (Service): The service object.

    Returns:
        List[LandmarkInService]: The list of landmarks in the service.
    """
    landmarks_in_service = []
    for landmark_in_route in landmarks_in_route:
        landmark_in_service = LandmarkInService(
            service_id=service.id,
            landmark_id=landmark_in_route.landmark_id,
            distance_from_start=landmark_in_route.distance_from_start,
            arrival_at=service.starting_at
            + timedelta(minutes=landmark_in_route.arrival_delta),
            departure_at=service.starting_at
            + timedelta(minutes=landmark_in_route.departure_delta),
        )
        landmarks_in_service.append(landmark_in_service)
    session.add_all(landmarks_in_service)
    session.flush()
    return landmarks_in_service


def fetch_landmarks_in_service(
    session: Session, service: Service
) -> list[LandmarkInService]:
    """
    Fetch and return landmark snapshots (`LandmarkInService`) for a service.

    Args:
        session (Session): SQLAlchemy session.
        service (Service): Service object to lookup.

    Returns:
        List[LandmarkInService]: List of `LandmarkInService` objects.
    """
    return (
        session.query(LandmarkInService)
        .filter(LandmarkInService.service_id == service.id)
        .order_by(LandmarkInService.distance_from_start.asc())
        .all()
    )


def delete_landmarks_in_service(session: Session, service: Service) -> None:
    """
    Delete all `LandmarkInService` rows associated with a `Service`.

    Args:
        session (Session): SQLAlchemy session.
        service (Service): Service whose landmark snapshots should be removed.
    """
    session.query(LandmarkInService).filter(
        LandmarkInService.service_id == service.id
    ).delete(synchronize_session=False)
    session.flush()


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


def fetch_fare_in_service(session: Session, service: Service) -> FareInService:
    """
    Fetch and return the `FareInService` snapshot for a service.

    Args :
        session (Session): SQLAlchemy session.
        service (Service): Service object to lookup.

    Returns:
        FareInService: `FareInService` object.
    """
    fare_in_service = (
        session.query(FareInService)
        .filter(FareInService.id == service.fare_in_service_id)
        .first()
    )
    assert fare_in_service is not None, "FareInService snapshot should not be None."
    return fare_in_service


def delete_fare_in_service(session: Session, fare_in_service: FareInService) -> None:
    """
    Decrements the reference count of the `FareInService` snapshot referenced by the
    given `Service` and deletes it if the count reaches zero.

    Args:
        session (Session): SQLAlchemy database session.
        fare_in_service (FareInService): `FareInService` object to be decremented/cleaned up.
    """
    fare_in_service.reference_count -= 1
    if fare_in_service.reference_count == 0:
        session.delete(fare_in_service)


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


def fetch_vehicle_in_service(session: Session, service: Service) -> VehicleInService:
    """
    Fetch and return the `VehicleInService` snapshot for a service.

    Args :
        session (Session): SQLAlchemy session.
        service (Service): Service object to lookup.

    Returns:
        VehicleInService: `VehicleInService` object.
    """
    vehicle_in_service = (
        session.query(VehicleInService)
        .filter(VehicleInService.id == service.vehicle_in_service_id)
        .first()
    )
    assert (
        vehicle_in_service is not None
    ), "VehicleInService snapshot should not be None."
    return vehicle_in_service


def delete_vehicle_in_service(
    session: Session, vehicle_in_service: VehicleInService
) -> None:
    """
    Decrements the reference count of the `VehicleInService` snapshot referenced by the
    given `Service` and deletes it if the count reaches zero.

    Args:
        session (Session): SQLAlchemy database session.
        vehicle_in_service (VehicleInService): `VehicleInService` object to be decremented/cleaned up.
    """
    vehicle_in_service.reference_count -= 1
    if vehicle_in_service.reference_count == 0:
        session.delete(vehicle_in_service)


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_service(
    session: Session,
    form_param: CreateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    route_filter=None,
    vehicle_filter=None,
    fare_filter=None,
) -> dict:
    """
    Creates a new service record in the database.

    Lock Acquisition Order:
        1. Service creation lock (prevent overlapping services)
        2. Fare reference lock (protect fare snapshot updates)
        3. Vehicle reference lock (protect vehicle snapshot updates)

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a service.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        route_filter: Additional filter for route validation.
        vehicle_filter: Additional filter for vehicle validation.
        fare_filter: Additional filter for fare validation.

    Returns:
        dict: The created service data.

    Raises:
        exceptions.InactiveResource: If the vehicle, company, or route is not active/verified/valid.
        exceptions.InvalidValue: If the starting date is not valid.
    """
    service_creation_lock = None
    fare_lock = None
    vehicle_lock = None
    try:
        company = validate_id(
            session,
            Company,
            form_param.company_id,
            Service.company_id,
        )
        if company.status != CompanyStatus.VERIFIED:
            raise exceptions.InactiveResource(Company)

        vehicle = validate_id(
            session,
            Vehicle,
            form_param.vehicle_id,
            Service.vehicle_id,
            extra_filter=vehicle_filter,
        )
        if vehicle.status != VehicleStatus.ACTIVE:
            raise exceptions.InactiveResource(Vehicle)
        route = validate_id(
            session,
            Route,
            form_param.route_id,
            Service.route_id,
            extra_filter=route_filter,
        )
        if route.status != RouteStatus.VALID:
            raise exceptions.InactiveResource(Route)
        fare = validate_id(
            session,
            Fare,
            form_param.fare_id,
            Service.fare_id,
            extra_filter=fare_filter,
        )

        landmarks_in_route = fetch_landmarks_in_route(session, route)
        starting_at = validate_starting_at(form_param.starting_at)
        first_landmark_in_route = landmarks_in_route[0]
        last_landmark_in_route = landmarks_in_route[-1]
        ending_at = starting_at + timedelta(
            minutes=last_landmark_in_route.arrival_delta
        )

        # Prevent assigning the same vehicle to overlapping services (any company)
        service_creation_lock = acquire_lock(
            construct_service_creation_lock(vehicle.registration_number)
        )
        validate_service_timing(
            session, starting_at, ending_at, vehicle.registration_number
        )

        # Acquire fare and vehicle locks to protect snapshot reference counts
        fare_lock = acquire_lock(construct_fare_reference_lock(fare.id, fare.version))
        fare_in_service = create_fare_in_service(session, fare)
        vehicle_lock = acquire_lock(
            construct_vehicle_reference_lock(vehicle.id, vehicle.version)
        )
        vehicle_in_service = create_vehicle_in_service(session, vehicle)

        # Generate keys
        ticket_creator = TicketCreator()
        private_key = ticket_creator.pem_private_key_string
        public_key = ticket_creator.pem_public_key_string

        service = Service(
            company_id=company.id,
            name=form_param.name,
            fare_in_service_id=fare_in_service.id,
            fare_id=fare.id,
            vehicle_in_service_id=vehicle_in_service.id,
            vehicle_id=vehicle.id,
            registration_number=vehicle.registration_number,
            route_id=route.id,
            route_version=route.version,
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
        create_landmarks_in_service(session, service, landmarks_in_route)

        # Create a ServiceLocation entry for the service, linking it to the first landmark in the route
        service_location = ServiceLocation(
            service_id=service.id,
            company_id=company.id,
            landmark_id=first_landmark_in_route.landmark_id,
        )
        session.add(service_location)

        session.commit()
        session.refresh(service)

        service_data = jsonable_encoder(service, exclude={"private_key"})
        log_event(token, request_info, service_data)
        return service_data
    finally:
        release_lock(vehicle_lock)
        release_lock(fare_lock)
        release_lock(service_creation_lock)


def update_service(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    service_filter=None,
    route_filter=None,
    vehicle_filter=None,
    fare_filter=None,
) -> dict:
    """
    Updates an existing service record.

    Supports service status transitions CREATED -> CACHED, CACHED -> ENDED,
    STARTED -> ENDED, and ENDED -> STARTED. When a service is ended, all STARTED duties on that
    service are ended at the same UTC timestamp and their collection totals are
    finalized from related paper tickets. Reactivating a service does not
    reactivate duties.

    Lock Acquisition Order:
        1. Service transition lock (prevent concurrent modifications to the service_)
        2. Old fare reference lock (if updating fare, protect old fare snapshot reference count update and potential deletion)
        3. New fare reference lock (if updating fare, protect new fare snapshot creation or reference count update)
        4. Old vehicle reference lock (if updating vehicle, protect old vehicle snapshot reference count update and potential deletion)
        5. New vehicle reference lock (if updating vehicle, protect new vehicle snapshot creation or reference count update)
        6. Service creation lock (prevent overlapping services)

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the service to update.
        form_param (UpdateForm): Form data for updating the service.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        service_filter: Additional filter for service validation.
        route_filter: Additional filter for route validation.
        vehicle_filter: Additional filter for vehicle validation.
        fare_filter: Additional filter for fare validation.

    Returns:
        dict: Updated service data.
    """
    service_lock = None
    service_creation_lock = None
    fare_lock_1 = None
    fare_lock_2 = None
    vehicle_lock_1 = None
    vehicle_lock_2 = None
    try:
        service = validate_id(
            session, Service, id, Service.id, extra_filter=service_filter
        )
        service_lock = acquire_lock(construct_service_transition_lock(service.id))
        session.refresh(service)

        if request_info.app_id == AppID.EXECUTIVE:
            route_filter = Route.company_id == service.company_id
            vehicle_filter = Vehicle.company_id == service.company_id
            fare_filter = (Fare.company_id == service.company_id) | (
                Fare.scope == FareScope.GLOBAL
            )

        update_data = form_param.model_dump(exclude_unset=True)
        revalidate_service_timing = False
        have_updates = False
        if "vehicle_id" in update_data:
            vehicle = validate_id(
                session,
                Vehicle,
                update_data["vehicle_id"],
                Service.vehicle_id,
                extra_filter=vehicle_filter,
            )
            vehicle_in_service = fetch_vehicle_in_service(session, service)

            vehicle_changed = vehicle.id != vehicle_in_service.vehicle_id
            if vehicle_changed or vehicle.version != vehicle_in_service.version:
                if service.status != ServiceStatus.CREATED:
                    raise exceptions.DataInUse(Service)
                if vehicle.status != VehicleStatus.ACTIVE:
                    raise exceptions.InactiveResource(Vehicle)

                # Acquire locks for old and new vehicle snapshots ensuring consistent
                # lock acquisition order to prevent deadlocks
                old_vehicle_lock_key = construct_vehicle_reference_lock(
                    vehicle_in_service.vehicle_id, vehicle_in_service.version
                )
                new_vehicle_lock_key = construct_vehicle_reference_lock(
                    vehicle.id, vehicle.version
                )
                first_lock_key, second_lock_key = sorted(
                    (old_vehicle_lock_key, new_vehicle_lock_key)
                )
                vehicle_lock_1 = acquire_lock(first_lock_key)
                vehicle_lock_2 = acquire_lock(second_lock_key)

                old_vehicle_in_service = vehicle_in_service
                new_vehicle_in_service = create_vehicle_in_service(session, vehicle)
                delete_vehicle_in_service(session, old_vehicle_in_service)

                if vehicle_changed:
                    service.registration_number = vehicle.registration_number
                    service.vehicle_id = vehicle.id
                    revalidate_service_timing = True
                service.vehicle_in_service_id = new_vehicle_in_service.id
                session.flush()
                have_updates = True
            update_data.pop("vehicle_id")
        if "fare_id" in update_data:
            fare = validate_id(
                session,
                Fare,
                update_data["fare_id"],
                Service.fare_id,
                extra_filter=fare_filter,
            )
            fare_in_service = fetch_fare_in_service(session, service)

            fare_changed = fare.id != fare_in_service.fare_id
            if fare_changed or fare.version != fare_in_service.version:
                if service.status != ServiceStatus.CREATED:
                    raise exceptions.DataInUse(Service)

                # Acquire locks for old and new fare snapshots ensuring consistent
                # lock acquisition order to prevent deadlocks
                old_fare_lock_key = construct_fare_reference_lock(
                    fare_in_service.fare_id, fare_in_service.version
                )
                new_fare_lock_key = construct_fare_reference_lock(fare.id, fare.version)
                first_lock_key, second_lock_key = sorted(
                    (old_fare_lock_key, new_fare_lock_key)
                )
                fare_lock_1 = acquire_lock(first_lock_key)
                fare_lock_2 = acquire_lock(second_lock_key)

                old_fare_in_service = fare_in_service
                new_fare_in_service = create_fare_in_service(session, fare)
                delete_fare_in_service(session, old_fare_in_service)

                if fare_changed:
                    service.fare_id = fare.id
                service.fare_in_service_id = new_fare_in_service.id
                session.flush()
                have_updates = True
            update_data.pop("fare_id")
        if "route_id" in update_data:
            route = validate_id(
                session,
                Route,
                update_data["route_id"],
                Service.route_id,
                extra_filter=route_filter,
            )

            if route.id != service.route_id or route.version != service.route_version:
                if service.status != ServiceStatus.CREATED:
                    raise exceptions.DataInUse(Service)
                if route.status != RouteStatus.VALID:
                    raise exceptions.InactiveResource(Route)

                delete_landmarks_in_service(session, service)
                landmarks_in_route = fetch_landmarks_in_route(session, route)
                first_landmark_in_route = landmarks_in_route[0]
                last_landmark_in_route = landmarks_in_route[-1]
                ending_at = service.starting_at + timedelta(
                    minutes=last_landmark_in_route.arrival_delta
                )
                create_landmarks_in_service(session, service, landmarks_in_route)

                service.ending_at = ending_at
                service.starting_landmark_id = first_landmark_in_route.landmark_id
                service.ending_landmark_id = last_landmark_in_route.landmark_id
                service.route_id = route.id
                service.route_version = route.version
                revalidate_service_timing = True
                session.flush()
                have_updates = True
            update_data.pop("route_id")
        if "starting_at" in update_data:
            if update_data["starting_at"] != service.starting_at:
                if service.status != ServiceStatus.CREATED:
                    raise exceptions.DataInUse(Service)

                old_starting_at = service.starting_at
                new_starting_at = validate_starting_at(update_data["starting_at"])
                time_difference = new_starting_at - old_starting_at
                session.query(LandmarkInService).filter(
                    LandmarkInService.service_id == service.id
                ).update(
                    {
                        LandmarkInService.arrival_at: LandmarkInService.arrival_at
                        + time_difference,
                        LandmarkInService.departure_at: LandmarkInService.departure_at
                        + time_difference,
                    },
                    synchronize_session=False,
                )
                service.starting_at = new_starting_at
                service.ending_at = service.ending_at + time_difference
                revalidate_service_timing = True
                session.flush()
                have_updates = True
            update_data.pop("starting_at")

        if revalidate_service_timing:
            service_creation_lock = acquire_lock(
                construct_service_creation_lock(service.registration_number)
            )
            validate_service_timing(
                session,
                service.starting_at,
                service.ending_at,
                service.registration_number,
                exclude_service_id=service.id,
            )

        if "status" in update_data:
            if update_data["status"] != service.status:
                allowed_service_status_transitions = {
                    ServiceStatus.CREATED: [ServiceStatus.CACHED],
                    ServiceStatus.CACHED: [ServiceStatus.ENDED],
                    ServiceStatus.STARTED: [ServiceStatus.ENDED],
                    ServiceStatus.ENDED: [ServiceStatus.STARTED],
                }
                validate_state_transition(
                    allowed_service_status_transitions,
                    service.status,
                    update_data["status"],
                    Service.status,
                )

                if update_data["status"] == ServiceStatus.STARTED:
                    service.collection = Decimal(0)
                elif update_data["status"] == ServiceStatus.ENDED:
                    utc_now = datetime.now(TMZ_PRIMARY)
                    duties = (
                        session.query(Duty).filter(Duty.service_id == service.id).all()
                    )

                    collections_by_duty_id: dict[int, Decimal] = {
                        duty_id: total
                        for duty_id, total in session.query(
                            PaperTicket.duty_id, func.sum(PaperTicket.amount)
                        )
                        .filter(PaperTicket.duty_id.in_([duty.id for duty in duties]))
                        .group_by(PaperTicket.duty_id)
                        .all()
                    }

                    service_collection = Decimal(0)
                    for duty in duties:
                        if duty.status == DutyStatus.STARTED:
                            duty.finished_on = utc_now
                            duty.status = DutyStatus.ENDED
                        duty.collection = collections_by_duty_id.get(duty.id)
                        service_collection += duty.collection or Decimal(0)
                    service.collection = service_collection
                service.status = update_data["status"]
                session.flush()
                have_updates = True
            update_data.pop("status")

        update_if_changed(service, update_data)
        if have_updates or session.is_modified(service):
            session.commit()
            session.refresh(service)
            service_data = jsonable_encoder(service, exclude={"private_key"})
            log_event(token, request_info, service_data)
        else:
            service_data = jsonable_encoder(service, exclude={"private_key"})
        return service_data
    finally:
        release_lock(vehicle_lock_1)
        release_lock(vehicle_lock_2)
        release_lock(fare_lock_1)
        release_lock(fare_lock_2)
        release_lock(service_creation_lock)
        release_lock(service_lock)


def search_service(session: Session, query_params: QueryParams) -> list[Service]:
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
    services_to_consider = None

    if (
        query_params.starting_landmark_id is not None
        and query_params.ending_landmark_id is not None
    ):
        # Find services with both landmarks where starting arrives before ending
        # Use aliases to differentiate between starting and ending landmarks
        starting_lmk = aliased(LandmarkInService)
        ending_lmk = aliased(LandmarkInService)

        services_to_consider = (
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
        services_to_consider = (
            session.query(LandmarkInService.service_id)
            .filter(LandmarkInService.landmark_id == query_params.starting_landmark_id)
            .distinct()
        )

    elif query_params.ending_landmark_id is not None:
        # Find services with the ending landmark only
        services_to_consider = (
            session.query(LandmarkInService.service_id)
            .filter(LandmarkInService.landmark_id == query_params.ending_landmark_id)
            .distinct()
        )

    query = session.query(Service)
    if services_to_consider is not None:
        query = query.filter(Service.id.in_(services_to_consider))
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


def fetch_service_details(
    session: Session, id: int, service_filter=None
) -> dict[str, Any]:
    """
    Returns details of a service along with related entities like landmarks, fare, and vehicle in service.

    Args:
        session (Session): SQLAlchemy session.
        id (int): ID of the service to lookup.
        service_filter: Optional filter to apply when fetching the service.

    Returns dict[str, Any]:
        - Dict[str, Any]: JSON-encoded representation of the service details.
    """
    service = get_by_id(session, Service, id, extra_filter=service_filter)
    if service is None:
        raise exceptions.UnknownValue(Service.id)

    landmarks_in_service = fetch_landmarks_in_service(session, service)
    fare_in_service = fetch_fare_in_service(session, service)
    vehicle_in_service = fetch_vehicle_in_service(session, service)

    service_data = jsonable_encoder(service, exclude={"private_key"})
    return {
        **service_data,
        "route": jsonable_encoder(landmarks_in_service),
        "fare": jsonable_encoder(fare_in_service),
        "vehicle": jsonable_encoder(vehicle_in_service),
    }


def delete_service(
    session: Session,
    id: int,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    service_filter=None,
) -> None:
    """
    Deletes a service from the database and decrements/cleans up related snapshot reference counts.

    This function ensures that when a service is deleted:
    1. The FareInService snapshot reference_count is decremented
    2. If FareInService reference_count reaches 0, the snapshot is deleted
    3. The VehicleInService snapshot reference_count is decremented
    4. If VehicleInService reference_count reaches 0, the snapshot is deleted
    5. The service and related LandmarkInService entries are deleted

    Lock Acquisition Order:
        1. Service transition lock (prevent concurrent modifications to the service)
        2. Fare reference lock (protect fare snapshot updates)
        3. Vehicle reference lock (protect vehicle snapshot updates)

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the service to delete.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    service_lock = None
    fare_lock = None
    vehicle_lock = None
    try:
        service = get_by_id(session, Service, id, extra_filter=service_filter)
        if service is None:
            return

        service_lock = acquire_lock(construct_service_transition_lock(service.id))
        session.refresh(service)
        if service.status != ServiceStatus.CREATED:
            raise exceptions.DataInUse(Service)
        service_data = jsonable_encoder(service, exclude={"private_key", "public_key"})
        session.delete(service)
        session.flush()

        delete_landmarks_in_service(session, service)
        fare_in_service = fetch_fare_in_service(session, service)
        fare_lock = acquire_lock(
            construct_fare_reference_lock(
                fare_in_service.fare_id, fare_in_service.version
            )
        )
        delete_fare_in_service(session, fare_in_service)
        vehicle_in_service = fetch_vehicle_in_service(session, service)
        vehicle_lock = acquire_lock(
            construct_vehicle_reference_lock(
                vehicle_in_service.vehicle_id, vehicle_in_service.version
            )
        )
        delete_vehicle_in_service(session, vehicle_in_service)

        session.commit()
        log_event(token, request_info, service_data)
    finally:
        release_lock(vehicle_lock)
        release_lock(fare_lock)
        release_lock(service_lock)


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue("vehicle_id"),
    exceptions.UnknownValue("route_id"),
    exceptions.UnknownValue("fare_id"),
    exceptions.InactiveResource(Vehicle),
    exceptions.InactiveResource(Company),
    exceptions.InactiveResource(Route),
    exceptions.OverlappingService(),
    exceptions.InvalidValue(Service.starting_at),
    exceptions.UnknownValue(Service.company_id),
    exceptions.LockAcquireTimeout(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Service.id),
    exceptions.UnknownValue("vehicle_id"),
    exceptions.UnknownValue("route_id"),
    exceptions.UnknownValue("fare_id"),
    exceptions.InvalidStateTransition(Service.status),
    exceptions.InactiveResource(Vehicle),
    exceptions.InactiveResource(Route),
    exceptions.OverlappingService(),
    exceptions.DataInUse(Service),
    exceptions.InvalidValue(Service.starting_at),
    exceptions.LockAcquireTimeout(),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.DataInUse(Service),
    exceptions.LockAcquireTimeout(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]

GET_DETAIL_EXCEPTIONS = [
    exceptions.UnknownValue(Service.id),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Create a new service.")
    .add_line("Logged-in user must have service creation permission.")
    .add_line("Validates that the vehicle, route, and fare are valid and accessible.")
    .add_line(
        "Status of vehicle must be ACTIVE, company must be VERIFIED, and route must be VALID."
    )
    .add_line(
        f"`starting_at` must be between now and {SERVICE_CREATION_LEAD_TIME_DAYS} days from now."
    )
    .add_line(
        "The service name is auto-generated based on the route, vehicle, and starting date if not provided."
    )
    .add_line("By default the status of the service is set to CREATED.")
    .add_line(
        "When a service is created, a corresponding service location is also created."
    )
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Update an existing service.")
    .add_line("Logged-in user must have service update permission.")
    .add_line(
        "Allowed status transitions: CREATED → CACHED, CACHED → ENDED, STARTED → ENDED, ENDED → STARTED."
    )
    .add_line(
        "When status transitions to ENDED, all STARTED duties on the service are ended at the same time."
    )
    .add_line(
        "`vehicle_id`, `route_id`, `fare_id`, and `starting_at` can only be updated when service status is CREATED."
    )
    .add_line(
        f"`starting_at` must be between now and {SERVICE_CREATION_LEAD_TIME_DAYS} days from now."
    )
    .add_line(
        "When status transitions to ENDED, the service collection is calculated and saved."
    )
    .add_line(
        "When status transitions from ENDED to STARTED, the service collection is reset to 0."
    )
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Delete an existing service.")
    .add_line("Logged-in user must have service delete permission.")
    .add_line("Service can only be deleted if it is in CREATED status.")
    .add_line("Returns 204 No Content even if the specified service does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of services.")

GET_DETAIL_DESCRIPTION = Description().add_head("Fetch service details by ID.")


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_SERVICE,
    summary="Create service",
    tags=["Service"],
    response_model=ServiceSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=POST_DESCRIPTION.copy()
    .add_line("Logged-in executive must have `company.service.create` permission.")
    .add_line(
        "`company_id` is required and used to validate route, fare, and vehicle ownership."
    )
    .to_string(),
)
async def create_service_for_executive(
    form_param: CreateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [ExecutivePermissionPath.CREATE_COMPANY_SERVICE]
        )
        return create_service(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
            route_filter=(Route.company_id == form_param.company_id),
            vehicle_filter=(Vehicle.company_id == form_param.company_id),
            fare_filter=(Fare.company_id == form_param.company_id)
            | (Fare.scope == FareScope.GLOBAL),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_SERVICE}/{{id}}",
    summary="Update service",
    tags=["Service"],
    response_model=ServiceSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=PATCH_DESCRIPTION.copy()
    .add_line("Logged-in executive must have `company.service.update` permission.")
    .to_string(),
)
async def update_service_for_executive(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [ExecutivePermissionPath.UPDATE_COMPANY_SERVICE]
        )
        return update_service(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_SERVICE,
    summary="Fetch service",
    tags=["Service"],
    response_model=list[ServiceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_services_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_service(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    f"{URL_SERVICE}/{{id}}",
    summary="Fetch service details",
    tags=["Service"],
    response_model=PublicServiceSchema,
    responses=fuse_exception_responses(
        [*GET_DETAIL_EXCEPTIONS, exceptions.InvalidToken()]
    ),
    description=GET_DETAIL_DESCRIPTION.to_string(),
)
async def fetch_service_details_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return fetch_service_details(session, id)
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_SERVICE}/{{id}}",
    summary="Delete service",
    tags=["Service"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=DELETE_DESCRIPTION.copy()
    .add_line("Logged-in executive must have `company.service.delete` permission.")
    .to_string(),
)
async def delete_service_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session, access_token, [ExecutivePermissionPath.DELETE_COMPANY_SERVICE]
        )
        delete_service(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_SERVICE,
    summary="Create service",
    tags=["Service"],
    response_model=ServiceSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=POST_DESCRIPTION.copy()
    .add_line("Logged-in operator must have `company.service.create` permission.")
    .to_string(),
)
async def create_service_for_operator(
    form_param: CreateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.CREATE_COMPANY_SERVICE],
        )
        return create_service(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            token,
            request_info,
            route_filter=(Route.company_id == token.company_id),
            vehicle_filter=(Vehicle.company_id == token.company_id),
            fare_filter=(Fare.company_id == token.company_id)
            | (Fare.scope == FareScope.GLOBAL),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.patch(
    f"{URL_SERVICE}/{{id}}",
    summary="Update service",
    tags=["Service"],
    response_model=ServiceSchema,
    status_code=status.HTTP_200_OK,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=PATCH_DESCRIPTION.copy()
    .add_line("Logged-in operator must have `company.service.update` permission.")
    .to_string(),
)
async def update_service_for_operator(
    id: int,
    form_param: UpdateForm,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_SERVICE],
        )
        return update_service(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
            service_filter=(Service.company_id == token.company_id),
            route_filter=(Route.company_id == token.company_id),
            vehicle_filter=(Vehicle.company_id == token.company_id),
            fare_filter=(Fare.company_id == token.company_id)
            | (Fare.scope == FareScope.GLOBAL),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_SERVICE,
    summary="Fetch service",
    tags=["Service"],
    response_model=list[ServiceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_services_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_service(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    f"{URL_SERVICE}/{{id}}",
    summary="Fetch service details",
    tags=["Service"],
    response_model=PrivateServiceSchema,
    responses=fuse_exception_responses(
        [*GET_DETAIL_EXCEPTIONS, exceptions.InvalidToken()]
    ),
    description=GET_DETAIL_DESCRIPTION.copy()
    .add_line("PATCH the status to CACHED state if you intend to use this service.")
    .to_string(),
)
async def fetch_service_details_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return fetch_service_details(
            session, id, service_filter=(Service.company_id == token.company_id)
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_SERVICE}/{{id}}",
    summary="Delete service",
    tags=["Service"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=DELETE_DESCRIPTION.copy()
    .add_line("Logged-in operator must have `company.service.delete` permission.")
    .to_string(),
)
async def delete_service_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.DELETE_COMPANY_SERVICE],
        )
        delete_service(
            session,
            id,
            token,
            request_info,
            service_filter=(Service.company_id == token.company_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.get(
    URL_SERVICE,
    summary="Fetch service",
    tags=["Service"],
    response_model=list[ServiceSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=GET_DESCRIPTION.to_string(),
)
async def fetch_services_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return search_service(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.get(
    f"{URL_SERVICE}/{{id}}",
    summary="Fetch service details",
    tags=["Service"],
    response_model=PublicServiceSchema,
    responses=fuse_exception_responses(
        [*GET_DETAIL_EXCEPTIONS, exceptions.InvalidToken()]
    ),
    description=GET_DETAIL_DESCRIPTION.to_string(),
)
async def fetch_service_details_for_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, VendorToken, access_token.credentials)
        return fetch_service_details(session, id)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_SERVICE,
    summary="Fetch service",
    tags=["Service"],
    response_model=list[ServiceSchema],
    description=(
        GET_DESCRIPTION.copy()
        .add_line("Public users can fetch services without authentication.")
        .to_string()
    ),
)
async def fetch_services_for_public(
    query_params: QueryParamsForPU = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        query_params = resolve_model_defaults(QueryParams, **query_params.model_dump())
        return search_service(session, query_params)
    except Exception as e:
        exceptions.handle(e)


@route_public.get(
    f"{URL_SERVICE}/{{id}}",
    summary="Fetch service details",
    tags=["Service"],
    response_model=PublicServiceSchema,
    responses=fuse_exception_responses(GET_DETAIL_EXCEPTIONS),
    description=GET_DETAIL_DESCRIPTION.to_string(),
)
async def fetch_service_details_for_public(
    id: int,
    session: Session = Depends(get_db_session),
):
    try:
        return fetch_service_details(session, id)
    except Exception as e:
        exceptions.handle(e)
