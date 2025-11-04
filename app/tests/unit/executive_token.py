"""
Executive Token API Test Suite.

This module contains a series of automated unit tests for verifying
the behavior of the Executive Token API endpoints.

Coverage includes:
    - Token generation, rotation, and refresh
    - Token revocation and permission checks
    - Access control validation between different users (admin/guest)
    - Error handling for invalid or expired tokens

Usage:
    python -m app.tests.unit.executive_token -test
"""

import argparse
from http import HTTPStatus

from app.src import urls
from app.src.constants import MAX_EXECUTIVE_TOKENS
from app.src.enums import GrantType
from app.tests.unit import inputs
from app.tests.unit import helpers


# Base token URL for executive domain
TOKEN_URL = inputs.EXECUTIVE_BASE_URL + urls.URL_EXECUTIVE_TOKEN


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------
def test_case_001():
    """Generate and refresh access token, then revoke it"""

    print("Functionality Testing")

    # Step 1: Login with admin credentials
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )

    # Step 2: Refresh the token using the refresh_token
    refresh_data = {"refresh_token": response.json()["refresh_token"]}
    response = helpers.POST(
        TOKEN_URL + "/refresh", data=refresh_data, status_code=HTTPStatus.CREATED
    )
    NEW_HEADER = helpers.make_header(response)

    # Step 3: Fetch details of the newly issued token
    helpers.GET(TOKEN_URL, NEW_HEADER, status_code=HTTPStatus.OK)

    # Step 4: Log out by revoking the refreshed token
    helpers.DELETE(TOKEN_URL, NEW_HEADER, status_code=HTTPStatus.NO_CONTENT)


def test_case_002():
    """Attempt to generate token with invalid credentials"""

    print("Invalid login credentials")
    invalid_credential = {"username": "unknown", "password": "unknown"}
    helpers.POST(
        TOKEN_URL, data=invalid_credential, status_code=HTTPStatus.UNAUTHORIZED
    )


def test_case_003():
    """Attempt to generate token using invalid grant_type"""

    print("Invalid grant_type test")
    admin_credential = inputs.ExecutiveCredential.admin
    invalid_grant_type = {
        **admin_credential,
        "grant_type": GrantType.REFRESH_TOKEN,
    }
    helpers.POST(
        TOKEN_URL, data=invalid_grant_type, status_code=HTTPStatus.NOT_ACCEPTABLE
    )


def test_case_004():
    """Validate Access token rotation (MAX_EXECUTIVE_TOKENS limit)"""

    print("Testing Access token rotation limit")
    admin_credential = inputs.ExecutiveCredential.admin

    # Issue tokens up to the configured max limit
    for i in range(MAX_EXECUTIVE_TOKENS + 1):
        print(f"Login attempt #{i + 1}")
        helpers.POST(TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED)

    # Fetch all tokens for the executive
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(response)
    executive_id = {"executive_id": response.json()["executive_id"]}
    response_list = helpers.GET(
        TOKEN_URL, HEADER, params=executive_id, status_code=HTTPStatus.OK
    )

    tokens = response_list.json()
    print(
        f"Total tokens found for executive_id={response.json()['executive_id']}: {len(tokens)}"
    )
    assert (
        len(tokens) <= MAX_EXECUTIVE_TOKENS
    ), f"Expected {MAX_EXECUTIVE_TOKENS} tokens, got {len(tokens)}"
    print("Token rotation check successful\n")


def test_case_005():
    """Attempt token renewal using invalid refresh token"""

    print("Invalid refresh token test")
    refresh_data = {"refresh_token": inputs.random_string(64)}
    helpers.POST(
        TOKEN_URL + "/refresh",
        data=refresh_data,
        status_code=HTTPStatus.UNAUTHORIZED,
    )


def test_case_006():
    """Try refreshing token with invalid grant_type (password instead of refresh_token)"""

    print("Invalid grant_type during refresh")
    admin_credential = inputs.ExecutiveCredential.admin

    # Login and obtain valid refresh token
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )

    # Attempt to use wrong grant_type
    invalid_grant_type = {
        "refresh_token": response.json()["refresh_token"],
        "grant_type": GrantType.PASSWORD,
    }
    helpers.POST(
        TOKEN_URL + "/refresh",
        data=invalid_grant_type,
        status_code=HTTPStatus.NOT_ACCEPTABLE,
    )


def test_case_007():
    """Try renewing a revoked refresh token"""

    print("Token renewal after revocation")
    admin_credential = inputs.ExecutiveCredential.admin

    # Obtain valid token
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    refresh_data = {"refresh_token": response.json()["refresh_token"]}
    HEADER = helpers.make_header(response)

    # Revoke token
    helpers.DELETE(f"{TOKEN_URL}", HEADER, status_code=HTTPStatus.NO_CONTENT)

    # Try using revoked refresh token
    helpers.POST(
        TOKEN_URL + "/refresh",
        data=refresh_data,
        status_code=HTTPStatus.UNAUTHORIZED,
    )


def test_case_008():
    """Revoke self-owned token using its ID"""

    print("Revoke own token with ID")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )

    # Prepare revocation payload
    token_id = {"id": response.json()["id"]}
    HEADER = helpers.make_header(response)

    # Delete token
    helpers.DELETE(TOKEN_URL, HEADER, data=token_id, status_code=HTTPStatus.NO_CONTENT)


def test_case_009():
    """Revoke another self-owned token using a different access_token"""

    print("Revoke other owned token with another valid token")
    guest_credential = inputs.ExecutiveCredential.guest

    # Generate two tokens for same user
    response1 = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(response1)
    response2 = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )

    # Attempt to revoke the second token using first
    token_data = response2.json()["id"]
    helpers.DELETE(
        TOKEN_URL, HEADER, data={"id": token_data}, status_code=HTTPStatus.FORBIDDEN
    )


def test_case_010():
    """Try revoking own token without sending header"""

    print("Revoke token without authentication")
    helpers.DELETE(TOKEN_URL, status_code=HTTPStatus.FORBIDDEN)


def test_case_011():
    """Try to access DELETE endpoint using a revoked token"""

    print("Use revoked token for DELETE operation")
    admin_credential = inputs.ExecutiveCredential.admin

    # Login and revoke token
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    token_id = {"id": response.json()["id"]}
    HEADER = helpers.make_header(response)
    helpers.DELETE(TOKEN_URL, HEADER, data=token_id, status_code=HTTPStatus.NO_CONTENT)

    # Attempt DELETE again using same token
    helpers.DELETE(TOKEN_URL, HEADER, status_code=HTTPStatus.UNAUTHORIZED)


def test_case_012():
    """Admin revokes guest token (authorized action)"""

    print("Admin revokes guest token")

    # Admin login
    admin_credential = inputs.ExecutiveCredential.admin
    admin_response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(admin_response)

    # Guest login
    guest_credential = inputs.ExecutiveCredential.guest
    guest_response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    guest_token_id = {"id": guest_response.json()["id"]}

    # Admin revokes guest's token
    helpers.DELETE(
        TOKEN_URL, HEADER, data=guest_token_id, status_code=HTTPStatus.NO_CONTENT
    )


def test_case_013():
    """Guest attempts to revoke admin token (unauthorized)"""

    print("Guest tries to revoke admin token")
    guest_credential = inputs.ExecutiveCredential.guest
    admin_credential = inputs.ExecutiveCredential.admin

    # Guest login
    guest_response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(guest_response)

    # Admin login (to get token ID)
    admin_response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    admin_token_id = {"id": admin_response.json()["id"]}

    # Guest attempts to delete admin token
    helpers.DELETE(
        TOKEN_URL, HEADER, data=admin_token_id, status_code=HTTPStatus.FORBIDDEN
    )


def test_case_014():
    """Admin fetches guest token details"""

    print("Admin fetches guest token details")

    # Admin login
    admin_credential = inputs.ExecutiveCredential.admin
    admin_response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    ADMIN_HEADER = helpers.make_header(admin_response)

    # Guest login
    guest_credential = inputs.ExecutiveCredential.guest
    guest_response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    guest_token_id = {"id": guest_response.json()["id"]}

    # Admin fetches guest token
    helpers.GET(
        TOKEN_URL, ADMIN_HEADER, params=guest_token_id, status_code=HTTPStatus.OK
    )


def test_case_015():
    """Admin fetches invalid token ID (id=0)"""

    print("Admin fetches invalid token details (id=0)")
    admin_credential = inputs.ExecutiveCredential.admin

    # Admin login
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(response)

    # Invalid token query
    invalid_token_id = {"id": 0}
    response = helpers.GET(
        TOKEN_URL, HEADER, params=invalid_token_id, status_code=HTTPStatus.OK
    )
    data = response.json()
    assert data == [], f"Expected empty list [], got {data}"


def test_case_016():
    """Guest tries to fetch admin token details"""

    print("Guest tries to access admin token details")

    # Admin login
    admin_credential = inputs.ExecutiveCredential.admin
    admin_response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    admin_token_id = {"id": admin_response.json()["id"]}

    # Guest login and attempt fetch
    guest_credential = inputs.ExecutiveCredential.guest
    guest_response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    GUEST_HEADER = helpers.make_header(guest_response)

    response = helpers.GET(
        TOKEN_URL, GUEST_HEADER, params=admin_token_id, status_code=HTTPStatus.OK
    )
    data = response.json()
    assert data == [], f"Expected empty list [], got {data}"


def test_case_017():
    """Guest fetches own token details without authorization header"""

    print("Guest tries to fetch own token without header")

    guest_credential = inputs.ExecutiveCredential.guest
    response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    token_id = {"id": response.json()["id"]}

    # Missing header should trigger forbidden response
    helpers.GET(TOKEN_URL, {}, params=token_id, status_code=HTTPStatus.FORBIDDEN)


# ------------------------------------------------------
# Runner
# ------------------------------------------------------
def test():
    """Execute all executive token test cases sequentially."""

    print(f"Starting test on target URL: {TOKEN_URL}\n")

    test_case_001()
    test_case_002()
    test_case_003()
    test_case_004()
    test_case_005()
    test_case_006()
    test_case_007()
    test_case_008()
    test_case_009()
    test_case_010()
    test_case_011()
    test_case_012()
    test_case_013()
    test_case_014()
    test_case_015()
    test_case_016()
    test_case_017()


# ---------------------------------------------------------------------------
# CLI Setup
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run test cases for executive token API"
    )
    parser.add_argument(
        "-test", action="store_true", help="Run executive token test cases"
    )
    args = parser.parse_args()

    if args.test:
        test()
