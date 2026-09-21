"""
OpenObserve Logging Utility.

This module provides helper functions for sending structured event logs
to an OpenObserve instance. It supports automatic log enrichment with
request metadata and user context, enabling consistent tracking of
API operations across applications.

These functions are primarily used for auditing, analytics,
and monitoring of API activities across different application contexts.
"""

import base64, json, logging, requests
from requests import Response

from app.src.constants import (
    LOGGING_TYPE,
    OPENOBSERVE_HOST,
    OPENOBSERVE_ORG,
    OPENOBSERVE_PASSWORD,
    OPENOBSERVE_PORT,
    OPENOBSERVE_PROTOCOL,
    OPENOBSERVE_STREAM,
    OPENOBSERVE_USERNAME,
)
from app.src.db import ExecutiveToken, OperatorToken, VendorToken
from app.src.schemas import RequestInfo

# ---------------------------------------------------------------------------
## OpenObserve Configuration
# ---------------------------------------------------------------------------
# Prepare Basic Auth credentials
credentials = base64.b64encode(
    f"{OPENOBSERVE_USERNAME}:{OPENOBSERVE_PASSWORD}".encode("utf-8")
).decode("utf-8")
# Default headers for all requests
headers = {"Content-Type": "application/json", "Authorization": f"Basic {credentials}"}
# Construct OpenObserve endpoint URL
openobserve_host = f"{OPENOBSERVE_PROTOCOL}://{OPENOBSERVE_HOST}:{OPENOBSERVE_PORT}"
openobserve_url = f"{openobserve_host}/api/{OPENOBSERVE_ORG}/{OPENOBSERVE_STREAM}/_json"

# Bind the correct logging function once at startup
if LOGGING_TYPE == "OPENOBSERVE":
    send_log = lambda e: _post_log_event(e)
elif LOGGING_TYPE == "CLOUD":
    send_log = lambda e: logging.info(json.dumps(e, default=str))
else:
    send_log = lambda e: print(e)


# ---------------------------------------------------------------------------
## Logging Functions
# ---------------------------------------------------------------------------
def _post_log_event(event_data: dict) -> Response | None:
    """
    Send an event log to the configured OpenObserve instance.

    This function serializes the given event data as JSON and sends it
    to the OpenObserve API using HTTP POST with Basic authentication.

    Args:
        event_data (dict): A dictionary representing the event log to be sent.
            Example:
                {
                    "_method": "POST",
                    "_path": "/api/v1/routes",
                    "_app_id": 1,
                    "_executive_id": 1,
                }

    Returns:
        Response | None: The HTTP response from OpenObserve if successful,
            or None if an error occurred during the request.
    """
    try:
        response = requests.post(
            openobserve_url, headers=headers, json=event_data, timeout=3
        )
        response.raise_for_status()
    except Exception:
        return None
    return response


def log_event(
    token: ExecutiveToken | OperatorToken | VendorToken,
    request_info: RequestInfo,
    data: dict,
) -> None:
    """
    Log an event to OpenObserve with request and user context.

    Args:
        token (ExecutiveToken | OperatorToken | VendorToken): Authenticated user token.
        request_info (RequestInfo): Metadata about the current request.
        data (dict): Additional event-specific details to include in the log.

    Notes:
        - Automatically attaches `_app_id`, `_method`, `_path`, and user-specific ID.
        - User-specific key depends on the app:
            - Executive → `_executive_id`
            - Operator  → `_operator_id`
            - Vendor    → `_vendor_id`
        - Log destination is controlled by `LOGGING_TYPE`:
            - `OPENOBSERVE` sends events to OpenObserve.
            - `CLOUD` emits JSON logs through Python logging.
            - Any other value logs to local console with print.
    """
    log_details = {
        "_method": request_info.method,
        "_path": request_info.path,
        "_app_id": request_info.app_id,
    }

    if isinstance(token, ExecutiveToken):
        log_details["_executive_id"] = token.executive_id
    elif isinstance(token, OperatorToken):
        log_details["_operator_id"] = token.operator_id
    elif isinstance(token, VendorToken):
        log_details["_vendor_id"] = token.vendor_id

    log_details.update(data)
    send_log(log_details)
