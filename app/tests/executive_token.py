from fastapi.testclient import TestClient
from app.main import app
from app.src.db import SessionLocal, ExecutiveToken
from app.src.constants import MAX_EXECUTIVE_TOKENS, MAX_ACCESS_TOKEN_VALIDITY
from app.src.enums import GrantType
import datetime
import time

# Use the actual FastAPI app client
client = TestClient(app)

# Create a single shared session using SessionLocal
session = SessionLocal()


# ---------------------------------------------------------------------------
# Test: End-to-End Executive Token Flow
# ---------------------------------------------------------------------------
def test_executive_token_end_to_end():
    """End-to-end test for executive token creation, refresh, revoke, and deletion."""

    # Step 1: Create a new token
    create_data = {
        "username": "admin",
        "password": "password",
        "platform_type": 1,
        "client_details": "Test Client",
        "grant_type": GrantType.PASSWORD,
    }
    response = client.post("/executive/entebus/account/token", data=create_data)
    assert response.status_code == 201

    token_data = response.json()
    assert "access_token" in token_data
    assert "refresh_token" in token_data
    assert token_data["expires_in"] == MAX_ACCESS_TOKEN_VALIDITY
    assert isinstance(
        datetime.datetime.fromisoformat(token_data["refresh_before"]), datetime.datetime
    )

    initial_token_id = token_data["id"]

    # Step 2: Fetch tokens (should return the created token)
    response = client.get(
        "/executive/entebus/account/token",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    assert response.status_code == 200
    tokens = response.json()
    assert len(tokens) == 1
    assert tokens[0]["id"] == initial_token_id
    assert "access_token" not in tokens[0]  # Should be masked

    # Step 3: Refresh the token
    refresh_data = {
        "refresh_token": token_data["refresh_token"],
        "grant_type": GrantType.REFRESH_TOKEN,
    }
    response = client.post(
        "/executive/entebus/account/token/refresh", data=refresh_data
    )
    assert response.status_code == 201
    refreshed_token_data = response.json()
    assert refreshed_token_data["id"] != initial_token_id
    assert "access_token" in refreshed_token_data
    assert "refresh_token" in refreshed_token_data

    # Step 4: Revoke the initial token
    logout_data = {"token": token_data["access_token"]}
    response = client.post(
        "/executive/entebus/account/token/revoke",
        data=logout_data,
        headers={"Authorization": f"Bearer {refreshed_token_data['access_token']}"},
    )
    assert response.status_code == 200

    # Step 5: Verify revoked token is not active
    time.sleep(1)  # Allow background consistency
    response = client.get(
        "/executive/entebus/account/token",
        headers={"Authorization": f"Bearer {refreshed_token_data['access_token']}"},
    )
    assert response.status_code == 200
    tokens = response.json()
    assert len(tokens) == 1
    assert tokens[0]["id"] == refreshed_token_data["id"]

    # Step 6: Delete the refreshed token
    response = client.delete(
        f"/executive/entebus/account/token/{refreshed_token_data['id']}",
        headers={"Authorization": f"Bearer {refreshed_token_data['access_token']}"},
    )
    assert response.status_code == 204

    # Step 7: Verify all tokens deleted in DB
    active_tokens = (
        session.query(ExecutiveToken)
        .filter(ExecutiveToken.is_revoked == False)
        .filter(ExecutiveToken.executive_id == 1)
        .count()
    )
    assert active_tokens == 0


# ---------------------------------------------------------------------------
# Test: Maximum Token Limit
# ---------------------------------------------------------------------------
def test_max_executive_tokens():
    """Ensure system enforces maximum allowed active tokens per executive."""

    tokens = []

    # Create maximum number of tokens
    for _ in range(MAX_EXECUTIVE_TOKENS):
        create_data = {
            "username": "admin",
            "password": "password",
            "platform_type": 2,
            "client_details": "Test Client",
            "grant_type": GrantType.PASSWORD,
        }
        response = client.post("/executive/entebus/account/token", data=create_data)
        assert response.status_code == 201
        tokens.append(response.json())

    # Create one more token — cleanup should allow it
    create_data = {
        "username": "admin",
        "password": "password",
        "platform_type": 3,
        "client_details": "Extra Token",
        "grant_type": GrantType.PASSWORD,
    }
    response = client.post("/executive/entebus/account/token", data=create_data)
    assert response.status_code == 201

    new_token = response.json()
    assert new_token["id"] not in [t["id"] for t in tokens]

    # Verify that total active tokens <= MAX_EXECUTIVE_TOKENS
    response = client.get(
        "/executive/entebus/account/token",
        headers={"Authorization": f"Bearer {new_token['access_token']}"},
    )
    assert response.status_code == 200
    tokens = response.json()
    assert len(tokens) <= MAX_EXECUTIVE_TOKENS
