import argparse

from app.tests.unit import executive
from app.tests.unit.inputs import EXECUTIVE_BASE_URL, VENDOR_BASE_URL, OPERATOR_BASE_URL


def test_executive(BASE_URL: str = EXECUTIVE_BASE_URL):
    print(f"Starting test on target URL: {BASE_URL}")

    executive.token_test_case_001(BASE_URL)

    print(f"Finished test on target URL: {BASE_URL}")


def test_vendor(BASE_URL: str = VENDOR_BASE_URL):
    print(f"Starting test on target URL: {BASE_URL}")

    print(f"Finished test on target URL: {BASE_URL}")


def test_operator(BASE_URL: str = OPERATOR_BASE_URL):
    print(f"Starting test on target URL: {BASE_URL}")

    print(f"Finished test on target URL: {BASE_URL}")


## Argparse setup
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run test cases")

    parser.add_argument(
        "-test_executive", action="store_true", help="Run executive tests"
    )
    parser.add_argument("-test_vendor", action="store_true", help="Run vendor tests")
    parser.add_argument(
        "-test_operator", action="store_true", help="Run operator tests"
    )
    args = parser.parse_args()

    if args.test_executive:
        test_executive()
    if args.test_vendor:
        test_vendor()
    if args.test_operator:
        test_operator()
