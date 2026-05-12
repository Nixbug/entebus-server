import requests

from app.api.company import CompanySchema
from app.api.executive_account import ExecutiveSchema
from app.api.executive_role import ExecutiveRoleSchema
from app.api.executive_role_map import ExecutiveRoleMapSchema
from app.api.executive_token import ExecutiveTokenSchema
from app.api.landmark import LandmarkSchema
from app.api.landmark_in_route import LandmarkInRouteSchema
from app.api.bus_stop import BusStopSchema
from app.api.operator_role import OperatorRoleSchema
from app.api.operator_role_map import OperatorRoleMapSchema
from app.api.vehicle import VehicleSchema
from app.api.vehicle_image import VehicleImageSchema
from app.src.urls import (
    URL_COMPANY,
    URL_LANDMARK_IN_ROUTE,
    URL_OPERATOR_ACCOUNT,
    URL_OPERATOR_PICTURE,
    URL_EXECUTIVE_PICTURE,
    URL_EXECUTIVE_ACCOUNT,
    URL_EXECUTIVE_ROLE,
    URL_OPERATOR_ROLE,
    URL_OPERATOR_ROLE_MAP,
    URL_FARE,
    URL_EXECUTIVE_ROLE_MAP,
    URL_VEHICLE,
    URL_VEHICLE_PICTURE,
    URL_EXECUTIVE_TOKEN,
    URL_LANDMARK,
    URL_BUS_STOP,
    URL_ROUTE,
)
from tests.inputs import (
    BUS_STOP_IN_LANDMARK_1,
    COMPANY_1,
    COMPANY_2,
    EX_GUEST_CREDENTIALS,
    FARE_2,
    LANDMARK_1_IN_ROUTE_1,
    LANDMARK_2,
    LANDMARK_2_IN_ROUTE_1,
    LANDMARK_3,
    LANDMARK_3_IN_ROUTE_1,
    OP_ACCOUNT_1,
    EX_ACCOUNT_1,
    EX_ACCOUNT_2,
    EX_ADMIN_CREDENTIALS,
    EX_ADMIN_ROLE,
    LANDMARK_1,
    OP_ACCOUNT_2,
    OP_TEST_ROLE,
    ROUTE_2,
    VEHICLE_2,
    generate_test_image,
    OP_ADMIN_ROLE,
    OP_GUEST_ROLE,
    VEHICLE_1,
    FARE_1,
    ROUTE_1,
)
from app.api.fare import FareSchema
from app.api.route import RouteSchema
from app.api.operator_account import OperatorSchema
from app.api.executive_image import ExecutiveImageSchema
from app.api.operator_image import OperatorImageSchema


def test_executive_token_flow(token_url: str, credentials: dict):
    print(f"Requesting token")
    response = requests.post(token_url, data=credentials)
    assert response.status_code == 200
    admin_token = ExecutiveTokenSchema.model_validate(response.json())
    admin_headers = {"Authorization": f"Bearer {admin_token.access_token}"}

    print("Fetching the token using ID")
    response = requests.get(f"{token_url}?id={admin_token.id}", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Refreshing the token")
    refresh_payload = {
        "refresh_token": admin_token.refresh_token,
        "grant_type": "refresh_token",
    }
    response = requests.post(
        f"{token_url}/refresh", headers=admin_headers, data=refresh_payload
    )
    assert response.status_code == 200
    admin_token = ExecutiveTokenSchema.model_validate(response.json())
    admin_headers = {"Authorization": f"Bearer {admin_token.access_token}"}

    print("Revoking the token")
    response = requests.post(
        f"{token_url}/revoke",
        headers=admin_headers,
        data={"token": admin_token.access_token},
    )
    assert response.status_code == 200

    print("Deleting the token")
    response = requests.delete(f"{token_url}/{admin_token.id}", headers=admin_headers)
    assert response.status_code == 401


def test_executive_image_flow(picture_url: str, token_headers: dict):
    print("Uploading executive image")
    files = generate_test_image()
    response = requests.post(picture_url, headers=token_headers, files=files)
    assert response.status_code == 201
    img_meta = ExecutiveImageSchema.model_validate(response.json())

    print("Fetching executive image list")
    response = requests.get(
        f"{picture_url}?executive_id={img_meta.executive_id}", headers=token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) >= 1

    print("Downloading the uploaded image")
    response = requests.get(f"{picture_url}/{img_meta.id}", headers=token_headers)
    assert response.status_code == 200
    assert int(response.headers.get("Content-Length", len(response.content))) > 0

    print("Deleting the image")
    response = requests.delete(f"{picture_url}/{img_meta.id}", headers=token_headers)
    assert response.status_code == 204


def test_executive_role_flow(role_url: str, role_data: dict, token_headers: dict):
    print("Creating role")
    response = requests.post(role_url, headers=token_headers, json=role_data)
    assert response.status_code == 201
    role = ExecutiveRoleSchema.model_validate(response.json())

    print("Fetching role by id")
    response = requests.get(f"{role_url}?id={role.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating role")
    update_payload = {"name": f"{role.name}-updated"}
    response = requests.patch(
        f"{role_url}/{role.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting role")
    response = requests.delete(f"{role_url}/{role.id}", headers=token_headers)
    assert response.status_code == 204


def test_executive_account_flow(
    account_url: str, account_data: dict, token_headers: dict
):
    print("Creating executive account")
    response = requests.post(account_url, headers=token_headers, json=account_data)
    assert response.status_code == 201
    account = ExecutiveSchema.model_validate(response.json())

    print("Fetching created account by id")
    response = requests.get(f"{account_url}?id={account.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Patching account info")
    patch_payload = {"full_name": f"{account.full_name} Updated"}
    response = requests.patch(
        f"{account_url}/{account.id}", headers=token_headers, json=patch_payload
    )
    assert response.status_code == 200

    print("Deleting account")
    response = requests.delete(f"{account_url}/{account.id}", headers=token_headers)
    assert response.status_code == 204


def test_executive_role_map_flow(
    role_map_url: str, role_url: str, account: ExecutiveSchema, token_headers: dict
):

    print("Fetching admin role")
    response = requests.get(f"{role_url}?name=admin", headers=token_headers)
    assert response.status_code == 200
    roles = response.json()
    assert isinstance(roles, list) and len(roles) == 1
    admin_role = roles[0]

    print("Fetching guest role")
    response = requests.get(f"{role_url}?name=guest", headers=token_headers)
    assert response.status_code == 200
    roles = response.json()
    assert isinstance(roles, list) and len(roles) == 1
    guest_role = roles[0]

    print("Creating role mapping")
    role_map_payload = {"role_id": admin_role["id"], "executive_id": account.id}
    response = requests.post(
        role_map_url,
        headers=token_headers,
        json=role_map_payload,
    )
    assert response.status_code == 201
    role_map = ExecutiveRoleMapSchema.model_validate(response.json())

    print("Updating role mapping to guest role")
    patch_payload = {"role_id": guest_role["id"]}
    response = requests.patch(
        f"{role_map_url}/{role_map.id}",
        headers=token_headers,
        json=patch_payload,
    )
    assert response.status_code == 200

    print("Deleting role mapping")
    response = requests.delete(
        f"{role_map_url}/{role_map.id}",
        headers=token_headers,
    )
    assert response.status_code == 204


def test_landmark_flow(landmark_url: str, landmark_data: dict, token_headers: dict):
    print("Creating landmark")
    response = requests.post(landmark_url, headers=token_headers, json=landmark_data)
    assert response.status_code == 201
    landmark = LandmarkSchema.model_validate(response.json())

    print("Fetching landmark by id")
    response = requests.get(f"{landmark_url}?id={landmark.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating landmark")
    update_payload = {"name": f"{landmark.name}-updated"}
    response = requests.patch(
        f"{landmark_url}/{landmark.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting landmark")
    response = requests.delete(f"{landmark_url}/{landmark.id}", headers=token_headers)
    assert response.status_code == 204


def test_bus_stop_flow(
    bus_stop_url: str,
    landmark: LandmarkSchema,
    bus_stop_data: dict,
    token_headers: dict,
):
    print("Creating bus stop")
    bus_stop_data["landmark_id"] = landmark.id
    response = requests.post(bus_stop_url, headers=token_headers, json=bus_stop_data)
    assert response.status_code == 201
    bus_stop = BusStopSchema.model_validate(response.json())

    print("Fetching bus stop by id")
    response = requests.get(f"{bus_stop_url}?id={bus_stop.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating bus stop location")
    update_payload = {"name": f"{bus_stop.name}-updated"}
    response = requests.patch(
        f"{bus_stop_url}/{bus_stop.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting bus stop")
    response = requests.delete(f"{bus_stop_url}/{bus_stop.id}", headers=token_headers)
    assert response.status_code == 204


def test_company_flow(company_url: str, company_data: dict, token_headers: dict):
    print("Creating company")
    response = requests.post(company_url, headers=token_headers, json=company_data)
    assert response.status_code == 201
    company = CompanySchema.model_validate(response.json())

    print("Fetching company by id")
    response = requests.get(f"{company_url}?id={company.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating company location")
    update_payload = {"location": "POINT(77.59466 12.97166)"}
    response = requests.patch(
        f"{company_url}/{company.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting company")
    response = requests.delete(f"{company_url}/{company.id}", headers=token_headers)
    assert response.status_code == 204


def test_operator_account_flow(
    operator_url: str, company: CompanySchema, operator_data: dict, token_headers: dict
):
    print("Creating operator")
    operator_data["company_id"] = company.id
    response = requests.post(operator_url, headers=token_headers, json=operator_data)
    assert response.status_code == 201
    operator = OperatorSchema.model_validate(response.json())

    print("Fetching operator by id")
    response = requests.get(f"{operator_url}?id={operator.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating operator")
    update_payload = {"full_name": f"{operator.full_name or 'Updated'} Updated"}
    response = requests.patch(
        f"{operator_url}/{operator.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting operator")
    response = requests.delete(f"{operator_url}/{operator.id}", headers=token_headers)
    assert response.status_code == 204


def test_operator_role_flow(
    role_url: str, company: CompanySchema, role_data: dict, token_headers: dict
):
    print("Creating operator role")
    role_data["company_id"] = company.id
    response = requests.post(role_url, headers=token_headers, json=role_data)
    assert response.status_code == 201
    role = OperatorRoleSchema.model_validate(response.json())

    print("Fetching operator role by id")
    response = requests.get(f"{role_url}?id={role.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating operator role")
    update_payload = {"name": f"{role.name}-updated"}
    response = requests.patch(
        f"{role_url}/{role.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting operator role")
    response = requests.delete(f"{role_url}/{role.id}", headers=token_headers)
    assert response.status_code == 204


def test_operator_role_map_flow(
    operator_role_map_url: str,
    operator: OperatorSchema,
    role_1: OperatorRoleSchema,
    role_2: OperatorRoleSchema,
    token_headers: dict,
):
    print("Creating role mapping")
    role_map_payload = {"role_id": role_1.id, "operator_id": operator.id}
    response = requests.post(
        operator_role_map_url,
        headers=token_headers,
        json=role_map_payload,
    )
    assert response.status_code == 201
    role_map = OperatorRoleMapSchema.model_validate(response.json())

    print("Fetching operator role mapping")
    response = requests.get(
        f"{operator_role_map_url}?id={role_map.id}", headers=token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating role mapping to guest role")
    patch_payload = {"role_id": role_2.id}
    response = requests.patch(
        f"{operator_role_map_url}/{role_map.id}",
        headers=token_headers,
        json=patch_payload,
    )
    assert response.status_code == 200

    print("Deleting role mapping")
    response = requests.delete(
        f"{operator_role_map_url}/{role_map.id}", headers=token_headers
    )
    assert response.status_code == 204


def test_operator_image_flow(
    picture_url: str,
    operator: OperatorSchema,
    company: CompanySchema,
    token_headers: dict,
):
    print("Uploading operator image")
    files = generate_test_image()
    data = {"company_id": str(company.id), "operator_id": str(operator.id)}
    response = requests.post(picture_url, headers=token_headers, files=files, data=data)
    assert response.status_code == 201
    img_meta = OperatorImageSchema.model_validate(response.json())

    print("Fetching operator image list")
    response = requests.get(
        f"{picture_url}?operator_id={operator.id}", headers=token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) >= 1

    print("Downloading the uploaded operator image")
    response = requests.get(f"{picture_url}/{img_meta.id}", headers=token_headers)
    assert response.status_code == 200
    assert int(response.headers.get("Content-Length", len(response.content))) > 0

    print("Deleting the operator image")
    response = requests.delete(f"{picture_url}/{img_meta.id}", headers=token_headers)
    assert response.status_code == 204


def test_fare_flow(
    fare_url: str, company: CompanySchema, fare_data: dict, token_headers: dict
):
    print("Creating fare")
    fare_data["company_id"] = company.id
    response = requests.post(fare_url, headers=token_headers, json=fare_data)
    assert response.status_code == 201
    fare = FareSchema.model_validate(response.json())

    print("Fetching fare by id")
    response = requests.get(f"{fare_url}?id={fare.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating fare")
    update_payload = {"name": f"{fare.name}-updated"}
    response = requests.patch(
        f"{fare_url}/{fare.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting fare")
    response = requests.delete(f"{fare_url}/{fare.id}", headers=token_headers)
    assert response.status_code == 204


def test_vehicle_flow(
    vehicle_url: str, company: CompanySchema, vehicle_data: dict, token_headers: dict
):
    print("Creating vehicle")
    vehicle_data["company_id"] = company.id
    response = requests.post(vehicle_url, headers=token_headers, json=vehicle_data)
    assert response.status_code == 201
    vehicle = VehicleSchema.model_validate(response.json())

    print("Fetching vehicle by id")
    response = requests.get(f"{vehicle_url}?id={vehicle.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating vehicle")
    update_payload = {"name": f"{vehicle.name}-Updated"}
    response = requests.patch(
        f"{vehicle_url}/{vehicle.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting vehicle")
    response = requests.delete(f"{vehicle_url}/{vehicle.id}", headers=token_headers)
    assert response.status_code == 204


def test_vehicle_image_flow(
    picture_url: str,
    company: CompanySchema,
    vehicle: VehicleSchema,
    token_headers: dict,
):
    print("Uploading vehicle image")
    files = generate_test_image()
    data = {"company_id": str(company.id), "vehicle_id": str(vehicle.id)}
    response = requests.post(picture_url, headers=token_headers, files=files, data=data)
    assert response.status_code == 201
    img_meta = VehicleImageSchema.model_validate(response.json())

    print("Fetching vehicle image list")
    response = requests.get(
        f"{picture_url}?vehicle_id={vehicle.id}", headers=token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) >= 1

    print("Downloading the uploaded image")
    response = requests.get(f"{picture_url}/{img_meta.id}", headers=token_headers)
    assert response.status_code == 200
    assert int(response.headers.get("Content-Length", len(response.content))) > 0

    print("Deleting the vehicle image")
    response = requests.delete(f"{picture_url}/{img_meta.id}", headers=token_headers)
    assert response.status_code == 204


def test_route_flow(
    route_url: str, company: CompanySchema, route_data: dict, token_headers: dict
):
    print("Creating route")
    route_data["company_id"] = company.id
    response = requests.post(route_url, headers=token_headers, json=route_data)
    assert response.status_code == 201
    route = RouteSchema.model_validate(response.json())

    print("Fetching route by id")
    response = requests.get(f"{route_url}?id={route.id}", headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 1

    print("Updating route")
    update_payload = {"name": f"{route.name}-updated"}
    response = requests.patch(
        f"{route_url}/{route.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting route")
    response = requests.delete(f"{route_url}/{route.id}", headers=token_headers)
    assert response.status_code == 204


def test_landmark_in_route_flow(
    landmark_in_route_url: str,
    route: RouteSchema,
    landmark_payload: dict,
    token_headers: dict,
):
    # use provided payload template and set the route id
    payload = dict(landmark_payload)
    payload["route_id"] = route.id

    print("Creating landmark in route")
    response = requests.post(landmark_in_route_url, headers=token_headers, json=payload)
    assert response.status_code == 201
    lir = LandmarkInRouteSchema.model_validate(response.json())

    print("Fetching landmarks in route")
    response = requests.get(
        f"{landmark_in_route_url}?route_id={route.id}", headers=token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) >= 1

    print("Updating landmark in route (increase distance)")
    update_payload = {"distance_from_start": (lir.distance_from_start or 0) + 10}
    response = requests.patch(
        f"{landmark_in_route_url}/{lir.id}", headers=token_headers, json=update_payload
    )
    assert response.status_code == 200

    print("Deleting landmark in route")
    response = requests.delete(
        f"{landmark_in_route_url}/{lir.id}", headers=token_headers
    )
    assert response.status_code == 204


def run_test(target_url):
    ACCOUNT_URL = f"{target_url}/executive{URL_EXECUTIVE_ACCOUNT}"
    ROLE_URL = f"{target_url}/executive{URL_EXECUTIVE_ROLE}"
    TOKEN_URL = f"{target_url}/executive{URL_EXECUTIVE_TOKEN}"
    ROLE_MAP_URL = f"{target_url}/executive{URL_EXECUTIVE_ROLE_MAP}"
    LANDMARK_URL = f"{target_url}/executive{URL_LANDMARK}"
    BUS_STOP_URL = f"{target_url}/executive{URL_BUS_STOP}"
    COMPANY_URL = f"{target_url}/executive{URL_COMPANY}"
    OPERATOR_URL = f"{target_url}/executive{URL_OPERATOR_ACCOUNT}"
    OPERATOR_ROLE_URL = f"{target_url}/executive{URL_OPERATOR_ROLE}"
    OPERATOR_ROLE_MAP_URL = f"{target_url}/executive{URL_OPERATOR_ROLE_MAP}"
    PICTURE_URL = f"{target_url}/executive{URL_EXECUTIVE_PICTURE}"
    OPERATOR_PICTURE_URL = f"{target_url}/executive{URL_OPERATOR_PICTURE}"
    VEHICLE_URL = f"{target_url}/executive{URL_VEHICLE}"
    VEHICLE_PICTURE_URL = f"{target_url}/executive{URL_VEHICLE_PICTURE}"
    FARE_URL = f"{target_url}/executive{URL_FARE}"
    ROUTE_URL = f"{target_url}/executive{URL_ROUTE}"
    LANDMARK_IN_ROUTE_URL = f"{target_url}/executive{URL_LANDMARK_IN_ROUTE}"
    print("Testing happy flow for executive")

    ## Creating primary resources for tests
    ## Authentication token
    print("Creating authentication token")
    response = requests.post(TOKEN_URL, data=EX_ADMIN_CREDENTIALS)
    assert response.status_code == 200
    admin_token = ExecutiveTokenSchema.model_validate(response.json())
    admin_headers = {"Authorization": f"Bearer {admin_token.access_token}"}

    # Executive
    print("Creating executive account")
    response = requests.post(ACCOUNT_URL, headers=admin_headers, json=EX_ACCOUNT_1)
    assert response.status_code == 201
    account = ExecutiveSchema.model_validate(response.json())

    # Company
    print("Creating company")
    response = requests.post(COMPANY_URL, headers=admin_headers, json=COMPANY_1)
    assert response.status_code == 201
    company = CompanySchema.model_validate(response.json())

    # Operator account
    print("Creating operator")
    OP_ACCOUNT_1["company_id"] = company.id
    response = requests.post(OPERATOR_URL, headers=admin_headers, json=OP_ACCOUNT_1)
    assert response.status_code == 201
    operator = OperatorSchema.model_validate(response.json())

    # Operator admin role
    print("Creating operator admin role")
    OP_ADMIN_ROLE["company_id"] = company.id
    response = requests.post(
        OPERATOR_ROLE_URL, headers=admin_headers, json=OP_ADMIN_ROLE
    )
    assert response.status_code == 201
    op_admin_role = OperatorRoleSchema.model_validate(response.json())

    # Operator guest role
    print("Creating operator guest role")
    OP_GUEST_ROLE["company_id"] = company.id
    response = requests.post(
        OPERATOR_ROLE_URL, headers=admin_headers, json=OP_GUEST_ROLE
    )
    assert response.status_code == 201
    op_guest_role = OperatorRoleSchema.model_validate(response.json())

    # Vehicle
    print("Creating vehicle")
    VEHICLE_1["company_id"] = company.id
    response = requests.post(VEHICLE_URL, headers=admin_headers, json=VEHICLE_1)
    assert response.status_code == 201
    vehicle = VehicleSchema.model_validate(response.json())

    # Landmark 1
    print("Creating landmark 1")
    response = requests.post(LANDMARK_URL, headers=admin_headers, json=LANDMARK_1)
    assert response.status_code == 201
    landmark_1 = LandmarkSchema.model_validate(response.json())

    # Landmark 2
    print("Creating landmark 2")
    response = requests.post(LANDMARK_URL, headers=admin_headers, json=LANDMARK_2)
    assert response.status_code == 201
    landmark_2 = LandmarkSchema.model_validate(response.json())

    # Route 1
    print("Creating route")
    ROUTE_1["company_id"] = company.id
    response = requests.post(ROUTE_URL, headers=admin_headers, json=ROUTE_1)
    assert response.status_code == 201
    route = RouteSchema.model_validate(response.json())

    # Landmark in Route 1
    print("Adding landmark 1 to route")
    LANDMARK_1_IN_ROUTE_1["route_id"] = route.id
    LANDMARK_1_IN_ROUTE_1["landmark_id"] = landmark_1.id
    response = requests.post(
        LANDMARK_IN_ROUTE_URL, headers=admin_headers, json=LANDMARK_1_IN_ROUTE_1
    )
    assert response.status_code == 201
    landmark_1_in_route_1 = LandmarkInRouteSchema.model_validate(response.json())

    # Landmark 2 in Route 1
    print("Adding landmark 2 to route")
    LANDMARK_2_IN_ROUTE_1["route_id"] = route.id
    LANDMARK_2_IN_ROUTE_1["landmark_id"] = landmark_2.id
    response = requests.post(
        LANDMARK_IN_ROUTE_URL, headers=admin_headers, json=LANDMARK_2_IN_ROUTE_1
    )
    assert response.status_code == 201
    landmark_2_in_route_1 = LandmarkInRouteSchema.model_validate(response.json())
    
    try:
        # Test executive token creation, retrieval, refreshing, revoking and deletion
        test_executive_token_flow(TOKEN_URL, EX_ADMIN_CREDENTIALS)
        # Test executive image upload, retrieval, download and deletion
        test_executive_image_flow(PICTURE_URL, admin_headers)
        # Test executive role creation, retrieval, updating and deletion
        test_executive_role_flow(ROLE_URL, EX_ADMIN_ROLE, admin_headers)
        # Test executive account creation, retrieval, updating and deletion
        test_executive_account_flow(ACCOUNT_URL, EX_ACCOUNT_2, admin_headers)
        # Test executive role mapping creation, updating and deletion
        test_executive_role_map_flow(ROLE_MAP_URL, ROLE_URL, account, admin_headers)

        # Test landmark creation, retrieval, updating and deletion
        test_landmark_flow(LANDMARK_URL, LANDMARK_3, admin_headers)
        # Test bus stop creation, retrieval, updating and deletion
        test_bus_stop_flow(
            BUS_STOP_URL, landmark_1, BUS_STOP_IN_LANDMARK_1, admin_headers
        )

        # Test company creation, retrieval, updating and deletion
        test_company_flow(COMPANY_URL, COMPANY_2, admin_headers)
        # Test operator creation, retrieval, updating and deletion
        test_operator_account_flow(OPERATOR_URL, company, OP_ACCOUNT_2, admin_headers)
        # Test operator role creation, retrieval, updating and deletion
        test_operator_role_flow(OPERATOR_ROLE_URL, company, OP_TEST_ROLE, admin_headers)
        # Test operator role map creation, updating and deletion
        test_operator_role_map_flow(
            OPERATOR_ROLE_MAP_URL, operator, op_admin_role, op_guest_role, admin_headers
        )
        # Test operator image upload, retrieval, download and deletion
        test_operator_image_flow(OPERATOR_PICTURE_URL, operator, company, admin_headers)

        # Test fare creation, retrieval, updating and deletion
        test_fare_flow(FARE_URL, company, FARE_2, admin_headers)
        # Test vehicle creation, retrieval, updating and deletion
        test_vehicle_flow(VEHICLE_URL, company, VEHICLE_2, admin_headers)
        # Test vehicle image upload, retrieval, download and deletion
        test_vehicle_image_flow(VEHICLE_PICTURE_URL, company, vehicle, admin_headers)
        # Test route creation, retrieval, updating and deletion
        test_route_flow(ROUTE_URL, company, ROUTE_2, admin_headers)
        # Test landmark in route creation, retrieval, updating and deletion
        test_landmark_in_route_flow(
            LANDMARK_IN_ROUTE_URL, route, LANDMARK_3_IN_ROUTE_1, admin_headers
        )
    except Exception as e:
        print(f"Error during test execution: {e}")

    ## Deleting the primary resources created for tests
    # Landmark 1
    print("Deleting route")
    response = requests.delete(f"{ROUTE_URL}/{route.id}", headers=admin_headers)
    assert response.status_code == 204

    print("Deleting landmark 1")
    response = requests.delete(f"{LANDMARK_URL}/{landmark_1.id}", headers=admin_headers)
    assert response.status_code == 204

    # Landmark 2
    print("Deleting landmark 2")
    response = requests.delete(f"{LANDMARK_URL}/{landmark_2.id}", headers=admin_headers)
    assert response.status_code == 204

    # Company
    print("Deleting company")
    response = requests.delete(f"{COMPANY_URL}/{company.id}", headers=admin_headers)
    assert response.status_code == 204

    # Executive account
    print("Deleting executive account")
    response = requests.delete(f"{ACCOUNT_URL}/{account.id}", headers=admin_headers)
    assert response.status_code == 204

    # Authentication token
    print("Deleting the token")
    response = requests.delete(f"{TOKEN_URL}/{admin_token.id}", headers=admin_headers)
    assert response.status_code == 204
