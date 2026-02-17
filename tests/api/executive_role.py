"""
Tests for the executive role endpoint.
"""

import requests
import re
import time
import uuid

from app.src.urls import URL_EXECUTIVE_ROLE
from tests.src.inputs import VALID_EXECUTIVE_CREDENTIALS
from tests.src.schemas import TokenHolder
from tests.src.inputs import (
    GUEST_PERMISSIONS,
    ADMIN_PERMISSIONS,
    PARTIAL_PERMISSIONS,
)
from app.src.db import ExecutiveToken, SessionLocal


NAME_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9 _.-]*[A-Za-z0-9])?$"


def generate_unique_name(base_name: str, max_len: int = 64) -> str:
    """
    Generate a unique name based on base_name, ensuring it matches
    NAME_PATTERN.
    """
    sanitized = re.sub(r"[^A-Za-z0-9 _.\-]+", "_", base_name or "")
    suffix = f"_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    max_base_len = max_len - len(suffix)

    if max_base_len < 1:
        truncated_base = "n"
    else:
        truncated_base = sanitized[:max_base_len]

        while truncated_base and not truncated_base[-1].isalnum():
            truncated_base = truncated_base[:-1]

        while truncated_base and not truncated_base[0].isalnum():
            truncated_base = truncated_base[1:]

        if not truncated_base:
            truncated_base = "n"

    candidate = f"{truncated_base}{suffix}"

    if not re.match(NAME_PATTERN, candidate):
        candidate = f"n{suffix}"

    return candidate


# ---------------------------------------------------------------------------


def run_endpoint_test(base_url: str):
    print("\n=== EXECUTIVE ROLE TESTS STARTED ===")

    role_url = f"{base_url}/executive{URL_EXECUTIVE_ROLE}"
    token_url = f"{base_url}/executive/entebus/account/token"

    # ---------------------------------------------------------------------------
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    assert response.status_code == 200
    admin = TokenHolder(**response.json())

    # ---------------------------------------------------------------------------
    role_name_01 = generate_unique_name("TestRole")
    role_name_admin = generate_unique_name("Admin")
    role_name_limited = generate_unique_name("Limited")
    role_name_updated = generate_unique_name("Updated")
    role_name_modified = generate_unique_name("Modified")

    # ---------------------------------------------------------------------------
    print("CASE 01: Create valid role with default permissions")
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
    print("CASE 02: Create role with full permissions")
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
    print("CASE 03: Create role with partial permissions")
    response = requests.post(
        role_url,
        headers=admin.HEADER(),
        json={"name": role_name_limited, "permissions": PARTIAL_PERMISSIONS},
    )
    assert response.status_code == 201
    limited_role = response.json()
    assert limited_role["name"] == role_name_limited

    # ---------------------------------------------------------------------------
    print("CASE 04: Fetch all roles successfully")
    response = requests.get(role_url, headers=admin.HEADER())
    assert response.status_code == 200
    roles = response.json()
    assert isinstance(roles, list)
    assert len(roles) >= 3

    # ---------------------------------------------------------------------------
    print("CASE 05: Fetch roles with pagination (limit & offset)")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"limit": 2, "offset": 0},
    )
    assert response.status_code == 200
    assert len(response.json()) <= 2

    # ---------------------------------------------------------------------------
    print("CASE 06: Fetch roles with valid offset")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"limit": 2, "offset": 1},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 07: Fetch roles ordered by created_on ASC")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"order_by": "created_on", "order_in": "asc"},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 08: Fetch roles ordered by created_on DESC")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"order_by": "created_on", "order_in": "desc"},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 09: Fetch roles ordered by updated_on")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"order_by": "updated_on"},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 10: Fetch roles filtered by ID")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"id": role_01["id"]},
    )
    assert response.status_code == 200
    filtered_roles = response.json()
    assert len(filtered_roles) == 1
    assert filtered_roles[0]["id"] == role_01["id"]

    # ---------------------------------------------------------------------------
    print("CASE 11: Fetch roles filtered by exact name")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"name": role_name_admin},
    )
    assert response.status_code == 200
    filtered_roles = response.json()
    assert any(r["name"] == role_name_admin for r in filtered_roles)

    # ---------------------------------------------------------------------------
    print("CASE 12: Fetch roles filtered by partial name")
    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"name": "Role"},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 13: Update role name")
    response = requests.patch(
        f"{role_url}/{role_01['id']}",
        headers=admin.HEADER(),
        json={"name": role_name_updated},
    )
    assert response.status_code == 200
    updated_role = response.json()
    assert updated_role["name"] == role_name_updated
    assert updated_role["updated_on"] is not None

    # ---------------------------------------------------------------------------
    print("CASE 14: Update role permissions only")
    response = requests.patch(
        f"{role_url}/{role_01['id']}",
        headers=admin.HEADER(),
        json={"permissions": ADMIN_PERMISSIONS},
    )
    assert response.status_code == 200
    assert response.json()["permissions"] == ADMIN_PERMISSIONS

    # ---------------------------------------------------------------------------
    print("CASE 15: Update role name and permissions")
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
    print("CASE 16: Empty PATCH request")
    response = requests.patch(
        f"{role_url}/{role_01['id']}",
        headers=admin.HEADER(),
        json={},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 17: Delete admin role and verify")
    response = requests.delete(
        f"{role_url}/{admin_role['id']}",
        headers=admin.HEADER(),
    )
    assert response.status_code == 204

    response = requests.get(
        role_url,
        headers=admin.HEADER(),
        params={"id": admin_role["id"]},
    )
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 18: Delete limited role")
    response = requests.delete(
        f"{role_url}/{limited_role['id']}",
        headers=admin.HEADER(),
    )
    assert response.status_code == 204

    # ---------------------------------------------------------------------------
    print("CASE 19: Guest user fetches roles")
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    assert response.status_code == 200
    guest = TokenHolder(**response.json())

    response = requests.get(role_url, headers=guest.HEADER())
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CLEANUP: Delete remaining test role and tokens")
    response = requests.delete(
        f"{role_url}/{role_01['id']}",
        headers=admin.HEADER(),
    )
    assert response.status_code == 204

    session = SessionLocal()
    session.query(ExecutiveToken).delete()
    session.commit()
    session.close()

    print("=== EXECUTIVE ROLE TESTS COMPLETED ===\n")
