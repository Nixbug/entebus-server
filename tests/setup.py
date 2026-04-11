"""
Testing utilities.

This module provides functions for running tests for the EnteBus API.
"""

import argparse

from tests.api import executive_token, executive_role


def run_endpoint_test(base_url: str):
    """Run full endpoint tests."""
    print("Starting endpoint tests.")
    executive_token.run_endpoint_test(base_url)
    executive_role.run_endpoint_test(base_url)
    print("All tests completed.")


def run_db_migration_test(base_url: str):
    """Run database migration tests."""
    pass


# ---------------------------------------------------------------------------
## Setup test entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Testing utilities.")
    subparsers = parser.add_subparsers(
        dest="group", required=True, help="Command group"
    )

    test_parser = subparsers.add_parser("test", help="Test commands")
    test_subparsers = test_parser.add_subparsers(
        dest="command", required=True, help="Test command"
    )

    # API endpoint tests
    api_sp = test_subparsers.add_parser("api", help="Run API endpoint tests")
    api_sp.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8080",
        help="Base URL for API endpoints",
    )

    # Migration tests
    migration_sp = test_subparsers.add_parser(
        "migration", help="Run DB migration tests"
    )
    migration_sp.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000",
        help="Base URL for API endpoints",
    )

    args = parser.parse_args()

    if args.group == "test":
        if args.command == "api":
            run_endpoint_test(args.base_url)
        elif args.command == "migration":
            run_db_migration_test(args.base_url)


if __name__ == "__main__":
    main()
