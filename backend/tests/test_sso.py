from unittest.mock import patch, MagicMock

import pytest

from backend import sso as sso_module
from backend import auth as auth_module
from backend.db import alert_incidents, alert_rules, users


def test_generate_pkce_pair_challenge_matches_verifier():
    import base64
    import hashlib

    verifier, challenge = sso_module.generate_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected


def test_start_login_raises_without_config(client, monkeypatch):
    monkeypatch.setattr(sso_module, "SSO_CLIENT_ID", "")
    with pytest.raises(RuntimeError):
        sso_module.start_login()


def test_start_login_stashes_verifier_and_builds_idp_url(client, monkeypatch):
    monkeypatch.setattr(sso_module, "SSO_CLIENT_ID", "test_client")
    monkeypatch.setattr(sso_module, "SSO_REDIRECT_URL", "http://example.com/callback")
    url = sso_module.start_login()
    assert "response_type=code" in url
    assert "client_id=test_client" in url
    assert "code_challenge_method=SHA256" in url
    assert sso_module.sso_states.count_documents({}) == 1


def test_exchange_code_for_profile_rejects_unknown_state(client):
    with pytest.raises(ValueError, match="Unknown or expired"):
        sso_module.exchange_code_for_profile("some_code", "nonexistent_state")


def test_exchange_code_for_profile_happy_path(client, monkeypatch):
    sso_module.sso_states.insert_one({"state": "s1", "code_verifier": "v1", "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})

    token_resp = MagicMock(ok=True)
    token_resp.json.return_value = {"access_token": "tok123"}
    validate_resp = MagicMock(ok=True)
    validate_resp.json.return_value = {"data": {"user": {"empcode": "E1", "empname": "Asha", "email": "asha@voicedesk.com"}}}

    with patch.object(sso_module.requests, "post", return_value=token_resp) as mock_post, \
         patch.object(sso_module.requests, "get", return_value=validate_resp) as mock_get:
        profile = sso_module.exchange_code_for_profile("code123", "s1")

    assert profile["empcode"] == "E1"
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["json"]["code_verifier"] == "v1"
    mock_get.assert_called_once()
    # state must be consumed — replay must fail
    with pytest.raises(ValueError, match="Unknown or expired"):
        sso_module.exchange_code_for_profile("code123", "s1")


def test_exchange_code_for_profile_raises_on_token_exchange_failure(client):
    sso_module.sso_states.insert_one({"state": "s2", "code_verifier": "v2", "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    token_resp = MagicMock(ok=False, status_code=400, text="bad request")
    with patch.object(sso_module.requests, "post", return_value=token_resp):
        with pytest.raises(ValueError, match="SSO token exchange failed"):
            sso_module.exchange_code_for_profile("code456", "s2")


def test_issue_token_for_sso_profile_upserts_user_and_mints_user_token():
    token = auth_module.issue_token_for_sso_profile({"empcode": "E2", "empname": "Ravi", "email": "ravi@voicedesk.com"})
    decoded = auth_module.jwt.decode(token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["sub"] == "ravi@voicedesk.com"
    assert decoded["role"] == "user"
    user = users.find_one({"email": "ravi@voicedesk.com"})
    assert user["sso_empcode"] == "E2"
    assert user["role"] == "user"


def test_issue_token_for_sso_profile_grants_admin_only_to_default_admin_email():
    token = auth_module.issue_token_for_sso_profile({"empcode": "E9", "email": auth_module.DEFAULT_ADMIN_EMAIL})
    decoded = auth_module.jwt.decode(token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["role"] == "admin"


def test_issue_token_for_sso_profile_does_not_reset_a_promoted_users_role():
    auth_module.issue_token_for_sso_profile({"empcode": "E5", "email": "promoted@voicedesk.com"})
    users.update_one({"email": "promoted@voicedesk.com"}, {"$set": {"role": "admin"}})

    token = auth_module.issue_token_for_sso_profile({"empcode": "E5", "email": "promoted@voicedesk.com"})
    decoded = auth_module.jwt.decode(token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["role"] == "admin"


def test_update_user_role_rejects_demoting_default_admin_even_by_another_admin(client, auth_headers):
    # A second admin (not the default admin) tries to demote admin@voicedesk.com.
    users.insert_one({"email": "second-admin@voicedesk.com", "password_hash": auth_module.hash_password("password"), "role": "admin"})
    resp = client.post("/api/auth/login", json={"email": "second-admin@voicedesk.com", "password": "password"})
    assert resp.status_code == 200, resp.text
    second_admin_headers = {"Authorization": f"Bearer {resp.json()['token']}"}

    resp = client.patch(
        f"/api/auth/users/{auth_module.DEFAULT_ADMIN_EMAIL}/role",
        json={"role": "user"},
        headers=second_admin_headers,
    )
    assert resp.status_code == 400
    assert users.find_one({"email": auth_module.DEFAULT_ADMIN_EMAIL})["role"] == "admin"


def test_update_user_role_allows_demoting_a_non_default_admin(client, auth_headers):
    users.insert_one({"email": "other-admin@voicedesk.com", "password_hash": auth_module.hash_password("password"), "role": "admin"})

    resp = client.patch(
        "/api/auth/users/other-admin@voicedesk.com/role",
        json={"role": "user"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert users.find_one({"email": "other-admin@voicedesk.com"})["role"] == "user"


def test_issue_token_for_sso_profile_falls_back_to_empcode_when_no_email():
    token = auth_module.issue_token_for_sso_profile({"empcode": "E3", "empname": "No Email"})
    decoded = auth_module.jwt.decode(token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["sub"] == "e3@voicedesk.com"


def test_sso_login_endpoint_redirects_to_idp(client, monkeypatch):
    monkeypatch.setattr(sso_module, "SSO_CLIENT_ID", "test_client")
    monkeypatch.setattr(sso_module, "SSO_REDIRECT_URL", "http://example.com/callback")
    resp = client.get("/api/auth/sso/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "accounts.voicedesk.com" in resp.headers["location"]


def test_sso_login_endpoint_500_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(sso_module, "SSO_CLIENT_ID", "")
    resp = client.get("/api/auth/sso/login", follow_redirects=False)
    assert resp.status_code == 500


def test_sso_callback_endpoint_redirects_to_frontend_with_token(client, monkeypatch):
    sso_module.sso_states.insert_one({"state": "cb1", "code_verifier": "v1", "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    token_resp = MagicMock(ok=True)
    token_resp.json.return_value = {"access_token": "tok"}
    validate_resp = MagicMock(ok=True)
    validate_resp.json.return_value = {"data": {"user": {"empcode": "E4", "email": "e4@voicedesk.com"}}}

    with patch.object(sso_module.requests, "post", return_value=token_resp), \
         patch.object(sso_module.requests, "get", return_value=validate_resp):
        resp = client.get("/api/auth/sso/callback?code=abc&state=cb1", follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert "sso_token=" in resp.headers["location"]


def test_sso_callback_endpoint_redirects_to_frontend_with_error_on_bad_state(client):
    resp = client.get("/api/auth/sso/callback?code=abc&state=nonexistent", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "sso_error=" in resp.headers["location"]


def test_delete_user_rejects_deleting_default_admin_even_by_another_admin(client, auth_headers):
    # A second admin (not the default admin) tries to delete admin@voicedesk.com.
    users.insert_one({"email": "third-admin@voicedesk.com", "password_hash": auth_module.hash_password("password"), "role": "admin"})
    resp = client.post("/api/auth/login", json={"email": "third-admin@voicedesk.com", "password": "password"})
    assert resp.status_code == 200, resp.text
    third_admin_headers = {"Authorization": f"Bearer {resp.json()['token']}"}

    resp = client.delete(
        f"/api/auth/users/{auth_module.DEFAULT_ADMIN_EMAIL}",
        headers=third_admin_headers,
    )
    assert resp.status_code == 400
    assert users.find_one({"email": auth_module.DEFAULT_ADMIN_EMAIL}) is not None


def test_delete_user_removes_their_alert_rules_and_incidents(client, auth_headers):
    users.insert_one({"email": "rule-owner@voicedesk.com", "password_hash": auth_module.hash_password("password"), "role": "user"})
    rule_id = alert_rules.insert_one({
        "created_by": "rule-owner@voicedesk.com",
        "name": "test rule",
        "enabled": True,
    }).inserted_id
    alert_incidents.insert_one({"rule_id": str(rule_id), "status": "open"})

    resp = client.delete("/api/auth/users/rule-owner@voicedesk.com", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    assert users.find_one({"email": "rule-owner@voicedesk.com"}) is None
    assert alert_rules.find_one({"_id": rule_id}) is None
    assert alert_incidents.find_one({"rule_id": str(rule_id)}) is None


def test_sso_logout_url_endpoint(client):
    resp = client.get("/api/auth/sso/logout-url")
    assert resp.status_code == 200
    assert "logoutServiceAuth" in resp.json()["url"]


def _signup_and_login(client, email, password="password123"):
    resp = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_list_users_rejects_non_admin(client):
    plain_headers = _signup_and_login(client, "plain-lister@voicedesk.com")
    resp = client.get("/api/auth/users", headers=plain_headers)
    assert resp.status_code == 403


def test_list_users_returns_full_list_without_password_hash_and_with_is_sso(client, auth_headers):
    users.insert_one({
        "email": "local-user@voicedesk.com",
        "password_hash": auth_module.hash_password("password"),
        "role": "user",
    })
    users.insert_one({
        "email": "sso-user@voicedesk.com",
        "sso_empcode": "E42",
        "sso_empname": "SSO Person",
        "role": "user",
    })

    resp = client.get("/api/auth/users", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    emails = {u["email"] for u in body}
    assert "admin@voicedesk.com" in emails
    assert "local-user@voicedesk.com" in emails
    assert "sso-user@voicedesk.com" in emails

    for entry in body:
        assert "password_hash" not in entry

    by_email = {u["email"]: u for u in body}
    assert by_email["local-user@voicedesk.com"]["is_sso"] is False
    assert by_email["sso-user@voicedesk.com"]["is_sso"] is True


def test_update_user_role_rejects_self_demotion_by_non_default_admin(client):
    # A second admin (not admin@voicedesk.com) tries to demote themselves — this hits the
    # `email == admin["sub"]` guard, distinct from the DEFAULT_ADMIN_EMAIL-specific guard.
    users.insert_one({
        "email": "self-demoter@voicedesk.com",
        "password_hash": auth_module.hash_password("password"),
        "role": "admin",
    })
    resp = client.post("/api/auth/login", json={"email": "self-demoter@voicedesk.com", "password": "password"})
    assert resp.status_code == 200, resp.text
    self_headers = {"Authorization": f"Bearer {resp.json()['token']}"}

    resp = client.patch(
        "/api/auth/users/self-demoter@voicedesk.com/role",
        json={"role": "user"},
        headers=self_headers,
    )
    assert resp.status_code == 400
    assert users.find_one({"email": "self-demoter@voicedesk.com"})["role"] == "admin"


def test_update_user_role_rejects_non_admin(client):
    plain_headers = _signup_and_login(client, "plain-role-updater@voicedesk.com")
    users.insert_one({
        "email": "some-target@voicedesk.com",
        "password_hash": auth_module.hash_password("password"),
        "role": "user",
    })
    resp = client.patch(
        "/api/auth/users/some-target@voicedesk.com/role",
        json={"role": "admin"},
        headers=plain_headers,
    )
    assert resp.status_code == 403


def test_delete_user_rejects_non_admin(client):
    plain_headers = _signup_and_login(client, "plain-deleter@voicedesk.com")
    users.insert_one({"email": "delete-target@voicedesk.com", "password_hash": auth_module.hash_password("password"), "role": "user"})
    resp = client.delete("/api/auth/users/delete-target@voicedesk.com", headers=plain_headers)
    assert resp.status_code == 403


def test_role_promotion_takes_effect_on_stale_token_next_request(client, auth_headers):
    """require_user re-reads the role from the DB on every request instead of trusting the
    (up to 12h-old) JWT payload, so a promotion via the Accounts page takes effect on the
    very next request made with the original, still-valid token — no re-login required."""
    user_headers = _signup_and_login(client, "promote-me@voicedesk.com")

    # Confirm the original token is not yet admin-privileged.
    resp = client.get("/api/auth/users", headers=user_headers)
    assert resp.status_code == 403

    resp = client.patch(
        "/api/auth/users/promote-me@voicedesk.com/role",
        json={"role": "admin"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Same original token, no re-login — now succeeds because require_user re-read the role.
    resp = client.get("/api/auth/users", headers=user_headers)
    assert resp.status_code == 200, resp.text


def test_deleted_users_stale_token_is_rejected_on_next_request(client, auth_headers):
    """Mirror of the promotion test: require_user's live DB re-read also means a deleted
    account's still-valid token stops working on the very next request."""
    user_headers = _signup_and_login(client, "delete-me@voicedesk.com")

    resp = client.get("/api/auth/me", headers=user_headers)
    assert resp.status_code == 200, resp.text

    resp = client.delete("/api/auth/users/delete-me@voicedesk.com", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/auth/me", headers=user_headers)
    assert resp.status_code == 401
    assert "Account no longer exists" in resp.json()["detail"]
