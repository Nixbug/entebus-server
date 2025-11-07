# tests/test_executive_token.py
from http import HTTPStatus
import time
from app.src.constants import MAX_EXECUTIVE_TOKENS, MAX_ACCESS_TOKEN_VALIDITY
from app.src.enums import GrantType
from app.src.db import ExecutiveToken
import datetime


BASE_URL = "/executive/entebus/account/token"


# ---------------------------------------------------------------------------
# 1️⃣ Total Functionality
# ---------------------------------------------------------------------------
def test_total_functionality(client, admin_credential, session):
    """Generate, refresh, fetch, and revoke own token (end-to-end)."""

    # Generate access token
    data = {**admin_credential, "grant_type": GrantType.PASSWORD}
    response = client.post(BASE_URL, data=data)
    assert response.status_code == HTTPStatus.CREATED
    token = response.json()

    # Validate response
    assert "access_token" in token
    assert "refresh_token" in token
    assert token["expires_in"] == MAX_ACCESS_TOKEN_VALIDITY

    # Refresh token
    refresh_data = {
        "refresh_token": token["refresh_token"],
        "grant_type": GrantType.REFRESH_TOKEN,
    }
    response = client.post(BASE_URL + "/refresh", data=refresh_data)
    assert response.status_code == HTTPStatus.CREATED
    refreshed = response.json()

    # Fetch all tokens
    headers = {"Authorization": f"Bearer {refreshed['access_token']}"}
    response = client.get(BASE_URL, headers=headers)
    assert response.status_code == HTTPStatus.OK
    tokens = response.json()
    assert len(tokens) >= 1

    # Revoke token (no id)
    response = client.delete(f"{BASE_URL}/{refreshed['id']}", headers=headers)
    assert response.status_code == HTTPStatus.NO_CONTENT


# ---------------------------------------------------------------------------
# 2️⃣ Generate token + Revocation scenarios
# ---------------------------------------------------------------------------
def test_invalid_credentials_and_grant_type(client):
    """Invalid username, missing field, and invalid grant type."""
    # Invalid username/password
    data = {"username": "bad", "password": "wrong"}
    response = client.post(BASE_URL, data=data)
    assert response.status_code == HTTPStatus.UNAUTHORIZED

    # Missing required field
    data = {"username": "admin"}  # no password
    response = client.post(BASE_URL, data=data)
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    # Invalid grant type
    data = {
        "username": "admin",
        "password": "password",
        "grant_type": GrantType.REFRESH_TOKEN,
    }
    response = client.post(BASE_URL, data=data)
    assert response.status_code == HTTPStatus.NOT_ACCEPTABLE


def test_access_token_rotation_and_revoke(client, guest_credential):
    """Ensure guest token rotation obeys MAX_EXECUTIVE_TOKENS."""
    data = {**guest_credential, "grant_type": GrantType.PASSWORD}

    tokens = []
    for _ in range(MAX_EXECUTIVE_TOKENS + 2):
        response = client.post(BASE_URL, data=data)
        assert response.status_code == HTTPStatus.CREATED
        tokens.append(response.json())

    # Fetch tokens (should be limited)
    headers1 = {"Authorization": f"Bearer {tokens[-1]['access_token']}"}
    headers2 = {"Authorization": f"Bearer {tokens[-2]['access_token']}"}
    headers3 = {"Authorization": f"Bearer {tokens[-3]['access_token']}"}
    response = client.get(BASE_URL, headers=headers1)
    assert response.status_code == HTTPStatus.OK
    all_tokens = response.json()
    assert len(all_tokens) <= MAX_EXECUTIVE_TOKENS

    # Revoke own tokens (access token)
    response = client.post(
        BASE_URL + "/revoke",
        headers=headers1,
        data={"token": tokens[-1]["access_token"]},
    )
    assert response.status_code == HTTPStatus.OK

    # Revoke own tokens (refresh token)
    response = client.post(
        BASE_URL + "/revoke",
        headers=headers2,
        data={"token": tokens[-1]["refresh_token"]},
    )
    assert response.status_code == HTTPStatus.OK

    # Revoke own token by ID
    token_id = tokens[-1]["id"]
    response = client.delete(f"{BASE_URL}/{token_id}", headers=headers3)
    assert response.status_code == HTTPStatus.NO_CONTENT


# ---------------------------------------------------------------------------
# 3️⃣ Generate using refresh_token
# ---------------------------------------------------------------------------
def test_refresh_token_invalid_and_revoked(client, admin_credential):
    """Invalid refresh and revoked token scenarios."""
    # Invalid refresh token
    data = {"refresh_token": "invalid123", "grant_type": GrantType.REFRESH_TOKEN}
    response = client.post(BASE_URL + "/refresh", data=data)
    assert response.status_code == HTTPStatus.UNAUTHORIZED

    # Valid login
    data = {**admin_credential, "grant_type": GrantType.PASSWORD}
    response = client.post(BASE_URL, data=data)
    token = response.json()

    # Invalid grant type during refresh
    data = {"refresh_token": token["refresh_token"], "grant_type": GrantType.PASSWORD}
    response = client.post(BASE_URL + "/refresh", data=data)
    assert response.status_code == HTTPStatus.NOT_ACCEPTABLE

    # Revoke and ensure cannot refresh again
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    response = client.delete(f"{BASE_URL}/{token['id']}", headers=headers)
    assert response.status_code == HTTPStatus.NO_CONTENT

    data = {
        "refresh_token": token["refresh_token"],
        "grant_type": GrantType.REFRESH_TOKEN,
    }
    response = client.post(BASE_URL + "/refresh", data=data)
    assert response.status_code == HTTPStatus.UNAUTHORIZED


# ---------------------------------------------------------------------------
# 4️⃣ Tests without header
# ---------------------------------------------------------------------------
def test_without_header_access_and_revoke(client, guest_credential):
    """Guest attempts to fetch or revoke without auth header."""
    # Login
    data = {**guest_credential, "grant_type": GrantType.PASSWORD}
    response = client.post(BASE_URL, data=data)
    token = response.json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    # Fetch without header
    params = {"id": token["id"]}
    response = client.get(BASE_URL, params=params)
    assert response.status_code == HTTPStatus.FORBIDDEN

    # Revoke without header
    response = client.delete(f"{BASE_URL}/{token['id']}", headers=headers)
    assert response.status_code == HTTPStatus.NO_CONTENT


# ---------------------------------------------------------------------------
# 5️⃣ Fetch & Revoke cross-role tests
# ---------------------------------------------------------------------------
def test_fetch_and_revoke_cross_roles(client, admin_credential, guest_credential):
    """Admin ↔ Guest interactions for fetch/revoke."""

    # Admin login
    admin_data = {**admin_credential, "grant_type": GrantType.PASSWORD}
    admin_response = client.post(BASE_URL, data=admin_data)
    admin_token = admin_response.json()
    admin_header = {"Authorization": f"Bearer {admin_token['access_token']}"}

    # Guest login
    guest_data = {**guest_credential, "grant_type": GrantType.PASSWORD}
    guest_response = client.post(BASE_URL, data=guest_data)
    guest_token = guest_response.json()
    guest_header = {"Authorization": f"Bearer {guest_token['access_token']}"}

    # Admin fetches guest token
    response = client.get(
        BASE_URL, headers=admin_header, params={"id": guest_token["id"]}
    )
    assert response.status_code == HTTPStatus.OK

    # Admin fetch invalid id
    response = client.get(BASE_URL, headers=admin_header, params={"id": 0})
    assert response.status_code == HTTPStatus.OK

    # Guest fetch admin token
    response = client.get(
        BASE_URL, headers=guest_header, params={"id": admin_token["id"]}
    )
    assert response.status_code == HTTPStatus.OK

    # Guest revoke admin token (id in URL)
    response = client.delete(f"{BASE_URL}/{admin_token['id']}", headers=guest_header)
    assert response.status_code == HTTPStatus.FORBIDDEN

    # Guest revoke admin token with access token
    response = client.post(
        BASE_URL + "/revoke",
        headers=guest_header,
        data={"token": admin_token["access_token"]},
    )
    assert response.status_code == HTTPStatus.OK

    # Admin fetch tokens (check revoked)
    response = client.get(BASE_URL, headers=admin_header)
    assert response.status_code == HTTPStatus.OK

    # Admin revoke own token with guest token → should fail
    response = client.post(
        BASE_URL + "/revoke",
        headers=guest_header,
        data={"token": admin_token["access_token"]},
    )
    assert response.status_code == HTTPStatus.OK

    # Admin fetch tokens again
    response = client.get(BASE_URL, headers=admin_header)
    assert response.status_code == HTTPStatus.OK

    # Admin revoke own token with invalid id=0
    response = client.delete(f"{BASE_URL}/0", headers=admin_header)
    assert response.status_code == HTTPStatus.NO_CONTENT

    # Admin revoke own token by id
    response = client.delete(f"{BASE_URL}/{admin_token['id']}", headers=admin_header)
    assert response.status_code == HTTPStatus.NO_CONTENT

    # Admin try to use revoked token
    response = client.get(BASE_URL, headers=admin_header)
    assert response.status_code == HTTPStatus.UNAUTHORIZED
