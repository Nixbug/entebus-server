"""
OpenObserve Logging Utility.

This module provides helper functions for sending structured event logs
to an OpenObserve instance. It supports automatic log enrichment with
request metadata and user context, enabling consistent tracking of
API operations across applications.

These functions are primarily used for auditing, analytics,
and monitoring of API activities across different application contexts.
"""

import base64, json, requests
from typing import Union
from requests import Response

from app.src.constants import (
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
from app.src.enums import AppID

# Prepare Basic Auth credentials
credentials = base64.b64encode(
    f"{OPENOBSERVE_USERNAME}:{OPENOBSERVE_PASSWORD}".encode("utf-8")
).decode("utf-8")

# Default headers for all requests
headers = {"Content-Type": "application/json", "Authorization": f"Basic {credentials}"}

# Construct OpenObserve endpoint URL
openobserve_host = f"{OPENOBSERVE_PROTOCOL}://{OPENOBSERVE_HOST}:{OPENOBSERVE_PORT}"
openobserve_url = f"{openobserve_host}/api/{OPENOBSERVE_ORG}/{OPENOBSERVE_STREAM}/_json"


def _post_log_event(event_data: dict) -> Response:
    """
    Send an event log to the configured OpenObserve instance.

    This function serializes the given event data as JSON and sends it
    to the OpenObserve API using HTTP POST with Basic authentication.

    Args:
        eventData (dict): A dictionary representing the event log to be sent.
            Example:
                {
                    "_method": "POST",
                    "_path": "/api/v1/routes",
                    "_app_id": 1,
                    "_executive_id": 1,
                }

    Returns:
        requests.Response: The HTTP response object returned by the OpenObserve API.
    """
    try:
        response = requests.post(
            openobserve_url, headers=headers, data=json.dumps(event_data)
        )
        response.raise_for_status()
    except Exception:
        pass


def log_event(
    token: Union[ExecutiveToken, OperatorToken, VendorToken],
    request_info: RequestInfo,
    data: dict,
) -> None:
    """
    Log an event to OpenObserve with request and user context.

    Args:
        token (Union[ExecutiveToken, OperatorToken, VendorToken]): Authenticated user token.
        request_info (RequestInfo): Metadata about the current request.
        data (dict): Additional event-specific details to include in the log.

    Notes:
        - Automatically attaches `_app_id`, `_method`, `_path`, and user-specific ID.
        - User-specific key depends on the app:
            - Executive → `_executive_id`
            - Operator  → `_operator_id`
            - Vendor    → `_vendor_id`
    """
    log_details = {
        "_method": request_info.method,
        "_path": request_info.path,
        "_app_id": request_info.app_id,
    }

    if request_info.app_id == AppID.EXECUTIVE:
        log_details["_executive_id"] = token.executive_id
    if request_info.app_id == AppID.OPERATOR:
        log_details["_operator_id"] = token.operator_id
    if request_info.app_id == AppID.VENDOR:
        log_details["_vendor_id"] = token.vendor_id

    for key, value in data.items():
        if isinstance(value, (dict, list)):
            log_details[key] = json.dumps(value)
        else:
            log_details[key] = value

    _post_log_event(log_details)
