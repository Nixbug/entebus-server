import requests

from app.src.urls import URL_EXECUTIVE_TOKEN
from tests.src.inputs import INVALID_EXECUTIVE_CREDENTIALS, VALID_EXECUTIVE_CREDENTIALS


def run_endpoint_test(base_url: str):
    """Test authentication: generate access token with valid credentials."""
    # --- Test Case: 001 --- --- --- --- --- --- --- --- --- --- --- ---
    print("Login with missing credentials")
    resp = requests.post(f"{base_url}/executive{URL_EXECUTIVE_TOKEN}", data={})
    assert (
        resp.status_code == 422
    ), "Authentication should fail: expected 422 Unprocessable Entity"

    # --- Test Case: 002 --- --- --- --- --- --- --- --- --- --- --- ---
    print("Login with invalid credentials")
    resp = requests.post(
        f"{base_url}/executive{URL_EXECUTIVE_TOKEN}",
        data=INVALID_EXECUTIVE_CREDENTIALS["wrong_credentials"],
    )
    assert (
        resp.status_code == 401
    ), "Authentication should fail: expected 401 Unauthorized"

    # --- Test Case: 003 --- --- --- --- --- --- --- --- --- --- --- ---
    print("Login with valid credentials")
    resp = requests.post(
        f"{base_url}/executive{URL_EXECUTIVE_TOKEN}",
        data=VALID_EXECUTIVE_CREDENTIALS["admin"],
    )
    assert resp.status_code == 201, "Authentication failed: expected 201 Created"
    # Create header for future requests
    ADMIN_HEADER = {"Authorization": f"Bearer {resp.json()['access_token']}"}
