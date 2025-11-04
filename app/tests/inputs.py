"""
Test Input Data and Utility Functions.

This module provides:
- Base URLs for different FastAPI application domains (executive, vendor, operator).
- Common test credentials for authentication.
- Helper utilities test data creation.
"""



# ---------------------------------------------------------------------------
# Base URLs for different user domains
# ---------------------------------------------------------------------------
EXECUTIVE_BASE_URL = "http://127.0.0.1:8080/executive"
VENDOR_BASE_URL = "http://127.0.0.1:8080/vendor"
OPERATOR_BASE_URL = "http://127.0.0.1:8080/operator"


# ---------------------------------------------------------------------------
# Credentials for authentication in test cases
# ---------------------------------------------------------------------------
class ExecutiveCredential:
    """Predefined credentials for Executive users for testing."""

    admin = {"username": "admin", "password": "password"}
    guest = {"username": "guest", "password": "password"}
