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
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Endpoint tests
    endpoint_sp = subparsers.add_parser("test_endpoints", help="Run endpoint tests")
    endpoint_sp.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8080",
        help="Base URL for API endpoints",
    )

    # Migration tests
    migration_sp = subparsers.add_parser(
        "test_migration", help="Run DB migration tests"
    )
    migration_sp.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000/api",
        help="Base URL for API endpoints",
    )
    args = parser.parse_args()

    if args.command == "test_endpoints":
        run_endpoint_test(args.base_url)
    elif args.command == "test_migration":
        run_db_migration_test(args.base_url)


if __name__ == "__main__":
    main()
