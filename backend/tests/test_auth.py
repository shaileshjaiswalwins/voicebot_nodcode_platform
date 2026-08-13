def test_login_with_default_admin_succeeds(client):
    resp = client.post("/api/auth/login", json={"email": "admin@justdial.com", "password": "password"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@justdial.com"
    assert body["token"]


def test_login_with_wrong_password_returns_helpful_401(client):
    resp = client.post("/api/auth/login", json={"email": "admin@justdial.com", "password": "wrong"})
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.json()["detail"]


def test_login_with_unknown_email_returns_401_not_500(client):
    resp = client.post("/api/auth/login", json={"email": "nobody@justdial.com", "password": "password"})
    assert resp.status_code == 401


def test_protected_route_without_token_returns_401(client):
    resp = client.get("/api/bots")
    assert resp.status_code == 401
    assert "Missing bearer token" in resp.json()["detail"]


def test_protected_route_with_garbage_token_returns_401_not_500(client):
    resp = client.get("/api/bots", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401
    assert "Invalid or expired token" in resp.json()["detail"]


def test_health_check_is_public(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_signup_with_new_email_succeeds_and_returns_usable_token(client):
    from backend import auth as auth_module

    resp = client.post(
        "/api/auth/signup", json={"email": "new.user@justdial.com", "password": "password123"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "new.user@justdial.com"
    assert body["token"]

    decoded = auth_module.jwt.decode(body["token"], auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["sub"] == "new.user@justdial.com"
    assert decoded["role"] == "user"


def test_signup_with_duplicate_email_returns_409(client):
    resp1 = client.post("/api/auth/signup", json={"email": "dup@justdial.com", "password": "password123"})
    assert resp1.status_code == 200, resp1.text

    resp2 = client.post("/api/auth/signup", json={"email": "dup@justdial.com", "password": "password123"})
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"]


def test_signup_with_too_short_password_returns_422(client):
    resp = client.post(
        "/api/auth/signup", json={"email": "shortpw@justdial.com", "password": "short"}
    )
    assert resp.status_code == 422


def test_signup_then_login_with_different_case_email_succeeds(client):
    """The exact lockout scenario _normalize_email exists to prevent: sign up with mixed
    case, log in with different casing, and it must still resolve to the same account."""
    signup_resp = client.post(
        "/api/auth/signup", json={"email": "Foo@Bar.com", "password": "password123"}
    )
    assert signup_resp.status_code == 200, signup_resp.text

    login_resp = client.post("/api/auth/login", json={"email": "foo@bar.com", "password": "password123"})
    assert login_resp.status_code == 200, login_resp.text
    assert login_resp.json()["email"] == "foo@bar.com"

    login_resp_upper = client.post(
        "/api/auth/login", json={"email": "FOO@BAR.COM", "password": "password123"}
    )
    assert login_resp_upper.status_code == 200, login_resp_upper.text


def test_sso_login_and_password_signup_with_different_case_resolve_to_same_user(client):
    """SSO issue_token_for_sso_profile and password-based create_user must normalize to the
    same stored user document even if the casing differs between the two entry points."""
    from backend import auth as auth_module
    from backend.db import users

    signup_resp = client.post(
        "/api/auth/signup", json={"email": "Shared.User@JustDial.com", "password": "password123"}
    )
    assert signup_resp.status_code == 200, signup_resp.text

    sso_token = auth_module.issue_token_for_sso_profile(
        {"empcode": "E100", "empname": "Shared User", "email": "shared.user@justdial.com"}
    )
    decoded = auth_module.jwt.decode(sso_token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["sub"] == "shared.user@justdial.com"

    # Only one user document should exist for this address, and it should carry both the
    # password hash (from signup) and the SSO empcode (from the SSO login) — proof they
    # resolved to the same document rather than two separate ones due to case mismatch.
    matching = list(users.find({"email": "shared.user@justdial.com"}))
    assert len(matching) == 1
    assert matching[0].get("password_hash")
    assert matching[0].get("sso_empcode") == "E100"
