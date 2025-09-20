from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

password_hasher = PasswordHasher(encoding="utf-8")


def make_password(password: str) -> str:
    """
    Hash a plain-text password using Argon2.

    Args:
        password (str): The plain-text password to be hashed.

    Returns:
        str: The Argon2 hash of the given password.
    """
    return password_hasher.hash(password)


def check_password(password: str, actual_password: str) -> bool:
    """
    Verify a plain-text password against a stored Argon2 hash.

    Args:
        password (str): The plain-text password to check.
        actual_password (str): The stored Argon2 hash of the password.

    Returns:
        bool: True if the password matches the hash, False otherwise.
    """
    try:
        password_hasher.verify(actual_password, password)
        return True
    except VerifyMismatchError:
        return False
