import argparse
from http import HTTPStatus
from datetime import datetime, timedelta, timezone

from app.src import urls
from app.src.constants import MAX_EXECUTIVE_TOKENS, MAX_REFRESH_TOKEN_VALIDITY
from app.src.enums import GrantType
from app.src.db import ExecutiveToken, SessionLocal
from app.tests.unit import inputs
from app.tests.unit import helpers


## Test Cases
def test_case_001(BASE_URL: str):
    """Generate access token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print(f" 1  Functionality Testing ")

    print("Log in with admin credentials")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    TOKEN_DATA = response.json()["refresh_token"]
    HEADER = helpers.make_header(response)
    print(response.json())

    print("Fetch token details")
    response = helpers.GET(TOKEN_URL, HEADER, status_code=HTTPStatus.OK)
    print(response.json())

    print("Refresh token")
    refresh_token = {"refresh_token": TOKEN_DATA}
    response = helpers.POST(
        TOKEN_URL + "/refresh", data=refresh_token, status_code=HTTPStatus.CREATED
    )
    NEW_HEADER = helpers.make_header(response)
    print(response.json())

    print("Log out with the refreshed access token")
    helpers.DELETE(TOKEN_URL, NEW_HEADER, status_code=HTTPStatus.NO_CONTENT)


def test_case_002(BASE_URL: str):
    """Try to generate access token using invalid username and password"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("2 Try to generate access token using invalid username and password")

    invalid_cred = {"username": "unknown", "password": "unknown"}
    helpers.POST(TOKEN_URL, data=invalid_cred, status_code=HTTPStatus.UNAUTHORIZED)


def test_case_003(BASE_URL: str):
    """Try to generate access token using invalid grant_type"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("3 Try to generate access token using invalid grant_type")

    invalid_admin_cre = {
        "username": "admin",
        "password": "password",
        "grant_type": GrantType.REFRESH_TOKEN,
    }
    helpers.POST(
        TOKEN_URL, data=invalid_admin_cre, status_code=HTTPStatus.NOT_ACCEPTABLE
    )


def test_case_004(BASE_URL: str):
    """Check Access token rotation (MAX_EXECUTIVE_TOKENS)"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("4 Testing Access token rotation")

    admin_cred = inputs.ExecutiveCredential.admin
    for i in range(MAX_EXECUTIVE_TOKENS + 1):
        print(f"Login attempt #{i+1}")
        helpers.POST(TOKEN_URL, data=admin_cred, status_code=HTTPStatus.CREATED)
    print("Fetch tokens after rotation")
    response = helpers.POST(TOKEN_URL, data=admin_cred, status_code=HTTPStatus.CREATED)
    header = helpers.make_header(response)

    # Use admin.executive_id as query parameter
    query_params = {"executive_id": response.json()["executive_id"]}
    get_response = helpers.GET(
        TOKEN_URL, header, params=query_params, status_code=HTTPStatus.OK
    )

    token_list = get_response.json()
    token_count = len(token_list)

    print(
        f"Total tokens found for executive_id={response.json()['executive_id']}: {token_count}"
    )
    assert token_count <= MAX_EXECUTIVE_TOKENS, (
        f"Token rotation failed: found {token_count} tokens, "
        f"expected <= {MAX_EXECUTIVE_TOKENS}"
    )

    print("Token rotation check passed\n")


def test_case_005(BASE_URL: str):
    """Token renewal using a refresh token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN

    print("5 Token renewal using a refresh token")
    admin = inputs.ExecutiveCredential.admin
    resp = helpers.POST(TOKEN_URL, data=admin, status_code=HTTPStatus.CREATED)
    refresh_payload = {"refresh_token": resp.json()["refresh_token"]}

    print("Renew token using refresh token")
    helpers.POST(
        TOKEN_URL + "/refresh",
        data=refresh_payload,
        status_code=HTTPStatus.CREATED,
    )


def test_case_006(BASE_URL: str):
    """Token renewal using refresh token and grant_type=password"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("6 Token renewal using refresh token and grant_type=password")

    payload = {"refresh_token": inputs.random_string(64), "grant_type": "password"}
    helpers.POST(
        TOKEN_URL + "/refresh", data=payload, status_code=HTTPStatus.NOT_ACCEPTABLE
    )


def test_case_007(BASE_URL: str):
    """Token renewal using revoked refresh_token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("7 Token renewal using revoked refresh_token ")

    resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.admin)
    refresh_token = resp.json()["refresh_token"]
    header = helpers.make_header(resp)

    helpers.DELETE(f"{TOKEN_URL}", header, status_code=HTTPStatus.NO_CONTENT)
    helpers.POST(
        TOKEN_URL + "/refresh",
        data={"refresh_token": refresh_token},
        status_code=HTTPStatus.UNAUTHORIZED,
    )


def test_case_008(BASE_URL: str):
    """Token renewal using invalid refresh_token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("8 Token renewal using invalid refresh_token")
    helpers.POST(
        TOKEN_URL + "/refresh",
        data={"refresh_token": "invalid"},
        status_code=HTTPStatus.UNAUTHORIZED,
    )


def test_case_009(BASE_URL: str):
    """Token renewal using expired refresh_token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("9 Token renewal using expired refresh_token")
    expired_token = "expired_dummy_token"
    helpers.POST(
        TOKEN_URL + "/refresh",
        data={"refresh_token": expired_token},
        status_code=HTTPStatus.UNAUTHORIZED,
    )


def test_case_010(BASE_URL: str):
    """Revoke own token with id"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("10 Revoke own token with id")

    resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.admin)
    header = helpers.make_header(resp)
    helpers.DELETE(
        TOKEN_URL,
        header,
        data={"id": resp.json()["id"]},
        status_code=HTTPStatus.NO_CONTENT,
    )


def test_case_011(BASE_URL: str):
    """Revoke other self owned token using another valid access_token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("11 Revoke other self owned token using another valid access_token")

    resp1 = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.guest)
    header1 = helpers.make_header(resp1)

    resp2 = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.guest)
    token2_id = resp2.json()["id"]

    helpers.DELETE(
        TOKEN_URL, header1, data={"id": token2_id}, status_code=HTTPStatus.FORBIDDEN
    )


def test_case_012(BASE_URL: str):
    """Revoke own token without id"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("12 Revoke own token without id")

    resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.admin)
    header = helpers.make_header(resp)
    helpers.DELETE(TOKEN_URL, header, status_code=HTTPStatus.NO_CONTENT)


def test_case_013(BASE_URL: str):
    """Revoke own token without passing header"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("13 Revoke own token without passing header")
    helpers.DELETE(TOKEN_URL, {}, status_code=HTTPStatus.FORBIDDEN)


def test_case_014(BASE_URL: str):
    """Try to access GET endpoint with revoked token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("14 Try to access GET endpoint with revoked token")
    resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.admin)
    token_id = resp.json()["id"]
    header = helpers.make_header(resp)
    helpers.DELETE(
        TOKEN_URL, header, data={"id": token_id}, status_code=HTTPStatus.NO_CONTENT
    )
    helpers.GET(TOKEN_URL, header, status_code=HTTPStatus.UNAUTHORIZED)


def test_case_015(BASE_URL: str):
    """Revoking others' tokens with permission"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("15 Revoking others' tokens with permission")
    admin_resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.admin)
    admin_header = helpers.make_header(admin_resp)

    guest_resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.guest)
    guest_token_id = guest_resp.json()["id"]

    helpers.DELETE(
        TOKEN_URL,
        admin_header,
        data={"id": guest_token_id},
        status_code=HTTPStatus.NO_CONTENT,
    )


def test_case_016(BASE_URL: str):
    """Revoking others' tokens without permission"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("16 Revoking others' tokens without permission")
    guest_resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.guest)
    guest_header = helpers.make_header(guest_resp)

    admin_resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.admin)
    admin_token_id = admin_resp.json()["id"]

    helpers.DELETE(
        TOKEN_URL,
        guest_header,
        data={"id": admin_token_id},
        status_code=HTTPStatus.FORBIDDEN,
    )


def test_case_017(BASE_URL: str):
    """Try to revoke tokens with permission using expired access token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("17 Try to revoke tokens with permission using expired access token")
    expired_header = {"Authorization": "Bearer expired_token"}
    helpers.DELETE(TOKEN_URL, expired_header, status_code=HTTPStatus.UNAUTHORIZED)


def test_case_018(BASE_URL: str):
    """Fetching tokens with permission"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("18 Fetching tokens with permission")
    resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.admin)
    header = helpers.make_header(resp)
    helpers.GET(TOKEN_URL, header)


def test_case_019(BASE_URL: str):
    """Fetching tokens with permission and passing valid query"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("19 Fetching tokens with permission and passing valid query")
    resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.admin)
    header = helpers.make_header(resp)
    helpers.GET(TOKEN_URL, header)


def test_case_020(BASE_URL: str):
    """Fetching tokens without permission"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print("20 Fetching tokens without permission")
    resp = helpers.POST(TOKEN_URL, data=inputs.ExecutiveCredential.guest)
    header = helpers.make_header(resp)
    helpers.GET(TOKEN_URL, header, status_code=HTTPStatus.OK)


def test_executive_token(BASE_URL: str = inputs.EXECUTIVE_BASE_URL):
    print(f"Starting test on target URL: {BASE_URL}")

    test_case_001(BASE_URL)
    test_case_002(BASE_URL)
    test_case_003(BASE_URL)
    test_case_004(BASE_URL)
    test_case_005(BASE_URL)
    test_case_006(BASE_URL)
    test_case_007(BASE_URL)
    test_case_008(BASE_URL)
    test_case_009(BASE_URL)
    test_case_010(BASE_URL)
    test_case_011(BASE_URL)
    test_case_012(BASE_URL)
    test_case_013(BASE_URL)
    test_case_014(BASE_URL)
    test_case_015(BASE_URL)
    test_case_016(BASE_URL)
    test_case_017(BASE_URL)
    test_case_018(BASE_URL)
    test_case_019(BASE_URL)
    test_case_020(BASE_URL)

    print(f"Finished test on target URL: {BASE_URL}")


## Argparse setup
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run test cases for executive tokens")
    parser.add_argument(
        "-test_executive_token", action="store_true", help="Run executive tests"
    )
    args = parser.parse_args()

    if args.test_executive_token:
        test_executive_token()
