import argparse
from http import HTTPStatus

from app.src import urls
from app.tests.unit import inputs
from app.tests.unit import helpers

TOKEN_URL = inputs.EXECUTIVE_BASE_URL + urls.URL_EXECUTIVE_TOKEN
ACCOUNT_URL = inputs.EXECUTIVE_BASE_URL + urls.URL_EXECUTIVE_ACCOUNT


## Test Cases
def test_case_001():
    """Generate executive_account"""
    print("Functionality Testing")
    print("Log in with admin credentials")
    admin_credential = inputs.ExecutiveCredential.admin
    response = helpers.POST(
        TOKEN_URL, data=admin_credential, status_code=HTTPStatus.CREATED
    )
    helpers.make_header(response)
    # TODO: Add more test cases for executive account.


def test():
    print(f"Starting test on target URL: {ACCOUNT_URL}")

    test_case_001(ACCOUNT_URL)


## Argparse setup
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run test cases of executive account")
    parser.add_argument(
        "-test",
        action="store_true",
        help="Run executive account test cases",
    )
    args = parser.parse_args()

    if args.test:
        test()
