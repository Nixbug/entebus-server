"""
This module provides input data for tests that are commonly used across FastAPI routes.
"""

from app.src.enums import GrantType, PlatformType


VALID_EXECUTIVE_CREDENTIALS = {
    "admin": {
        "username": "admin",
        "password": "password",
        "client_details": "client_details",
        "platform_type": PlatformType.WEB,
        "grant_type": GrantType.PASSWORD,
    },
    "guest": {
        "username": "guest",
        "password": "password",
        "client_details": "client_details",
        "platform_type": PlatformType.WEB,
        "grant_type": GrantType.PASSWORD,
    },
}

INVALID_EXECUTIVE_CREDENTIALS = {
    "wrong_credentials": {
        "username": "admin",
        "password": "wrong_password",
        "platform_type": PlatformType.WEB,
        "grant_type": GrantType.PASSWORD,
    },
    "empty_credentials": {
        "username": "",
        "password": "",
        "platform_type": PlatformType.WEB,
        "grant_type": GrantType.PASSWORD,
    },
    "wrong_grant_type": {
        "username": "admin",
        "password": "password",
        "platform_type": PlatformType.WEB,
        "grant_type": "invalid_grant",
    },
    "wrong_platform_type": {
        "username": "admin",
        "password": "password",
        "platform_type": "invalid_platform",
        "grant_type": GrantType.PASSWORD,
    },
}


GUEST_PERMISSIONS = {
    "landmark": {
        "create": False,
        "update": False,
        "delete": False,
        "bus_stop": {"create": False, "update": False, "delete": False},
    },
    "fare": {"create": False, "update": False, "delete": False},
    "executive": {
        "create": False,
        "update": False,
        "delete": False,
        "role": {"create": False, "update": False, "delete": False},
        "token": {"fetch": False, "delete": False},
    },
    "business": {
        "create": False,
        "update": False,
        "delete": False,
        "vendor": {
            "create": False,
            "update": False,
            "delete": False,
            "role": {"create": False, "update": False, "delete": False},
            "token": {"fetch": False, "delete": False},
        },
    },
    "company": {
        "create": False,
        "update": False,
        "delete": False,
        "bus": {"create": False, "update": False, "delete": False},
        "fare": {"create": False, "update": False, "delete": False},
        "route": {"create": False, "update": False, "delete": False},
        "operator": {
            "create": False,
            "update": False,
            "delete": False,
            "role": {"create": False, "update": False, "delete": False},
            "token": {"fetch": False, "delete": False},
        },
        "service": {
            "create": False,
            "update": False,
            "delete": False,
            "duty": {"create": False, "update": False, "delete": False},
        },
    },
}


ADMIN_PERMISSIONS = {
    "landmark": {
        "create": True,
        "update": True,
        "delete": True,
        "bus_stop": {"create": True, "update": True, "delete": True},
    },
    "fare": {"create": True, "update": True, "delete": True},
    "executive": {
        "create": True,
        "update": True,
        "delete": True,
        "role": {"create": True, "update": True, "delete": True},
        "token": {"fetch": True, "delete": True},
    },
    "business": {
        "create": True,
        "update": True,
        "delete": True,
        "vendor": {
            "create": True,
            "update": True,
            "delete": True,
            "role": {"create": True, "update": True, "delete": True},
            "token": {"fetch": True, "delete": True},
        },
    },
    "company": {
        "create": True,
        "update": True,
        "delete": True,
        "bus": {"create": True, "update": True, "delete": True},
        "fare": {"create": True, "update": True, "delete": True},
        "route": {"create": True, "update": True, "delete": True},
        "operator": {
            "create": True,
            "update": True,
            "delete": True,
            "role": {"create": True, "update": True, "delete": True},
            "token": {"fetch": True, "delete": True},
        },
        "service": {
            "create": True,
            "update": True,
            "delete": True,
            "duty": {"create": True, "update": True, "delete": True},
        },
    },
}


PARTIAL_PERMISSIONS = {
    "landmark": {
        "create": True,
        "update": False,
        "delete": False,
        "bus_stop": {"create": False, "update": False, "delete": False},
    },
    "fare": {"create": False, "update": False, "delete": False},
    "executive": {
        "create": False,
        "update": False,
        "delete": False,
        "role": {"create": False, "update": False, "delete": False},
        "token": {"fetch": True, "delete": False},
    },
    "business": {
        "create": False,
        "update": False,
        "delete": False,
        "vendor": {
            "create": False,
            "update": False,
            "delete": False,
            "role": {"create": False, "update": False, "delete": False},
            "token": {"fetch": False, "delete": False},
        },
    },
    "company": {
        "create": False,
        "update": False,
        "delete": False,
        "bus": {"create": False, "update": False, "delete": False},
        "fare": {"create": False, "update": False, "delete": False},
        "route": {"create": False, "update": False, "delete": False},
        "operator": {
            "create": False,
            "update": False,
            "delete": False,
            "role": {"create": False, "update": False, "delete": False},
            "token": {"fetch": False, "delete": False},
        },
        "service": {
            "create": False,
            "update": False,
            "delete": False,
            "duty": {"create": False, "update": False, "delete": False},
        },
    },
}
