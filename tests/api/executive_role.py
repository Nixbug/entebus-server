"""
Tests for the executive role endpoint.
"""

import requests
import uuid

from datetime import datetime
from app.src.urls import URL_EXECUTIVE_ROLE
from tests.src.inputs import VALID_EXECUTIVE_CREDENTIALS
from tests.src.schemas import TokenHolder
from tests.src.inputs import GUEST_PERMISSIONS, ADMIN_PERMISSIONS, PARTIAL_PERMISSIONS


def generate_unique_name(base_name: str) -> str:
    """Generate a unique name by appending a timestamp and short UUID."""
    timestamp = datetime.now().strftime("%s")
    unique_id = str(uuid.uuid4())[:8]
    suffix = f"_{timestamp[-4:]}_{unique_id}"
    max_base_length = 32 - len(suffix)
    truncated_base = base_name[:max_base_length]
    return f"{truncated_base}{suffix}"


def run_endpoint_test(base_url: str):
    print("\n=== EXECUTIVE ROLE TESTS STARTED ===")

    role_url = f"{base_url}/executive{URL_EXECUTIVE_ROLE}"
    token_url = f"{base_url}/executive/entebus/account/token"

    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    assert response.status_code == 200
    admin = TokenHolder(**response.json())

    role_name_01 = generate_unique_name("TestRole")
    role_name_admin = generate_unique_name("Admin")
    role_name_limited = generate_unique_name("Limited")
    role_name_updated = generate_unique_name("Updated")
    role_name_modified = generate_unique_name("Modified")
    role_name_guest = generate_unique_name("Guest")

    # ---------------------------------------------------------------------------
    print("CASE 01: Create role without Authorization header")
    response = requests.post(
        role_url, json={"name": "TestRole", "permissions": GUEST_PERMISSIONS}
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 02: Create role with invalid bearer token")
    response = requests.post(
        role_url,
        headers={"Authorization": "Bearer InvalidToken"},
        json={"name": "TestRole", "permissions": GUEST_PERMISSIONS},
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 03: Create role with missing required fields (name)")
    response = requests.post(
        role_url, headers=admin.HEADER(), json={"permissions": GUEST_PERMISSIONS}
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 04: Create role with missing required fields (permissions)")
    response = requests.post(
        role_url, headers=admin.HEADER(), json={"name": role_name_01}
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 05: Create role with empty name")
    response = requests.post(
        role_url,
        headers=admin.HEADER(),
        json={"name": "", "permissions": GUEST_PERMISSIONS},
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 06: Create role with name exceeding max length (>32 chars)")
    response = requests.post(
        role_url,
        headers=admin.HEADER(),
        json={"name": "A" * 33, "permissions": GUEST_PERMISSIONS},
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 07: Create role with invalid name pattern (special chars)")
    response = requests.post(
        role_url,
        headers=admin.HEADER(),
        json={"name": "Role@#$%", "permissions": GUEST_PERMISSIONS},
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 08: Create valid role with default permissions")
    response = requests.post(
        role_url,
        headers=admin.HEADER(),
        json={"name": role_name_01, "permissions": GUEST_PERMISSIONS},
    )
    assert response.status_code == 201
    role_01 = response.json()
    assert role_01["name"] == role_name_01
    assert role_01["permissions"] == GUEST_PERMISSIONS
    assert "id" in role_01
    assert "created_on" in role_01
    assert role_01["updated_on"] is None

    # ---------------------------------------------------------------------------
    print("CASE 09: Create duplicate role (name)")
    response = requests.post(
        role_url,
        headers=admin.HEADER(),
        json={"name": role_name_01, "permissions": ADMIN_PERMISSIONS},
    )
    assert response.status_code == 409

    # ---------------------------------------------------------------------------
    print("CASE 10: Create role with full permissions")
    response = requests.post(
        role_url,
        headers=admin.HEADER(),
        json={"name": role_name_admin, "permissions": ADMIN_PERMISSIONS},
    )
    assert response.status_code == 201
    admin_role = response.json()
    assert admin_role["name"] == role_name_admin
    assert admin_role["permissions"] == ADMIN_PERMISSIONS

    # ---------------------------------------------------------------------------
    print("CASE 11: Create role with partial permissions")
    response = requests.post(
        role_url,
        headers=admin.HEADER(),
        json={"name": role_name_limited, "permissions": PARTIAL_PERMISSIONS},
    )
    assert response.status_code == 201
    limited_role = response.json()
    assert limited_role["name"] == role_name_limited

    # ---------------------------------------------------------------------------
    print("CASE 12: Fetch all roles without Authorization header")
    response = requests.get(role_url)
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 13: Fetch all roles with invalid bearer token")
    response = requests.get(role_url, headers={"Authorization": "Bearer InvalidToken"})
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 14: Fetch all roles successfully")
    response = requests.get(role_url, headers=admin.HEADER())
    assert response.status_code == 200
    roles = response.json()
    assert isinstance(roles, list)
    assert len(roles) >= 3  # At least the 3 roles we created

    # ---------------------------------------------------------------------------
    print("CASE 15: Fetch roles with pagination (limit and offset)")
    response = requests.get(
        role_url, headers=admin.HEADER(), params={"limit": 2, "offset": 0}
    )
    assert response.status_code == 200
    paginated_roles = response.json()
    assert len(paginated_roles) <= 2

    # ---------------------------------------------------------------------------
    print("CASE 16: Fetch roles with limit and valid offset")
    response = requests.get(
        role_url, headers=admin.HEADER(), params={"limit": 2, "offset": 1}
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 17: Fetch roles ordered by created_on ascending")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"order_by": "created_on", "order_in": "asc"},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 18: Fetch roles ordered by created_on descending")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"order_by": "created_on", "order_in": "desc"},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 19: Fetch roles ordered by updated_on")
    response = requests.get(
        role_url, headers=admin.HEADER(), params={"order_by": "updated_on"}
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 20: Fetch roles filtered by id")
    response = requests.get(
        role_url, headers=admin.HEADER(), params={"id": role_01["id"]}
    )
    assert response.status_code == 200
    filtered_roles = response.json()
    assert len(filtered_roles) == 1
    assert filtered_roles[0]["id"] == role_01["id"]

    # ---------------------------------------------------------------------------
    print("CASE 21: Fetch roles filtered by non-existent id")
    response = requests.get(role_url, headers=admin.HEADER(), params={"id": 99999})
    assert response.status_code == 200
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 22: Fetch roles filtered by name")
    response = requests.get(
        role_url, headers=admin.HEADER(), params={"name": role_name_admin}
    )
    assert response.status_code == 200
    filtered_roles = response.json()
    assert len(filtered_roles) >= 1
    assert any(r["name"] == role_name_admin for r in filtered_roles)

    # ---------------------------------------------------------------------------
    print("CASE 23: Fetch roles filtered by partial name")
    response = requests.get(role_url, headers=admin.HEADER(), params={"name": "Role"})
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 24: Update role without Authorization header")
    response = requests.patch(
        f"{role_url}/{role_01['id']}", json={"name": role_name_updated}
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 25: Update role with invalid bearer token")
    response = requests.patch(
        f"{role_url}/{role_01['id']}",
        headers={"Authorization": "Bearer InvalidToken"},
        json={"name": role_name_updated},
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 26: Update non-existent role")
    response = requests.patch(
        f"{role_url}/99999", headers=admin.HEADER(), json={"name": "NonExistent"}
    )
    assert response.status_code == 404

    # ---------------------------------------------------------------------------
    print("CASE 27: Update role with empty name")
    response = requests.patch(
        f"{role_url}/{role_01['id']}", headers=admin.HEADER(), json={"name": ""}
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 28: Update role with name exceeding max length")
    response = requests.patch(
        f"{role_url}/{role_01['id']}", headers=admin.HEADER(), json={"name": "A" * 33}
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 29: Update role with invalid name pattern")
    response = requests.patch(
        f"{role_url}/{role_01['id']}", headers=admin.HEADER(), json={"name": "Role@#$%"}
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 30: Update role with new valid name")
    response = requests.patch(
        f"{role_url}/{role_01['id']}",
        headers=admin.HEADER(),
        json={"name": role_name_updated},
    )
    assert response.status_code == 200
    updated_role = response.json()
    assert updated_role["id"] == role_01["id"]
    assert updated_role["name"] == role_name_updated
    assert updated_role["updated_on"] is not None

    # ---------------------------------------------------------------------------
    print("CASE 31: Update role permissions only")
    response = requests.patch(
        f"{role_url}/{role_01['id']}",
        headers=admin.HEADER(),
        json={"permissions": ADMIN_PERMISSIONS},
    )
    assert response.status_code == 200
    updated_role = response.json()
    assert updated_role["permissions"] == ADMIN_PERMISSIONS

    # ---------------------------------------------------------------------------
    print("CASE 32: Update role with both name and permissions")
    response = requests.patch(
        f"{role_url}/{role_01['id']}",
        headers=admin.HEADER(),
        json={"name": role_name_modified, "permissions": PARTIAL_PERMISSIONS},
    )
    assert response.status_code == 200
    updated_role = response.json()
    assert updated_role["name"] == role_name_modified
    assert updated_role["permissions"] == PARTIAL_PERMISSIONS

    # ---------------------------------------------------------------------------
    print("CASE 33: Empty PATCH request (no updates)")
    response = requests.patch(
        f"{role_url}/{role_01['id']}", headers=admin.HEADER(), json={}
    )
    assert response.status_code == 200
    unchanged_role = response.json()
    assert unchanged_role["id"] == role_01["id"]

    # ---------------------------------------------------------------------------
    print("CASE 34: Delete role without Authorization header")
    response = requests.delete(f"{role_url}/{role_01['id']}")
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 35: Delete role with invalid bearer token")
    response = requests.delete(
        f"{role_url}/{role_01['id']}", headers={"Authorization": "Bearer InvalidToken"}
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 36: Delete non-existent role")
    response = requests.delete(f"{role_url}/99999", headers=admin.HEADER())
    assert response.status_code == 204

    # ---------------------------------------------------------------------------
    print("CASE 37: Delete existing role successfully")
    response = requests.delete(f"{role_url}/{admin_role['id']}", headers=admin.HEADER())
    assert response.status_code == 204

    # Verify role was deleted
    response = requests.get(
        role_url, headers=admin.HEADER(), params={"id": admin_role["id"]}
    )
    assert response.status_code == 200
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 38: Delete another existing role")
    response = requests.delete(
        f"{role_url}/{limited_role['id']}", headers=admin.HEADER()
    )
    assert response.status_code == 204

    # ---------------------------------------------------------------------------
    print("CASE 39: Guest user fetches all roles")
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    assert response.status_code == 200
    guest = TokenHolder(**response.json())

    response = requests.get(role_url, headers=guest.HEADER())
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 40: Guest attempts to create role without proper permissions")
    response = requests.post(
        role_url,
        headers=guest.HEADER(),
        json={"name": role_name_guest, "permissions": GUEST_PERMISSIONS},
    )
    assert response.status_code == 403

    print("=== EXECUTIVE ROLE TESTS COMPLETED ===\n")
