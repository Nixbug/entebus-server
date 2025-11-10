"""
Authentication Schemes for EnteBus API.

This module defines OAuth2 and HTTP Bearer authentication schemes
for various user roles within the EnteBus system.

Each authentication scheme is designed to enforce role-based access control
for protected API endpoints.
"""

from fastapi.security import HTTPBearer, OAuth2PasswordBearer

# OAuth2 Password Bearer scheme for Executive users
oauth2_executive = OAuth2PasswordBearer(
    tokenUrl="entebus/account/token",
    refreshUrl="entebus/account/token/refresh",
    scheme_name="ExecutiveOAuth2",
    description="OAuth2 Password Bearer for Executive APIs",
)
# HTTP Bearer scheme for Vendor users
bearer_vendor = HTTPBearer(
    scheme_name="VendorBearer", description="HTTP Bearer for Vendor APIs"
)
# HTTP Bearer scheme for Operator users
bearer_operator = HTTPBearer(
    scheme_name="OperatorBearer", description="HTTP Bearer for Operator APIs"
)
