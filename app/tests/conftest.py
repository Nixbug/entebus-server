# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.src.db import SessionLocal

@pytest.fixture(scope="module")
def client():
    """Fixture for FastAPI TestClient"""
    with TestClient(app) as c:
        yield c

@pytest.fixture(scope="module")
def session():
    """Fixture for DB session"""
    db = SessionLocal()
    yield db
    db.close()

@pytest.fixture
def admin_credential():
    return {
        "username": "admin",
        "password": "password",
        "platform_type": 1,
        "client_details": "Pytest Client",
    }

@pytest.fixture
def guest_credential():
    return {
        "username": "guest",
        "password": "password",
        "platform_type": 2,
        "client_details": "Guest Client",
    }
