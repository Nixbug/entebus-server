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


def functionality_test(target_url: str):
    print("BASIC FUNCTIONALITY TESTS STARTED")

    print("CASE 01: Login with valid admin credentials")
    response = requests.post(target_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    assert response.status_code == 200
    admin_token = TokenHolder(**response.json())

    print("CASE 02: Fetch all tokens")
    # At least one token should be present for the admin user (the one we just created)
    response = requests.get(target_url, headers=admin_token.HEADER())
    assert response.status_code == 200
    assert len(response.json()) >= 1

    print("CASE 03: Refresh token")
    response = requests.post(
        f"{target_url}/refresh",
        data={
            "refresh_token": admin_token.refresh_token,
            "grant_type": GrantType.REFRESH_TOKEN,
        },
    )
    assert response.status_code == 200
    admin_token = TokenHolder(**response.json())

    print("CASE 04: Revoking token")
    response = requests.post(
        f"{target_url}/revoke",
        headers=admin_token.HEADER(),
        data={"token": admin_token.access_token},
    )
    assert response.status_code == 200

    print("CASE 05: Delete token by id")
    response = requests.post(target_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    admin_token = TokenHolder(**response.json())
    response = requests.delete(
        f"{target_url}/{admin_token.id}", headers=admin_token.HEADER()
    )
    assert response.status_code == 204


def performance_test(target_url: str):
    print("PERFORMANCE TESTS STARTED")

    print("CASE 01: Token rotation testing")
    response = requests.post(target_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    admin_token = TokenHolder(**response.json())

    # Generate the maximum allowed tokens for the admin user
    for _ in range(MAX_EXECUTIVE_TOKENS):
        response = requests.post(target_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
        assert response.status_code == 200

    # The first token should have been revoked due to token rotation policy
    response = requests.get(target_url, headers=admin_token.HEADER())
    assert response.status_code == 401


def security_test(target_url: str):
    print("SECURITY TESTS STARTED")

    print("CASE 01: Login with invalid credentials")
    response = requests.post(
        target_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_credentials"]
    )
    assert response.status_code == 401

    print("CASE 02: Login with missing required fields")
    response = requests.post(target_url, data={})
    assert response.status_code == 422

    print("CASE 03: Login with wrong grant_type")
    response = requests.post(
        target_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_grant_type"]
    )
    assert response.status_code == 422

    print("CASE 04: Login with invalid platform_type")
    response = requests.post(
        target_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_platform_type"]
    )
    assert response.status_code == 422

    print("CASE 05: Login with empty credentials")
    response = requests.post(
        target_url, data=INVALID_EXECUTIVE_CREDENTIALS["empty_credentials"]
    )
    assert response.status_code == 401

    print("CASE 06: Fetch tokens without Authorization header")
    response = requests.get(target_url)
    assert response.status_code == 401

    print("CASE 07: Revoke token without Authorization header")
    response = requests.post(f"{target_url}/revoke")
    assert response.status_code == 401

    print("CASE 08: Authorization with invalid bearer token")
    response = requests.get(
        target_url, headers={"Authorization": "Bearer InvalidToken"}
    )
    assert response.status_code == 401

    print("CASE 09: Refresh token with wrong grant_type")
    response = requests.post(
        f"{target_url}/refresh",
        data={
            "refresh_token": "SomeRefreshToken",
            "grant_type": GrantType.PASSWORD,
        },
    )
    assert response.status_code == 406

    print("CASE 10: Refresh token with invalid refresh token")
    response = requests.post(
        f"{target_url}/refresh",
        data={
            "grant_type": GrantType.REFRESH_TOKEN,
            "refresh_token": "InvalidRefreshToken",
        },
    )
    assert response.status_code == 401

    print("CASE 11: Revoke own token with access token")
    response = requests.post(target_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    admin_token = TokenHolder(**response.json())
    response = requests.post(
        f"{target_url}/revoke",
        headers=admin_token.HEADER(),
        data={"token": admin_token.access_token},
    )
    assert response.status_code == 200

    print("CASE 12: Refresh token with already revoked refresh token")
    response = requests.post(
        f"{target_url}/refresh",
        data={
            "grant_type": GrantType.REFRESH_TOKEN,
            "refresh_token": admin_token.refresh_token,
        },
    )
    assert response.status_code == 401


def permission_test(target_url: str):
    print("PERMISSION TESTS STARTED")

    print("CASE 01: Guest attempts to fetch admin token details")
    response = requests.post(target_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    admin_token = TokenHolder(**response.json())
    response = requests.post(target_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    guest_token = TokenHolder(**response.json())
    response = requests.get(
        target_url,
        headers=guest_token.HEADER(),
        params={"executive_id": admin_token.executive_id},
    )
    assert response.status_code == 403

    print("CASE 02: Guest attempts to delete admin token via id")
    response = requests.delete(
        f"{target_url}/{admin_token.id}", headers=guest_token.HEADER()
    )
    assert response.status_code == 403

    print("CASE 03: Guest tries to revoke admin access token")
    response = requests.post(
        f"{target_url}/revoke",
        headers=guest_token.HEADER(),
        data={"token": admin_token.access_token},
    )
    assert response.status_code == 200

    # REVOKE returns 200 but token is not revoked, so GET still returns 200.
    response = requests.get(target_url, headers=admin_token.HEADER())
    assert response.status_code == 200


def run_endpoint_test(base_url: str):
    target_url = f"{base_url}/executive{URL_EXECUTIVE_TOKEN}"
    print("Running tests for Executive Token Endpoint: ", target_url)

    functionality_test(target_url)
    performance_test(target_url)
    security_test(target_url)
    permission_test(target_url)
