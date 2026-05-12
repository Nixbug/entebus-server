"""
This module provides input data for tests.
"""

from io import BytesIO
from PIL import Image
import numpy as np

from app.src.enums import GenderType, GrantType, PlatformType
from app.src.permissions.executive import PermissionSchema as PermissionSchemaEX
from app.src.permissions.operator import PermissionSchema as PermissionSchemaOP
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
    "platform_type": PlatformType.WEB,
    "grant_type": GrantType.PASSWORD,
}
EX_GUEST_CREDENTIALS = {
    "username": "guest",
    "password": "password",
    "client_details": "client_details",
    "platform_type": PlatformType.WEB,
    "grant_type": GrantType.PASSWORD,
}

# Prime input resources
EX_ACCOUNT_1 = {
    "username": "account1",
    "password": "password",
    "gender": GenderType.MALE,
    "full_name": "Account One",
    "designation": "Tester",
    "phone_number": "+91-9496801234",
    "email_id": "account1@example.com",
}
LANDMARK_1 = {
    "name": "landmark-1",
    "boundary": "POLYGON((77.5946 12.9716, 77.5946 12.9717, 77.5947 12.9717, 77.5947 12.9716, 77.5946 12.9716))",
    "type": LandmarkType.LOCAL,
    "alias_names": ["lm1"],
}
LANDMARK_2 = {
    "name": "landmark-2",
    "boundary": "POLYGON((77.5950 12.9720, 77.5950 12.9721, 77.5951 12.9721, 77.5951 12.9720, 77.5950 12.9720))",
    "type": LandmarkType.LOCAL,
    "alias_names": ["lm2"],
}
BUS_STOP_IN_LANDMARK_1 = {
    "name": "bus-stop-1",
    "landmark_id": 0,  # to be updated with actual landmark id during test execution
    "location": "POINT(77.59465 12.97165)",
}
BUS_STOP_IN_LANDMARK_2 = {
    "name": "bus-stop-2",
    "landmark_id": 0,  # to be updated with actual landmark id during test execution
    "location": "POINT(77.59505 12.97205)",
}
COMPANY_1 = {
    "name": "Company One",
    "status": CompanyStatus.VERIFIED,
    "type": CompanyType.PRIVATE,
    "description": "A sample private transport company used in tests",
    "address": "123 Main St, City",
    "contact_number": "+91-9496801234",
    "email_id": "company1@example.com",
    "location": "POINT(77.59465 12.97165)",
}
OP_ACCOUNT_1 = {
    "username": "operator1",
    "password": "password",
    "company_id": 0,  # to be updated with actual company id during test execution
    "gender": GenderType.OTHER,
    "description": "Operator One for company one",
    "type": OperatorType.NORMAL,
    "full_name": "Operator One",
    "status": AccountStatus.ACTIVE,
    "phone_number": "+91-9000000001",
    "email_id": "operator1@example.com",
}
VEHICLE_1 = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "registration_number": "KA01AB1234",
    "name": "Vehicle One",
    "capacity": 40,
    "manufactured_on": None,
    "insurance_upto": None,
    "pollution_upto": None,
    "fitness_upto": None,
    "road_tax_upto": None,
    "status": VehicleStatus.CREATED,
}
FARE_1 = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "name": "fare-1",
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
ROUTE_1 = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "name": "route-1",
    "start_time": "08:00:00",
}
OP_ADMIN_ROLE = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "name": "op-admin-role-1",
    "permissions": PermissionSchemaOP.all_granted().model_dump(),
}
OP_GUEST_ROLE = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "name": "op-guest-role-1",
    "permissions": PermissionSchemaOP.all_denied().model_dump(),
}
LANDMARK_1_IN_ROUTE_1 = {
    "route_id": 0,  # to be updated with actual route id during test execution
    "landmark_id": 0,  # to be updated with actual landmark id during test execution
    "distance_from_start": 0,
    "arrival_delta": 0,
    "departure_delta": 1,
}
LANDMARK_2_IN_ROUTE_1 = {
    "route_id": 0,  # to be updated with actual route id during test execution
    "landmark_id": 0,  # to be updated with actual landmark id during test execution
    "distance_from_start": 1000,
    "arrival_delta": 2,
    "departure_delta": 3,
}


# Common input resources
EX_ADMIN_ROLE = {
    "name": "admin-role-1",
    "permissions": PermissionSchemaEX.all_granted().model_dump(),
}
EX_GUEST_ROLE = {
    "name": "guest-role-1",
    "permissions": PermissionSchemaEX.all_denied().model_dump(),
}

# Secondary input resources
EX_ACCOUNT_2 = {
    "username": "account2",
    "password": "password",
    "gender": GenderType.FEMALE,
    "full_name": "Account Two",
    "designation": "Tester",
    "phone_number": "+91-9496805678",
    "email_id": "account2@example.com",
}
LANDMARK_3 = {
    "name": "landmark-3",
    "boundary": "POLYGON((77.5965 12.9735, 77.5965 12.9736, 77.5966 12.9736, 77.5966 12.9735, 77.5965 12.9735))",
    "type": LandmarkType.LOCAL,
    "alias_names": ["lm3"],
}
BUS_STOP_IN_LANDMARK_3 = {
    "name": "bus-stop-3",
    "landmark_id": 0,  # to be updated with actual landmark id during test execution
    "location": "POINT(77.59655 12.97355)",
}
COMPANY_2 = {
    "name": "Company Two",
    "status": CompanyStatus.UNDER_VERIFICATION,
    "type": CompanyType.OTHER,
    "description": "Another sample company for tests",
    "address": "456 Second St, City",
    "contact_number": "+91-9496805678",
    "email_id": "company2@example.com",
    "location": "POINT(77.59505 12.97205)",
}
OP_TEST_ROLE = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "name": "op-test-role-1",
    "permissions": PermissionSchemaOP.all_denied().model_dump(),
}
OP_ACCOUNT_2 = {
    "username": "operator2",
    "password": "password",
    "company_id": 0,  # to be updated with actual company id during test execution
    "gender": GenderType.FEMALE,
    "description": "Operator Two for company two",
    "type": OperatorType.MANAGER,
    "full_name": "Operator Two",
    "status": AccountStatus.ACTIVE,
    "phone_number": "+91-9000000002",
    "email_id": "operator2@example.com",
}
VEHICLE_2 = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "registration_number": "KA01CD5678",
    "name": "Vehicle Two",
    "capacity": 30,
    "manufactured_on": None,
    "insurance_upto": None,
    "pollution_upto": None,
    "fitness_upto": None,
    "road_tax_upto": None,
    "status": VehicleStatus.CREATED,
}
FARE_2 = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "name": "fare-2",
    "attributes": {
        "df_version": 1,
        "ticket_types": [{"id": 1, "name": "regular"}, {"id": 2, "name": "senior"}],
        "currency_type": "INR",
        "distance_unit": "meter",
        "extras": {"surcharge": 5},
    },
    "function": "function getFare(type, distance, extras) { return 5 ; }",
    "scope": FareScope.LOCAL,
}
ROUTE_2 = {
    "company_id": 0,  # to be updated with actual company id during test execution
    "name": "route-2",
    "start_time": "09:00:00",
}
LANDMARK_3_IN_ROUTE_1 = {
    "route_id": 0,  # to be updated with actual route id during test execution
    "landmark_id": 0,  # to be updated with actual landmark id during test execution
    "distance_from_start": 2000,
    "arrival_delta": 3,
    "departure_delta": 4,
}


# Utility function to generate a random image for testing purposes
def generate_test_image(height=256, width=256):
    img_array = np.random.randint(0, 4, (height, width, 3), dtype=np.uint8)
    img = Image.fromarray(img_array, "RGB")

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return {"file": ("exec_test.png", buffer, "image/png")}
