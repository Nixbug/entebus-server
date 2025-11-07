import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.src.db import SessionLocal, Executive, ExecutiveToken, engine
from app.src.constants import MAX_EXECUTIVE_TOKENS, MAX_ACCESS_TOKEN_VALIDITY
from sqlalchemy import event
from sqlalchemy.orm import Session
import datetime
import time

# Fixture to create a test client
@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

# Fixture to set up and tear down test database
@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)

    # Create tables
    Executive.metadata.create_all(bind=engine)
    ExecutiveToken.metadata.create_all(bind=engine)

    yield session

    session.close()
    transaction.rollback()
    connection.close()

# Fixture to create a test executive
@pytest.fixture
def test_executive(db_session):
    executive = Executive(
        username="test_user",
        password="test_password",
        gender=0,
        full_name="Test User",
        designation="Manager",
        status=1,
        email_id="test@example.com"
    )
    db_session.add(executive)
    db_session.commit()
    db_session.refresh(executive)
    return executive

# Test case for end-to-end executive token operations
def test_executive_token_end_to_end(client, db_session, test_executive):
    # Step 1: Create a new token
    create_data = {
        "username": "test_user",
        "password": "test_password",
        "platform_type": 0,
        "client_details": "Test Client",
        "grant_type": "password"
    }
    response = client.post("/executive/token", data=create_data)
    assert response.status_code == 201
    token_data = response.json()
    assert "access_token" in token_data
    assert "refresh_token" in token_data
    assert token_data["expires_in"] == MAX_ACCESS_TOKEN_VALIDITY
    assert isinstance(datetime.datetime.fromisoformat(token_data["refresh_before"]), datetime.datetime)
    initial_token_id = token_data["id"]

    # Step 2: Fetch tokens (should return the created token)
    response = client.get("/executive/token", headers={"Authorization": f"Bearer {token_data['access_token']}"})
    assert response.status_code == 200
    tokens = response.json()
    assert len(tokens) == 1
    assert tokens[0]["id"] == initial_token_id
    assert "access_token" not in tokens[0]  # Should be masked

    # Step 3: Refresh the token
    refresh_data = {
        "refresh_token": token_data["refresh_token"],
        "grant_type": "refresh_token"
    }
    response = client.post("/executive/token/refresh", data=refresh_data)
    assert response.status_code == 201
    refreshed_token_data = response.json()
    assert refreshed_token_data["id"] != initial_token_id
    assert "access_token" in refreshed_token_data
    assert "refresh_token" in refreshed_token_data

    # Step 4: Revoke the initial token
    logout_data = {"token": token_data["access_token"]}
    response = client.post("/executive/token/revoke", data=logout_data, headers={"Authorization": f"Bearer {refreshed_token_data['access_token']}"})
    assert response.status_code == 200

    # Step 5: Verify token is revoked (fetch should not include revoked token)
    time.sleep(1)  # Allow for eventual consistency
    response = client.get("/executive/token", headers={"Authorization": f"Bearer {refreshed_token_data['access_token']}"})
    assert response.status_code == 200
    tokens = response.json()
    assert len(tokens) == 1  # Only the refreshed token should be active
    assert tokens[0]["id"] == refreshed_token_data["id"]

    # Step 6: Delete the refreshed token
    delete_data = {"id": refreshed_token_data["id"]}
    response = client.delete(f"/executive/token/{refreshed_token_data['id']}", data=delete_data, headers={"Authorization": f"Bearer {refreshed_token_data['access_token']}"})
    assert response.status_code == 204

    # Step 7: Verify all tokens are deleted
    response = client.get("/executive/token", headers={"Authorization": f"Bearer {refreshed_token_data['access_token']}"})
    assert response.status_code == 200
    tokens = response.json()
    assert len(tokens) == 0

# Test case for exceeding maximum tokens
def test_max_executive_tokens(client, db_session, test_executive):
    # Create maximum number of tokens
    tokens = []
    for _ in range(MAX_EXECUTIVE_TOKENS):
        create_data = {
            "username": "test_user",
            "password": "test_password",
            "platform_type": 0,
            "client_details": f"Test Client {_}",
            "grant_type": "password"
        }
        response = client.post("/executive/token", data=create_data)
        assert response.status_code == 201
        tokens.append(response.json())

    # Attempt to create one more token (should still succeed due to cleanup)
    create_data = {
        "username": "test_user",
        "password": "test_password",
        "platform_type": 0,
        "client_details": "Extra Token",
        "grant_type": "password"
    }
    response = client.post("/executive/token", data=create_data)
    assert response.status_code == 201
    new_token = response.json()
    assert new_token["id"] not in [t["id"] for t in tokens]  # New token ID

    # Verify only MAX_EXECUTIVE_TOKENS tokens exist
    response = client.get("/executive/token", headers={"Authorization": f"Bearer {new_token['access_token']}"})
    assert response.status_code == 200
    tokens = response.json()
    assert len(tokens) <= MAX_EXECUTIVE_TOKENS
    