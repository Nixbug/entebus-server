"""
HTTP Bearer authentication schemes for different user roles.

This module defines FastAPI `HTTPBearer` security schemes for use in route
authentication. Each scheme corresponds to a specific user role, allowing
role-based access control in API endpoints.
"""

from fastapi.security import HTTPBearer

# Define HTTP Bearer authentication schemes for different user roles
bearer_executive = HTTPBearer(
    scheme_name="ExecutiveBearer", description="HTTP Bearer token for Executive APIs"
)
bearer_vendor = HTTPBearer(
    scheme_name="VendorBearer", description="HTTP Bearer token for Vendor APIs"
)
bearer_operator = HTTPBearer(
    scheme_name="OperatorBearer", description="HTTP Bearer token for Operator APIs"
)
