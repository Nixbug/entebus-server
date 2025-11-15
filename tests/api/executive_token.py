import requests

from app.src.enums import GrantType
from app.src.urls import URL_EXECUTIVE_TOKEN
from app.src.constants import MAX_EXECUTIVE_TOKENS
from tests.src.inputs import (
    VALID_EXECUTIVE_CREDENTIALS,
    INVALID_EXECUTIVE_CREDENTIALS,
)


def run_endpoint_test(base_url: str):
    print("\n=== EXECUTIVE TOKEN TESTS STARTED ===")

    base = f"{base_url}/executive{URL_EXECUTIVE_TOKEN}"

    # -------------------------------------------------------------
    print("CASE 1: Generate access token with valid credentials → 200")
    resp = requests.post(base, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    assert resp.status_code == 200, "Expected 200 for valid login"
    ADMIN_REFRESH = resp.json()["refresh_token"]

    # -------------------------------------------------------------
    print("CASE 2: Renew token using refresh token → 200")
    resp = requests.post(
        f"{base}/refresh",
        data={"grant_type": GrantType.REFRESH_TOKEN, "refresh_token": ADMIN_REFRESH},
    )
    assert resp.status_code == 200, "Expected 200 on valid refresh"
    ADMIN_ACCESS = resp.json()["access_token"]
    ADMIN_HEADER = {"Authorization": f"Bearer {ADMIN_ACCESS}"}

    # -------------------------------------------------------------
    print("CASE 3: Fetch all tokens as admin → 200")
    resp = requests.get(base, headers=ADMIN_HEADER)
    assert resp.status_code == 200, "Admin should fetch all tokens"

    # -------------------------------------------------------------
    print("CASE 4: Revoke own token using access token → 200")
    resp = requests.post(
        f"{base}/revoke", headers=ADMIN_HEADER, data={"token": ADMIN_ACCESS}
    )
    assert resp.status_code == 200, "Expected 200 on self revoke"

    # -------------------------------------------------------------
    print("CASE 5: Login with invalid credentials → 401")
    resp = requests.post(base, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_credentials"])
    assert resp.status_code == 401, "Expected 401 on invalid credentials"

    # -------------------------------------------------------------
    print("CASE 6: Login with missing required fields → 422")
    resp = requests.post(base, data={})
    assert resp.status_code == 422, "Expected 422 on missing fields"

    # -------------------------------------------------------------
    print("CASE 7: Login using wrong grant_type (refresh_token) → 422")
    resp = requests.post(
        base,
        data={
            "username": "admin",
            "password": "password",
            "grant_type": GrantType.REFRESH_TOKEN,
        },
    )
    assert resp.status_code == 422, "Expected 422 on wrong grant_type"

    # -------------------------------------------------------------
    print("CASE 8: Login with missing/empty grant_type → 406")
    resp = requests.post(
        base,
        data={
            "username": "admin",
            "password": "password",
        },
    )
    assert resp.status_code == 406, "Expected 406 on empty grant_type"

    # -------------------------------------------------------------
    print("CASE 9: Login using invalid grant_type → 422")
    resp = requests.post(base, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_grant_type"])
    assert resp.status_code == 422, "Expected 422 on invalid grant_type"

    # -------------------------------------------------------------
    print("CASE 10: Login using invalid platform_type → 422")
    resp = requests.post(
        base, data=INVALID_EXECUTIVE_CREDENTIALS["wrong_platform_type"]
    )
    assert resp.status_code == 422, "Expected 422 on invalid platform_type"

    # -------------------------------------------------------------
    print("CASE 11: Refresh using wrong grant_type (password) → 406")
    resp = requests.post(base, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    assert resp.status_code == 200, "Guest login should succeed"
    GUEST_ACCESS = resp.json()["access_token"]
    GUEST_REFRESH = resp.json()["refresh_token"]
    GUEST_HEADER = {"Authorization": f"Bearer {GUEST_ACCESS}"}
    GUEST_ID = resp.json()["id"]
    resp = requests.post(
        f"{base}/refresh",
        data={
            "refresh_token": GUEST_REFRESH,
            "grant_type": GrantType.PASSWORD,
        },
    )
    assert resp.status_code == 406, "Expected 406 on wrong grant_type"

    # -------------------------------------------------------------
    print("CASE 12: Guest revokes own token using access token → 204")
    resp = requests.delete(f"{base}/{GUEST_ID}", headers=GUEST_HEADER)
    assert resp.status_code == 204

    # -------------------------------------------------------------
    print("CASE 13: Refresh using revoked refresh token → 401")
    resp = requests.post(
        f"{base}/refresh",
        data={"grant_type": GrantType.REFRESH_TOKEN, "refresh_token": GUEST_REFRESH},
    )
    assert resp.status_code == 401

    # -------------------------------------------------------------
    print("CASE 14: Refresh using invalid refresh token → 401")
    resp = requests.post(
        f"{base}/refresh",
        data={"grant_type": GrantType.REFRESH_TOKEN, "refresh_token": "invalid"},
    )
    assert resp.status_code == 401

    # -------------------------------------------------------------
    print("CASE 15: Guest token rotation (simulate multiple logins)")
    for i in range(MAX_EXECUTIVE_TOKENS + 1):
        resp = requests.post(base, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
        assert resp.status_code == 200, f"Login #{i+1} failed"
    GUEST_HEADER = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    GUEST_EX_ID = resp.json()["executive_id"]
    GUEST_ID = resp.json()["id"]
    GUEST_ACCESS = resp.json()["access_token"]
    GUEST_REFRESH = resp.json()["refresh_token"]
    resp = requests.get(
        base, headers=GUEST_HEADER, params={"executive_id": GUEST_EX_ID}
    )
    assert resp.status_code == 200, "Admin fetch tokens failed"
    tokens = resp.json()
    assert (
        len(tokens) <= MAX_EXECUTIVE_TOKENS
    ), f"Expected ≤ {MAX_EXECUTIVE_TOKENS} tokens, got {len(tokens)}"

    # -------------------------------------------------------------
    print("CASE 16: Guest revokes own token using refresh token → 200")
    resp = requests.post(
        f"{base}/revoke",
        headers=GUEST_HEADER,
        data={"token": GUEST_REFRESH},
    )
    assert resp.status_code == 200
    resp = requests.post(base, data=VALID_EXECUTIVE_CREDENTIALS["guest"])
    GUEST_ACCESS = resp.json()["access_token"]
    GUEST_HEADER = {"Authorization": f"Bearer {GUEST_ACCESS}"}
    GUEST_ID_2 = resp.json()["id"]
    GUEST_EX_ID = resp.json()["executive_id"]
    resp = requests.get(base, headers=GUEST_HEADER, params={"id": GUEST_ID})
    assert resp.json() == []
    assert resp.status_code == 200

    # -------------------------------------------------------------
    print("CASE 17: Guest revokes another owned token via ID → 204")
    resp = requests.get(
        base, headers=GUEST_HEADER, params={"executive_id": GUEST_EX_ID}
    )
    tokens = resp.json()
    ID = tokens[-1]["id"]
    resp = requests.delete(f"{base}/{ID}", headers=GUEST_HEADER)
    assert resp.status_code == 204
    resp = requests.get(base, headers=GUEST_HEADER, params={"id": ID})
    assert resp.json() == []
    assert resp.status_code == 200

    # -------------------------------------------------------------
    print("CASE 18: Fetch own tokens without Authorization header → 401")
    resp = requests.get(base)
    assert resp.status_code == 401

    # -------------------------------------------------------------
    print("CASE 19: Revoke token without Authorization header → 401")
    resp = requests.post(f"{base}/revoke")
    assert resp.status_code == 401

    # -------------------------------------------------------------
    print("CASE 20: Admin fetches guest token details → 200")
    resp = requests.post(base, data=VALID_EXECUTIVE_CREDENTIALS["admin"])
    ADMIN_ACCESS = resp.json()["access_token"]
    ADMIN_REFRESH = resp.json()["refresh_token"]
    ADMIN_HEADER = {"Authorization": f"Bearer {ADMIN_ACCESS}"}
    ADMIN_EX_ID = resp.json()["executive_id"]
    ADMIN_ID = resp.json()["id"]
    resp = requests.get(
        base, headers=ADMIN_HEADER, params={"executive_id": GUEST_EX_ID}
    )
    assert resp.json() != []
    assert resp.status_code == 200

    # -------------------------------------------------------------
    print("CASE 21: Guest attempts to fetch admin token details → 200")
    resp = requests.get(
        base, headers=GUEST_HEADER, params={"executive_id": ADMIN_EX_ID}
    )
    assert resp.json() == []
    assert resp.status_code == 200

    # -------------------------------------------------------------
    print("CASE 22: Admin fetches token with id=0 → 200")
    resp = requests.get(base, headers=ADMIN_HEADER, params={"id": 0})
    assert resp.json() == []
    assert resp.status_code == 200

    # -------------------------------------------------------------
    print("CASE 23: Guest attempts to revoke admin token using ID → 403")
    resp = requests.delete(f"{base}/{ADMIN_ID}", headers=GUEST_HEADER)
    assert resp.status_code == 403

    # -------------------------------------------------------------
    print("CASE 24: Guest attempts to revoke admin token using access token → 200")
    resp = requests.post(
        f"{base}/revoke", headers=GUEST_HEADER, data={"token": ADMIN_ACCESS}
    )
    assert resp.status_code == 200
    resp = requests.get(base, headers=ADMIN_HEADER, params={"id": ADMIN_ID})
    assert resp.json() != []
    assert resp.status_code == 200

    # -------------------------------------------------------------
    print("CASE 25: Admin deletes token using id=0 → 204")
    resp = requests.delete(f"{base}/0", headers=ADMIN_HEADER)
    assert resp.status_code == 204

    # -------------------------------------------------------------
    print("CASE 26: Admin revokes guest access token → 200")
    resp = requests.post(
        f"{base}/revoke", headers=ADMIN_HEADER, data={"token": GUEST_ACCESS}
    )
    assert resp.status_code == 200
    resp = requests.get(base, headers=ADMIN_HEADER, params={"id": GUEST_ID_2})
    assert resp.json() != []
    assert resp.status_code == 200

    # -------------------------------------------------------------
    print("CASE 27: Authenticate using invalid bearer token type → 401")
    resp = requests.get(
        base, headers={"Authorization": f"Bearer Invalid {ADMIN_ACCESS}"}
    )
    assert resp.status_code == 401

    # -------------------------------------------------------------
    print("CASE 28: Admin revokes all guest tokens using token IDs → 204")
    resp = requests.get(
        base, headers=ADMIN_HEADER, params={"executive_id": GUEST_EX_ID}
    )
    tokens = resp.json()
    for token in tokens:
        ID = token["id"]
        resp = requests.delete(f"{base}/{ID}", headers=ADMIN_HEADER)
        assert resp.status_code == 204

    # -------------------------------------------------------------
    print("CASE 29: Admin deletes guest token using its ID → 204")
    resp = requests.delete(f"{base}/{ADMIN_ID}", headers=ADMIN_HEADER)
    assert resp.status_code == 204

    print("=== EXECUTIVE TOKEN TESTS COMPLETED ===\n")
