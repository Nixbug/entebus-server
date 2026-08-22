"""
Test runner for invoking automated tests.
Make sure that the server is running before executing this script.
Usage:
    python -m tests.runner --url http://localhost:8080
"""

import argparse

from tests.executive.happy_flow import run_test as run_executive_happy_flow
from tests.operator.happy_flow import run_test as run_operator_happy_flow
from tests.vendor.happy_flow import run_test as run_vendor_happy_flow


def main():
    parser = argparse.ArgumentParser(description="Run automated tests")
    parser.add_argument(
        "--url",
        required=False,
        default="http://localhost:8080",
        help="Target base URL (e.g., http://localhost:8080). Defaults to localhost if omitted.",
    )
    args = parser.parse_args()
    run_executive_happy_flow(args.url)
    run_operator_happy_flow(args.url)
    run_vendor_happy_flow(args.url)


if __name__ == "__main__":
    main()
