"""
Event Logging Utility for OpenObserve.

This module provides helper functions to log structured events to OpenObserve,
enriching them with request metadata and user context (Executive, Operator, or Vendor).

Features:
    - Adds common request metadata: `_method`, `_path`, `_app_id`
    - Dynamically attaches user-specific IDs based on the authenticated token
    - Provides a generic `log_event` function and specialized wrappers
"""

from typing import Union

from app.src import openobserve
from app.src.enums import AppID
from app.src.db import ExecutiveToken
from app.src.schemas import RequestInfo


def log_event(
    token: Union[ExecutiveToken],
    request_info: RequestInfo,
    data: dict,
) -> None:
    """
    Log an event to OpenObserve with request and user context.

    Args:
        token (Union[ExecutiveToken, OperatorToken, VendorToken]): Authenticated user token.
        requestInfo (RequestInfo): Metadata about the current request.
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

    if request_info.app_id == AppID.EXECUTIVE and isinstance(token, ExecutiveToken):
        log_details["_executive_id"] = token.executive_id

    log_details.update(data)
    openobserve.post_log_event(log_details)


# Convenience wrappers (optional — use only if you want explicit naming in routes)
def log_executive_event(
    token: ExecutiveToken, request_info: RequestInfo, data: dict
) -> None:
    """
    Convenience wrapper to log events specifically for Executive context.

    Args:
        token (ExecutiveToken): Authenticated executive token.
        request_info (RequestInfo): Request metadata (method, path, app ID).
        data (dict): Additional event details.
    Returns:
        None
    """
    log_event(token, request_info, data)
