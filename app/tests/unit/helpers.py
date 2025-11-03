from requests import get, post, patch, delete
from http import HTTPStatus


# Create header from login
def make_header(response) -> dict:
    tokenData = response.json()
    return {
        "Authorization": f"Bearer {tokenData['access_token']}",
    }


# ---------------------------------------------------------------------------
## HTTP Methods
# ---------------------------------------------------------------------------
def POST(URL: str, header: dict = {}, status_code: int = HTTPStatus.CREATED, **kwargs):
    response = post(URL, headers=header, **kwargs)
    if response.status_code != status_code:
        print("{} != {}".format(response.status_code, status_code))
        print(response.json())
        assert response.status_code == status_code
    else:
        print("POST request successful\n")
        return response


def GET(URL: str, header: dict = {}, status_code: int = HTTPStatus.OK, **kwargs):
    response = get(URL, headers=header, **kwargs)

    if response.status_code != status_code:
        print("{} != {} ".format(response.status_code, status_code))
        print(response.json())
        assert response.status_code == status_code
    else:
        print("GET request successful\n")
        return response


def PATCH(URL: str, header: dict = {}, status_code: int = HTTPStatus.CREATED, **kwargs):
    response = patch(URL, headers=header, **kwargs)

    if response.status_code != status_code:
        print("{} != {} ".format(response.status_code, status_code))
        print(response.json())
        assert response.status_code == status_code
    else:
        print("PATCH request successful\n")
        return response


def DELETE(
    URL: str, header: dict = {}, status_code: int = HTTPStatus.NO_CONTENT, **kwargs
):
    print("Testing DELETE {}".format(URL))
    response = delete(URL, headers=header, **kwargs)

    if response.status_code != status_code:
        print("{} != {} ".format(response.status_code, status_code))
        print(response.json())
        assert response.status_code == status_code
    else:
        print("DELETE request successful\n")
        return response
