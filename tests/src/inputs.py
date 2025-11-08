from app.src.enums import GrantType, PlatformType


VALID_EXECUTIVE_CREDENTIALS = {
    "admin": {
        "username": "admin",
        "password": "password",
        "platform_type": PlatformType.WEB,
        "grant_type": GrantType.PASSWORD,
    },
    "guest": {
        "username": "guest",
        "password": "password",
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
