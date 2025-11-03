from http import HTTPStatus

from app.src import urls
from app.tests.unit import inputs
from app.tests.unit import helpers


## Test Cases
def token_test_case_001(BASE_URL: str):
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_TOKEN
    print(f"Testing {TOKEN_URL}")

    print("Log in with admin credentials")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    TOKEN_DATA = response.json()["refresh_token"]
    HEADER = helpers.make_header(response)

    print("Fetch token details")
    helpers.GET(TOKEN_URL, HEADER, status_code=HTTPStatus.OK)

    print("Refresh token")
    refresh_token = {"refresh_token": TOKEN_DATA}
    response = helpers.POST(
        TOKEN_URL + "/refresh", data=refresh_token, status_code=HTTPStatus.CREATED
    )
    NEW_HEADER = helpers.make_header(response)

    print("Log out with the refreshed access token")
    helpers.DELETE(TOKEN_URL, NEW_HEADER, status_code=HTTPStatus.NO_CONTENT)
