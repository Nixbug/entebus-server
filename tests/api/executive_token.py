"""
Tests for the executive token endpoint.
"""

import requests

from app.src.enums import GrantType
from app.src.urls import URL_EXECUTIVE_TOKEN
from app.src.constants import MAX_EXECUTIVE_TOKENS
from tests.src.inputs import (
    VALID_EXECUTIVE_CREDENTIALS,
    INVALID_EXECUTIVE_CREDENTIALS,
)
from tests.src.schemas import TokenHolder


def run_endpoint_test(base_url: str):
    print("\n=== EXECUTIVE TOKEN TESTS STARTED ===")

    token_url = f"{base_url}/executive{URL_EXECUTIVE_TOKEN}"

    # ---------------------------------------------------------------------------
    print("CASE 01: Login with invalid credentials")
    response = requests.post(
        token_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_credentials"]
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 02: Login with missing required fields")
    response = requests.post(token_url, data={})
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 03: Login with wrong grant_type")
    response = requests.post(
        token_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_grant_type"]
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 04: Login with invalid platform_type")
    response = requests.post(
        token_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_platform_type"]
    )
    assert response.status_code == 422

    # ---------------------------------------------------------------------------
    print("CASE 05: Login with empty credentials")
    response = requests.post(
        token_url, data=INVALID_EXECUTIVE_CREDENTIALS["empty_credentials"]
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 06: Generate access token with valid credentials")
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    assert response.status_code == 200
    admin = TokenHolder(**response.json())

    # ---------------------------------------------------------------------------
    print("CASE 07: Fetch tokens without Authorization header")
    response = requests.get(token_url)
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 08: Authorization with invalid bearer token")
    response = requests.get(token_url, headers={"Authorization": "Bearer InvalidToken"})
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 09: Fetch all tokens")
    response = requests.get(token_url, headers=admin.HEADER())
    assert response.status_code == 200
    assert len(response.json()) >= 1

    # ---------------------------------------------------------------------------
    print("CASE 10: Refresh token with wrong grant_type")
    response = requests.post(
        f"{token_url}/refresh",
        data={
            "refresh_token": admin.refresh_token,
            "grant_type": GrantType.PASSWORD,
        },
    )
    assert response.status_code == 406

    # ---------------------------------------------------------------------------
    print("CASE 11: Refresh token with invalid refresh token")
    response = requests.post(
        f"{token_url}/refresh",
        data={
            "grant_type": GrantType.REFRESH_TOKEN,
            "refresh_token": "InvalidRefreshToken",
        },
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 12: Refresh token with valid refresh token")
    response = requests.post(
        f"{token_url}/refresh",
        data={
            "refresh_token": admin.refresh_token,
            "grant_type": GrantType.REFRESH_TOKEN,
        },
    )
    assert response.status_code == 200
    admin = TokenHolder(**response.json())

    # ---------------------------------------------------------------------------
    print("CASE 13: Revoke token without Authorization header")
    response = requests.post(f"{token_url}/revoke")
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 14: Revoke own token using access token")
    response = requests.post(
        f"{token_url}/revoke",
        headers=admin.HEADER(),
        data={"token": admin.access_token},
    )
    assert response.status_code == 200

    # ---------------------------------------------------------------------------
    print("CASE 15: Refresh token with already revoked refresh token")
    response = requests.post(
        f"{token_url}/refresh",
        data={
            "grant_type": GrantType.REFRESH_TOKEN,
            "refresh_token": admin.refresh_token,
        },
    )
    assert response.status_code == 401

    # ---------------------------------------------------------------------------
    print("CASE 16: Login with guest credentials and revoke token")
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    assert response.status_code == 200
    guest_details = TokenHolder(**response.json())
    response = requests.post(
        f"{token_url}/revoke",
        headers=guest_details.HEADER(),
        data={"token": guest_details.refresh_token},
    )
    assert response.status_code == 200

    # login again
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    assert response.status_code == 200
    guest = TokenHolder(**response.json())

    response = requests.get(
        token_url, headers=guest.HEADER(), params={"id": guest_details.id}
    )
    assert response.status_code == 200
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 17: Admin fetches guest token details")
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    assert response.status_code == 200
    admin = TokenHolder(**response.json())

    response = requests.get(
        token_url, headers=admin.HEADER(), params={"executive_id": guest.executive_id}
    )
    assert response.status_code == 200
    assert response.json() != []

    # ---------------------------------------------------------------------------
    print("CASE 18: Admin fetches token with invalid id")
    response = requests.get(token_url, headers=admin.HEADER(), params={"id": 0})
    assert response.status_code == 200
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 19: Guest attempts to fetch admin token details")
    response = requests.get(
        token_url, headers=guest.HEADER(), params={"executive_id": admin.executive_id}
    )
    assert response.status_code == 200
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 20: Guest attempts to delete admin token via ID")
    response = requests.delete(f"{token_url}/{admin.id}", headers=guest.HEADER())
    assert response.status_code == 403

    # ---------------------------------------------------------------------------
    print("CASE 21: Admin deletes token using invalid ID")
    response = requests.delete(f"{token_url}/0", headers=admin.HEADER())
    assert response.status_code == 204

    # ---------------------------------------------------------------------------
    print("CASE 22: Admin tries to revoke guest using guest access token")
    response = requests.post(
        f"{token_url}/revoke",
        headers=admin.HEADER(),
        data={"token": guest.access_token},
    )
    assert response.status_code == 200

    response = requests.get(token_url, headers=admin.HEADER(), params={"id": guest.id})
    assert response.status_code == 200
    assert response.json() != []

    # ---------------------------------------------------------------------------
    print("CASE 23: Admin tries to revoke guest using guest refresh token")
    response = requests.post(
        f"{token_url}/revoke",
        headers=admin.HEADER(),
        data={"token": guest.refresh_token},
    )
    assert response.status_code == 200

    response = requests.get(token_url, headers=admin.HEADER(), params={"id": guest.id})
    assert response.status_code == 200
    assert response.json() != []

    # ---------------------------------------------------------------------------
    print("CASE 24: Check token rotation limit")
    last_guest = None
    for _ in range(MAX_EXECUTIVE_TOKENS + 1):
        response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
        assert response.status_code == 200
        last_guest = TokenHolder(**response.json())

    response = requests.get(
        token_url,
        headers=last_guest.HEADER(),
        params={"id": guest.id},
    )
    assert response.status_code == 200
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 25: Guest deletes another owned token via ID")
    response = requests.get(
        token_url,
        headers=last_guest.HEADER(),
        params={"executive_id": last_guest.executive_id},
    )
    assert response.status_code == 200
    token_list = response.json()
    token_id = token_list[-1]["id"]

    response = requests.delete(f"{token_url}/{token_id}", headers=last_guest.HEADER())
    assert response.status_code == 204

    response = requests.get(
        token_url, headers=last_guest.HEADER(), params={"id": token_id}
    )
    assert response.status_code == 200
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 26: Admin deletes guest tokens via IDs")
    response = requests.get(
        token_url, headers=admin.HEADER(), params={"executive_id": guest.executive_id}
    )
    for token in response.json():
        response = requests.delete(f"{token_url}/{token['id']}", headers=admin.HEADER())
        assert response.status_code == 204

    response = requests.get(
        token_url, headers=admin.HEADER(), params={"executive_id": guest.executive_id}
    )
    assert response.status_code == 200
    assert response.json() == []

    # ---------------------------------------------------------------------------
    print("CASE 27: Admin deletes own token using ID")
    response = requests.delete(f"{token_url}/{admin.id}", headers=admin.HEADER())
    assert response.status_code == 204

    print("=== EXECUTIVE TOKEN TESTS COMPLETED ===\n")
