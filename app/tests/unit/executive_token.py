import argparse
from http import HTTPStatus

from app.src import urls
from app.src.constants import MAX_EXECUTIVE_TOKENS
from app.src.enums import GrantType
from app.tests.unit import inputs
from app.tests.unit import helpers

TOKEN_URL = inputs.EXECUTIVE_BASE_URL + urls.URL_EXECUTIVE_TOKEN


## Test Cases
def test_case_001():
    """Generate access token"""
    print(f" 1  Functionality Testing ")
    print("Log in with admin credentials")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )

    print("Refresh token")
    refresh_data = {"refresh_token": response.json()["refresh_token"]}
    response = helpers.POST(
        TOKEN_URL + "/refresh", data=refresh_data, status_code=HTTPStatus.CREATED
    )
    NEW_HEADER = helpers.make_header(response)

    print("Fetch token details")
    helpers.GET(TOKEN_URL, NEW_HEADER, status_code=HTTPStatus.OK)

    print("Log out with the refreshed access token")
    helpers.DELETE(TOKEN_URL, NEW_HEADER, status_code=HTTPStatus.NO_CONTENT)


def test_case_002():
    """Try to generate access token using invalid username and password"""
    print("2 Try to generate access token using invalid username and password")
    invalid_credential = {"username": "unknown", "password": "unknown"}
    helpers.POST(
        TOKEN_URL, data=invalid_credential, status_code=HTTPStatus.UNAUTHORIZED
    )


def test_case_003():
    """Try to generate access token using invalid grant_type"""
    print("3 Try to generate access token using invalid grant_type")
    admin_credential = inputs.ExecutiveCredential.admin
    invalid_grant_type = {
        **admin_credential,
        "grant_type": GrantType.REFRESH_TOKEN,
    }
    helpers.POST(
        TOKEN_URL, data=invalid_grant_type, status_code=HTTPStatus.NOT_ACCEPTABLE
    )


def test_case_004():
    """Check Access token rotation (MAX_EXECUTIVE_TOKENS)"""
    print("4 Testing Access token rotation")
    admin_credential = inputs.ExecutiveCredential.admin
    for i in range(MAX_EXECUTIVE_TOKENS + 1):
        print(f"Login attempt #{i+1}")
        helpers.POST(TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED)
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
        f"Total tokens found for executive_id = {response.json()['executive_id']}: {len(tokens)}"
    )
    assert (
        len(tokens) <= MAX_EXECUTIVE_TOKENS
    ), f"Expected {MAX_EXECUTIVE_TOKENS} tokens, but got {len(tokens)}"
    print("Token rotation check is successful \n")


def test_case_005():
    """Token renewal using invalid refresh token"""
    print("5 Token renewal using invalid refresh token")
    refresh_data = {"refresh_token": inputs.random_string(64)}
    helpers.POST(
        TOKEN_URL + "/refresh",
        data=refresh_data,
        status_code=HTTPStatus.UNAUTHORIZED,
    )


def test_case_006():
    """Token renewal using a refresh token and grant_type as password"""
    print("6 Token renewal using a refresh token and grant_type as password")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
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
    """Token renewal using revoked refresh_token"""
    print("7 Token renewal using revoked refresh_token ")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    refresh_data = {"refresh_token": response.json()["refresh_token"]}
    HEADER = helpers.make_header(response)
    helpers.DELETE(f"{TOKEN_URL}", HEADER, status_code=HTTPStatus.NO_CONTENT)
    helpers.POST(
        TOKEN_URL + "/refresh",
        data=refresh_data,
        status_code=HTTPStatus.UNAUTHORIZED,
    )


def test_case_008():
    """Revoke own token with id"""
    print("8 Revoke own token with id")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    token_id = {"id": response.json()["id"]}
    HEADER = helpers.make_header(response)
    helpers.DELETE(
        TOKEN_URL,
        HEADER,
        data=token_id,
        status_code=HTTPStatus.NO_CONTENT,
    )


def test_case_009():
    """Revoke other self owned token using another valid access_token"""
    print("9 Revoke other self owned token using another valid access_token")
    guest_credential = inputs.ExecutiveCredential.guest
    response1 = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(response1)
    response2 = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    token_data = response2.json()["id"]
    helpers.DELETE(
        TOKEN_URL, HEADER, data={"id": token_data}, status_code=HTTPStatus.FORBIDDEN
    )


def test_case_010():
    """Revoke own token without passing header"""
    print("10 Revoke own token without passing header")
    helpers.DELETE(TOKEN_URL, {}, status_code=HTTPStatus.FORBIDDEN)


def test_case_011():
    """Try to access DELETE endpoint with revoked token"""
    print("11 Try to access DELETE endpoint with revoked token")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    token_id = {"id": response.json()["id"]}
    HEADER = helpers.make_header(response)
    helpers.DELETE(TOKEN_URL, HEADER, data=token_id, status_code=HTTPStatus.NO_CONTENT)
    helpers.DELETE(TOKEN_URL, HEADER, status_code=HTTPStatus.UNAUTHORIZED)


def test_case_012():
    """Revoking others' tokens with permission"""
    print("12 Revoking others' tokens with permission")
    admin_credential = inputs.ExecutiveCredential.admin
    admin_response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(admin_response)
    guest_credential = inputs.ExecutiveCredential.guest
    guest_response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    guest_token_id = {"id": guest_response.json()["id"]}
    helpers.DELETE(
        TOKEN_URL,
        HEADER,
        data=guest_token_id,
        status_code=HTTPStatus.NO_CONTENT,
    )


def test_case_013():
    """Revoking others' tokens without permission"""
    print("13 Revoking others' tokens without permission")
    guest_credential = inputs.ExecutiveCredential.guest
    admin_credential = inputs.ExecutiveCredential.admin
    guest_response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(guest_response)
    admin_response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    admin_token_id = {"id": admin_response.json()["id"]}
    helpers.DELETE(
        TOKEN_URL,
        HEADER,
        data=admin_token_id,
        status_code=HTTPStatus.FORBIDDEN,
    )


def test_case_014():
    """Admin tries to fetch guest token details"""
    print("14 Admin tries to fetch guest token details")
    # Login as admin
    admin_credential = inputs.ExecutiveCredential.admin
    admin_response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    ADMIN_HEADER = helpers.make_header(admin_response)
    # Login as guest and get token details
    guest_credential = inputs.ExecutiveCredential.guest
    guest_response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    guest_token_id = {"id": guest_response.json()["id"]}
    helpers.GET(
        TOKEN_URL, ADMIN_HEADER, params=guest_token_id, status_code=HTTPStatus.OK
    )


def test_case_015():
    """Admin tries to fetch invalid (id=0) token details"""
    print("15 Admin tries to fetch invalid (id=0) token details")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    HEADER = helpers.make_header(response)
    invalid_token_id = {"id": 0}
    response = helpers.GET(
        TOKEN_URL,
        HEADER,
        params=invalid_token_id,
        status_code=HTTPStatus.OK,
    )
    data = response.json()
    assert data == [], f"Expected empty list [], but got {data}"


def test_case_016():
    """Guest tries to fetch admin token details"""
    print("16 Guest tries to fetch admin token details")
    # Admin creates a token
    admin_credential = inputs.ExecutiveCredential.admin
    admin_response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    admin_token_id = {"id": admin_response.json()["id"]}
    # Guest logs in and attempts to fetch admin token details
    guest_credential = inputs.ExecutiveCredential.guest
    guest_response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    GUEST_HEADER = helpers.make_header(guest_response)
    response = helpers.GET(
        TOKEN_URL,
        GUEST_HEADER,
        params=admin_token_id,
        status_code=HTTPStatus.OK,
    )
    data = response.json()
    assert data == [], f"Expected empty list [], but got {data}"


def test_case_017():
    """Guest tries to fetch own token details without header"""
    print("17 Guest tries to fetch own token details without header")
    guest_credential = inputs.ExecutiveCredential.guest
    response = helpers.POST(
        TOKEN_URL, data=guest_credential, status_code=HTTPStatus.CREATED
    )
    token_id = {"id": response.json()["id"]}
    helpers.GET(TOKEN_URL, {}, params=token_id, status_code=HTTPStatus.FORBIDDEN)


def test():
    print(f"Starting test on target URL: {TOKEN_URL}")

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


## Argparse setup
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run test cases of executive token")
    parser.add_argument(
        "-test", action="store_true", help="Run executive token test cases"
    )
    args = parser.parse_args()

    if args.test:
        test()
