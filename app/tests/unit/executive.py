import argparse
from http import HTTPStatus

from app.src import urls
from app.tests.unit import inputs
from app.tests.unit import helpers


## Test Cases
def test_case_001(BASE_URL: str):
    """Generate access token"""
    TOKEN_URL = BASE_URL + urls.URL_EXECUTIVE_ACCOUNT
    print(f" 1  Functionality Testing ")

    print("Log in with admin credentials")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    response.json()["refresh_token"]
    helpers.make_header(response)
    print(response.json())


def test_executive_account(BASE_URL: str = inputs.EXECUTIVE_BASE_URL):
    print(f"Starting test on target URL: {BASE_URL}")

    test_case_001(BASE_URL)

    print(f"Finished test on target URL: {BASE_URL}")


## Argparse setup
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run test cases for executive account")
    parser.add_argument(
        "-test_executive_account",
        action="store_true",
        help="Run executive account tests",
    )
    args = parser.parse_args()

    if args.test_executive_account:
        test_executive_account()
