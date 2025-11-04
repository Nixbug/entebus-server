"""
Unit Test Runner.

This script serves as the main entry point for executing all unit test cases in sequential order.
It provides a simple CLI interface using argparse to trigger the tests sequentially.
"""

from app.tests.unit import executive_token


def run_test():
    """Execute all test cases."""

    # Run the tests
    executive_token.test()
