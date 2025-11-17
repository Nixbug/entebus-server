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


def run_endpoint_test(base_url: str):
    print("\n=== EXECUTIVE TOKEN TESTS STARTED ===")

    token_url = f"{base_url}/executive{URL_EXECUTIVE_TOKEN}"

    # -------------------------------------------------------------
    print("CASE 1: Generate access token with valid credentials")
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    assert response.status_code == 200
    ADMIN_REFRESH = response.json()["refresh_token"]

    # -------------------------------------------------------------
    print("CASE 2: Renew token using refresh token")
    response = requests.post(
        f"{token_url}/refresh",
        data={"grant_type": GrantType.REFRESH_TOKEN, "refresh_token": ADMIN_REFRESH},
    )
    assert response.status_code == 200
    ADMIN_ACCESS = response.json()["access_token"]
    ADMIN_HEADER = {"Authorization": f"Bearer {ADMIN_ACCESS}"}

    # -------------------------------------------------------------
    print("CASE 3: Fetch all tokens as admin")
    response = requests.get(token_url, headers=ADMIN_HEADER)
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 4: Revoke own token using access token")
    response = requests.post(
        f"{token_url}/revoke", headers=ADMIN_HEADER, data={"token": ADMIN_ACCESS}
    )
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 5: Login with invalid credentials")
    response = requests.post(
        token_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_credentials"]
    )
    assert response.status_code == 401

    # -------------------------------------------------------------
    print("CASE 6: Login with missing required fields")
    response = requests.post(token_url, data={})
    assert response.status_code == 422

    # -------------------------------------------------------------
    print("CASE 7: Login using wrong grant_type (refresh_token)")
    response = requests.post(
        token_url,
        data={
            "username": "admin",
            "password": "password",
            "grant_type": GrantType.REFRESH_TOKEN,
        },
    )
    assert response.status_code == 422

    # -------------------------------------------------------------
    print("CASE 8: Login with missing/empty grant_type")
    response = requests.post(
        token_url,
        data={
            "username": "admin",
            "password": "password",
        },
    )
    assert response.status_code == 406

    # -------------------------------------------------------------
    print("CASE 9: Login using invalid grant_type")
    response = requests.post(
        token_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_grant_type"]
    )
    assert response.status_code == 422

    # -------------------------------------------------------------
    print("CASE 10: Login using invalid platform_type")
    response = requests.post(
        token_url, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_platform_type"]
    )
    assert response.status_code == 422
    # -------------------------------------------------------------
    print("CASE 11: Refresh using wrong grant_type (password)")
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    assert response.status_code == 200, "Guest login should succeed"
    GUEST_ACCESS = response.json()["access_token"]
    GUEST_REFRESH = response.json()["refresh_token"]
    GUEST_HEADER = {"Authorization": f"Bearer {GUEST_ACCESS}"}
    GUEST_ID = response.json()["id"]
    response = requests.post(
        f"{token_url}/refresh",
        data={
            "refresh_token": GUEST_REFRESH,
            "grant_type": GrantType.PASSWORD,
        },
    )
    assert response.status_code == 406

    # -------------------------------------------------------------
    print("CASE 12: Guest revokes own token using access token")
    response = requests.delete(f"{token_url}/{GUEST_ID}", headers=GUEST_HEADER)
    assert response.status_code == 204

    # -------------------------------------------------------------
    print("CASE 13: Refresh using revoked refresh token")
    response = requests.post(
        f"{token_url}/refresh",
        data={"grant_type": GrantType.REFRESH_TOKEN, "refresh_token": GUEST_REFRESH},
    )
    assert response.status_code == 401

    # -------------------------------------------------------------
    print("CASE 14: Refresh using invalid refresh token")
    response = requests.post(
        f"{token_url}/refresh",
        data={"grant_type": GrantType.REFRESH_TOKEN, "refresh_token": "invalid"},
    )
    assert response.status_code == 401

    # -------------------------------------------------------------
    print("CASE 15: Guest token rotation (simulate multiple logins)")
    for _ in range(MAX_EXECUTIVE_TOKENS + 1):
        response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
        assert response.status_code == 200
    GUEST_ACCESS = response.json()["access_token"]
    GUEST_HEADER = {"Authorization": f"Bearer {GUEST_ACCESS}"}
    GUEST_EX_ID = response.json()["executive_id"]
    GUEST_ID = response.json()["id"]
    GUEST_REFRESH = response.json()["refresh_token"]
    response = requests.get(
        token_url, headers=GUEST_HEADER, params={"executive_id": GUEST_EX_ID}
    )
    assert response.status_code == 200
    tokens = response.json()
    assert (
        len(tokens) <= MAX_EXECUTIVE_TOKENS
    ), f"Expected ≤ {MAX_EXECUTIVE_TOKENS} tokens, got {len(tokens)}"

    # -------------------------------------------------------------
    print("CASE 16: Guest revokes own token using refresh token")
    response = requests.post(
        f"{token_url}/revoke",
        headers=GUEST_HEADER,
        data={"token": GUEST_REFRESH},
    )
    assert response.status_code == 200
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    GUEST_ACCESS = response.json()["access_token"]
    GUEST_HEADER = {"Authorization": f"Bearer {GUEST_ACCESS}"}
    GUEST_ID_2 = response.json()["id"]
    GUEST_EX_ID = response.json()["executive_id"]
    response = requests.get(token_url, headers=GUEST_HEADER, params={"id": GUEST_ID})
    assert response.json() == []
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 17: Guest revokes another owned token via ID")
    response = requests.get(
        token_url, headers=GUEST_HEADER, params={"executive_id": GUEST_EX_ID}
    )
    tokens = response.json()
    ID = tokens[-1]["id"]
    response = requests.delete(f"{token_url}/{ID}", headers=GUEST_HEADER)
    assert response.status_code == 204
    response = requests.get(token_url, headers=GUEST_HEADER, params={"id": ID})
    assert response.json() == []
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 18: Fetch own tokens without Authorization header")
    response = requests.get(token_url)
    assert response.status_code == 401

    # -------------------------------------------------------------
    print("CASE 19: Revoke token without Authorization header")
    response = requests.post(f"{token_url}/revoke")
    assert response.status_code == 401

    # -------------------------------------------------------------
    print("CASE 20: Admin fetches guest token details")
    response = requests.post(token_url, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    ADMIN_ACCESS = response.json()["access_token"]
    ADMIN_REFRESH = response.json()["refresh_token"]
    ADMIN_HEADER = {"Authorization": f"Bearer {ADMIN_ACCESS}"}
    ADMIN_EX_ID = response.json()["executive_id"]
    ADMIN_ID = response.json()["id"]
    response = requests.get(
        token_url, headers=ADMIN_HEADER, params={"executive_id": GUEST_EX_ID}
    )
    assert response.json() != []
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 21: Guest attempts to fetch admin token details")
    response = requests.get(
        token_url, headers=GUEST_HEADER, params={"executive_id": ADMIN_EX_ID}
    )
    assert response.json() == []
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 22: Admin fetches token with id=0")
    response = requests.get(token_url, headers=ADMIN_HEADER, params={"id": 0})
    assert response.json() == []
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 23: Guest attempts to revoke admin token using ID")
    response = requests.delete(f"{token_url}/{ADMIN_ID}", headers=GUEST_HEADER)
    assert response.status_code == 403

    # -------------------------------------------------------------
    print("CASE 24: Guest attempts to revoke admin token using access token")
    response = requests.post(
        f"{token_url}/revoke", headers=GUEST_HEADER, data={"token": ADMIN_ACCESS}
    )
    assert response.status_code == 200
    response = requests.get(token_url, headers=ADMIN_HEADER, params={"id": ADMIN_ID})
    assert response.json() != []
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 25: Admin deletes token using id=0")
    response = requests.delete(f"{token_url}/0", headers=ADMIN_HEADER)
    assert response.status_code == 204

    # -------------------------------------------------------------
    print("CASE 26: Admin revokes guest access token")
    response = requests.post(
        f"{token_url}/revoke", headers=ADMIN_HEADER, data={"token": GUEST_ACCESS}
    )
    assert response.status_code == 200
    response = requests.get(token_url, headers=ADMIN_HEADER, params={"id": GUEST_ID_2})
    assert response.json() != []
    assert response.status_code == 200

    # -------------------------------------------------------------
    print("CASE 27: Authenticate using invalid bearer token type")
    response = requests.get(
        token_url, headers={"Authorization": f"Bearer Invalid {ADMIN_ACCESS}"}
    )
    assert response.status_code == 401

    # -------------------------------------------------------------
    print("CASE 28: Admin revokes all guest tokens using token IDs")
    response = requests.get(
        token_url, headers=ADMIN_HEADER, params={"executive_id": GUEST_EX_ID}
    )
    tokens = response.json()
    for token in tokens:
        ID = token["id"]
        response = requests.delete(f"{token_url}/{ID}", headers=ADMIN_HEADER)
        assert response.status_code == 204

    # -------------------------------------------------------------
    print("CASE 29: Admin deletes guest token using its ID")
    response = requests.delete(f"{token_url}/{ADMIN_ID}", headers=ADMIN_HEADER)
    assert response.status_code == 204

    print("=== EXECUTIVE TOKEN TESTS COMPLETED ===\n")
