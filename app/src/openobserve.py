"""
OpenObserve Logging Client.

This module provides utilities for sending event logs to an OpenObserve instance
via HTTP POST requests using Basic Authentication.

Features:
    - Encodes credentials in Base64 for authentication.
    - Constructs the OpenObserve endpoint URL dynamically from constants.
    - Sends structured JSON logs to the OpenObserve stream.
"""

import base64, json, requests
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

# Prepare Basic Auth credentials
credentials = base64.b64encode(
    f"{OPENOBSERVE_USERNAME}:{OPENOBSERVE_PASSWORD}".encode("utf-8")
).decode("utf-8")

# Default headers for all requests
headers = {"Content-Type": "application/json", "Authorization": f"Basic {credentials}"}

# Construct OpenObserve endpoint URL
openobserve_host = f"{OPENOBSERVE_PROTOCOL}://{OPENOBSERVE_HOST}:{OPENOBSERVE_PORT}"
openobserve_url = f"{openobserve_host}/api/{OPENOBSERVE_ORG}/{OPENOBSERVE_STREAM}/_json"


def post_log_event(event_data: dict) -> Response:
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
                    "_app_id": "EXECUTIVE",
                    "_executive_id": "1"
                }

    Returns:
        requests.Response: The HTTP response object returned by the OpenObserve API.
    """
    return requests.post(openobserve_url, headers=headers, data=json.dumps(event_data))
