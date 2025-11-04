"""
HTTP Request Helpers for Unit Testing.

This module provides helper functions to simplify making HTTP requests
(POST, GET, PATCH, DELETE) during unit testing.
It also includes a utility to create authorization headers.
"""

import random, string
from requests import get, post, patch, delete
from http import HTTPStatus


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------
def random_string(length: int) -> str:
    """
    Generate a random string of specified length.

    Args:
        length (int): The desired length of the generated string.

    Returns:
        str: A randomly generated string containing uppercase and lowercase letters.
    """
    characters = string.ascii_letters
    return "".join(random.choices(characters, k=length))


# ---------------------------------------------------------------------------
# Header Creation
# ---------------------------------------------------------------------------
def make_header(response) -> dict:
    """
    Create an authorization header from a login response.

    Args:
        response: The HTTP response object returned after a successful login.

    Returns:
        dict: A header dictionary containing the Bearer token.
    """
    tokenData = response.json()
    return {
        "Authorization": f"Bearer {tokenData['access_token']}",
    }


# ---------------------------------------------------------------------------
# HTTP Methods
# ---------------------------------------------------------------------------
def POST(URL: str, header: dict = {}, status_code: int = HTTPStatus.CREATED, **kwargs):
    """
    Send an HTTP POST request and validate the response status.

    Args:
        URL (str): The endpoint URL to send the request to.
        header (dict, optional): Request headers. Defaults to {}.
        status_code (int, optional): Expected HTTP status code. Defaults to 201 (Created).
        **kwargs: Additional arguments passed to `requests.post()`.

    Returns:
        Response: The HTTP response object, if successful.
    """
    response = post(URL, headers=header, **kwargs)
    if response.status_code != status_code:
        print("{} != {}".format(response.status_code, status_code))
        print(response.json())
        assert response.status_code == status_code
    else:
        print("POST request successful")
        print(f"{response.json()}\n")
        return response


def GET(URL: str, header: dict = {}, status_code: int = HTTPStatus.OK, **kwargs):
    """
    Send an HTTP GET request and validate the response status.

    Args:
        URL (str): The endpoint URL to send the request to.
        header (dict, optional): Request headers. Defaults to {}.
        status_code (int, optional): Expected HTTP status code. Defaults to 200 (OK).
        **kwargs: Additional arguments passed to `requests.get()`.

    Returns:
        Response: The HTTP response object, if successful.
    """
    response = get(URL, headers=header, **kwargs)
    if response.status_code != status_code:
        print("{} != {} ".format(response.status_code, status_code))
        print(response.json())
        assert response.status_code == status_code
    else:
        print("GET request successful")
        print(f"{response.json()}\n")
        return response


def PATCH(URL: str, header: dict = {}, status_code: int = HTTPStatus.CREATED, **kwargs):
    """
    Send an HTTP PATCH request and validate the response status.

    Args:
        URL (str): The endpoint URL to send the request to.
        header (dict, optional): Request headers. Defaults to {}.
        status_code (int, optional): Expected HTTP status code. Defaults to 201 (Created).
        **kwargs: Additional arguments passed to `requests.patch()`.

    Returns:
        Response: The HTTP response object, if successful.
    """
    response = patch(URL, headers=header, **kwargs)
    if response.status_code != status_code:
        print("{} != {} ".format(response.status_code, status_code))
        print(response.json())
        assert response.status_code == status_code
    else:
        print("PATCH request successful")
        print(f"{response.json()}\n")
        return response


def DELETE(
    URL: str, header: dict = {}, status_code: int = HTTPStatus.NO_CONTENT, **kwargs
):
    """
    Send an HTTP DELETE request and validate the response status.

    Args:
        URL (str): The endpoint URL to send the request to.
        header (dict, optional): Request headers. Defaults to {}.
        status_code (int, optional): Expected HTTP status code. Defaults to 204 (No Content).
        **kwargs: Additional arguments passed to `requests.delete()`.

    Returns:
        Response: The HTTP response object, if successful.
    """
    response = delete(URL, headers=header, **kwargs)
    if response.status_code != status_code:
        print("{} != {} ".format(response.status_code, status_code))
        print(response.json())
        assert response.status_code == status_code
    else:
        print("DELETE request successful \n")
        return response
