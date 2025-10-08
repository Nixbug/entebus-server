"""
HTTP Bearer authentication schemes for different user roles.

This module defines FastAPI `HTTPBearer` security schemes for use in route
authentication. Each scheme corresponds to a specific user role, allowing
role-based access control in API endpoints.

Defined schemes:
- `bearer_executive`: Authentication for executive users.
- `bearer_vendor`: Authentication for vendor users.
- `bearer_operator`: Authentication for operator users.

"""

from fastapi.security import HTTPBearer

# Define HTTP Bearer authentication schemes for different user roles
bearer_executive = HTTPBearer(scheme_name="Executive HTTPBearer")
bearer_vendor = HTTPBearer(scheme_name="Vendor HTTPBearer")
bearer_operator = HTTPBearer(scheme_name="Operator HTTPBearer")
