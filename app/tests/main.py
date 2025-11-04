"""
Unit Test Runner for different domains.

This script serves as the main entry point for executing all unit test cases in sequential order.
It provides a simple CLI interface using argparse to trigger the tests sequentially.
"""

import argparse
from app.tests.unit import executive_token


def test():
    """Execute all test cases."""

    print("Starting all test cases.\n")

    # Run the tests
    executive_token.test()

    print("\nAll test cases finished successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run all unit test cases")
    parser.add_argument("-test", action="store_true", help="Run all test cases")
    args = parser.parse_args()

    if args.test:
        test()
