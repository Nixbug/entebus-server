"""
HTTP Bearer authentication schemes for different user roles.

This module defines FastAPI `HTTPBearer` security schemes for use in route
authentication. Each scheme corresponds to a specific user role, allowing
role-based access control in API endpoints.
"""

from fastapi.security import HTTPBearer, OAuth2PasswordBearer

# Define HTTP Bearer authentication schemes for different user roles
oauth2_executive = OAuth2PasswordBearer(
    tokenUrl="entebus/account/token",
    refreshUrl="entebus/account/token/refresh",
    scheme_name="ExecutiveOAuth2",
    description="OAuth2 Password Bearer for Executive APIs",
)
bearer_vendor = HTTPBearer(
    scheme_name="VendorBearer", description="HTTP Bearer for Vendor APIs"
)
bearer_operator = HTTPBearer(
    scheme_name="OperatorBearer", description="HTTP Bearer for Operator APIs"
)
