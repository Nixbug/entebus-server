"""
This module generates input data or payloads for tests.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from PIL import Image
import numpy as np
from shapely import wkt

from app.src.enums import GenderType, GrantType, PlatformType
from app.src.enums import (
    LandmarkType,
    CompanyStatus,
    CompanyType,
    OperatorType,
    AccountStatus,
    VehicleStatus,
    FareScope,
)

EX_ADMIN_CREDENTIALS = {
    "username": "admin",
    "password": "password",
    "client_details": "client_details",
    "platform_type": PlatformType.WEB.value,
    "grant_type": GrantType.PASSWORD.value,
}
EX_GUEST_CREDENTIALS = {
    "username": "guest",
    "password": "password",
    "client_details": "client_details",
    "platform_type": PlatformType.WEB.value,
    "grant_type": GrantType.PASSWORD.value,
}


# Payload generators for dynamic test data
def generate_executive_account_payload():
    suffix = str(np.random.randint(1000, 9999))
    return {
        "username": f"account{suffix}",
        "password": "password",
        "gender": GenderType.OTHER,
        "full_name": f"Account {suffix}",
        "designation": f"Tester {suffix}",
        "phone_number": f"+91-949680{suffix}",
        "email_id": f"account{suffix}@example.com",
    }


def generate_operator_account_payload(company_id: int):
    suffix = str(np.random.randint(1000, 9999))
    return {
        "username": f"operator{suffix}",
        "password": "password",
        "company_id": company_id,
        "gender": GenderType.OTHER,
        "description": f"Operator {suffix} for company {company_id}",
        "type": OperatorType.NORMAL,
        "full_name": f"Operator {suffix}",
        "status": AccountStatus.ACTIVE,
        "phone_number": f"+91-900000{suffix}",
        "email_id": f"operator{suffix}@example.com",
    }


def generate_operator_role_payload(company_id: int, permissions: dict):
    suffix = str(np.random.randint(1000, 9999))
    return {
        "company_id": company_id,
        "name": f"op-role-{suffix}",
        "permissions": permissions,
    }


def generate_executive_role_payload(permissions: dict):
    suffix = str(np.random.randint(1000, 9999))
    return {
        "name": f"ex-role-{suffix}",
        "permissions": permissions,
    }


def generate_company_payload():
    suffix = str(np.random.randint(1000, 9999))
    return {
        "name": f"Company {suffix}",
        "status": CompanyStatus.VERIFIED,
        "type": CompanyType.PRIVATE,
        "description": f"A sample company {suffix} used in tests",
        "address": f"{suffix} Main St, City",
        "contact_number": f"+91-949680{suffix}",
        "email_id": f"company{suffix}@example.com",
        "location": f"POINT(77.59{suffix} 12.97{suffix})",
    }


def generate_vehicle_payload(company_id: int):
    suffix = str(np.random.randint(1000, 9999))
    return {
        "company_id": company_id,
        "registration_number": f"KA01AB{suffix}",
        "name": f"Vehicle {suffix}",
        "capacity": np.random.randint(20, 50),
        "manufactured_on": None,
        "insurance_upto": None,
        "pollution_upto": None,
        "fitness_upto": None,
        "road_tax_upto": None,
        "status": VehicleStatus.ACTIVE,
    }


def generate_route_payload(company_id: int):
    suffix = str(np.random.randint(1000, 9999))
    return {
        "company_id": company_id,
        "name": f"Route {suffix}",
        "start_time": "08:00:00",
    }


def generate_fare_payload(company_id: int):
    suffix = str(np.random.randint(1000, 9999))
    return {
        "company_id": company_id,
        "name": f"Fare {suffix}",
        "attributes": {
            "df_version": 1,
            "ticket_types": [{"id": 1, "name": "regular"}],
            "currency_type": "INR",
            "distance_unit": "meter",
            "extras": {},
        },
        "function": "function getFare(type, distance, extras) { return 10; }",
        "scope": FareScope.LOCAL,
    }


def generate_landmark_payload():
    suffix = str(np.random.randint(1000, 9999))
    center_lon = float(77.59 + np.random.uniform(0, 0.01))
    center_lat = float(12.97 + np.random.uniform(0, 0.01))
    # small offsets in degrees (~10-50 meters)
    d1 = float(np.random.uniform(0.00005, 0.0002))
    d2 = float(np.random.uniform(0.00005, 0.0002))
    p1 = (center_lon - d1, center_lat - d2)
    p2 = (center_lon - d1, center_lat + d2)
    p3 = (center_lon + d1, center_lat + d2)
    p4 = (center_lon + d1, center_lat - d2)
    boundary = f"POLYGON(({p1[0]} {p1[1]}, {p2[0]} {p2[1]}, {p3[0]} {p3[1]}, {p4[0]} {p4[1]}, {p1[0]} {p1[1]}))"
    return {
        "name": f"Landmark {suffix}",
        "boundary": boundary,
        "type": LandmarkType.LOCAL,
        "alias_names": [f"lm{suffix}"],
    }


def generate_bus_stop_payload(landmark_id: int, boundary: str):
    suffix = str(np.random.randint(1000, 9999))

    geom = wkt.loads(boundary) if boundary else None
    if geom is not None and not geom.is_empty:
        pt = geom.representative_point()
        lon, lat = float(pt.x), float(pt.y)
    else:
        # Fallback to random point if boundary is invalid
        lon = float(77.59 + np.random.uniform(0, 0.01))
        lat = float(12.97 + np.random.uniform(0, 0.01))

    location = f"POINT({round(lon,6)} {round(lat,6)})"
    return {
        "name": f"Bus Stop {suffix}",
        "landmark_id": landmark_id,
        "location": location,
    }


def generate_landmark_in_route_payload(
    route_id: int,
    landmark_id: int,
    distance_from_start: int,
    arrival_delta: int,
    departure_delta: int,
):
    return {
        "route_id": route_id,
        "landmark_id": landmark_id,
        "distance_from_start": distance_from_start,
        "arrival_delta": arrival_delta,
        "departure_delta": departure_delta,
    }


def generate_service_payload(
    company_id: int, route_id: int, fare_id: int, vehicle_id: int
):
    # choose a random start offset in minutes between 0 and 1440 (24 hours)
    minutes_offset = int(np.random.randint(0, 1441))
    return {
        "starting_at": (
            datetime.now(timezone.utc) + timedelta(minutes=minutes_offset)
        ).isoformat(),
        "company_id": company_id,
        "route_id": route_id,
        "fare_id": fare_id,
        "vehicle_id": vehicle_id,
    }


def generate_service_assignment_payload(
    service_id: int, operator_id: int, company_id: int
) -> dict:
    return {
        "service_id": service_id,
        "operator_id": operator_id,
        "company_id": company_id,
    }


# Utility function to generate a random image for testing purposes
def generate_test_image(height=256, width=256):
    img_array = np.random.randint(0, 4, (height, width, 3), dtype=np.uint8)
    img = Image.fromarray(img_array, "RGB")

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return {"file": ("exec_test.png", buffer, "image/png")}
