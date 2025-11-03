## Base URLs
import random, string


EXECUTIVE_BASE_URL = "http://127.0.0.1:8080//executive"
VENDOR_BASE_URL = "http://127.0.0.1:8080//vendor"
OPERATOR_BASE_URL = "http://127.0.0.1:8080//operator"


def random_string(length: int) -> str:
    characters = string.ascii_letters
    return "".join(random.choices(characters, k=length))


## Credentials
class ExecutiveCredential:
    admin = {"username": "admin", "password": "password"}
    guest = {"username": "guest", "password": "password"}
