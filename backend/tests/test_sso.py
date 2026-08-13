from unittest.mock import patch, MagicMock

import pytest

from backend import sso as sso_module
from backend import auth as auth_module
from backend.db import users


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
    validate_resp.json.return_value = {"data": {"user": {"empcode": "E1", "empname": "Asha", "email": "asha@justdial.com"}}}

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
    token = auth_module.issue_token_for_sso_profile({"empcode": "E2", "empname": "Ravi", "email": "ravi@justdial.com"})
    decoded = auth_module.jwt.decode(token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["sub"] == "ravi@justdial.com"
    assert decoded["role"] == "user"
    user = users.find_one({"email": "ravi@justdial.com"})
    assert user["sso_empcode"] == "E2"
    assert user["role"] == "user"


def test_issue_token_for_sso_profile_grants_admin_only_to_default_admin_email():
    token = auth_module.issue_token_for_sso_profile({"empcode": "E9", "email": auth_module.DEFAULT_ADMIN_EMAIL})
    decoded = auth_module.jwt.decode(token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["role"] == "admin"


def test_issue_token_for_sso_profile_does_not_reset_a_promoted_users_role():
    auth_module.issue_token_for_sso_profile({"empcode": "E5", "email": "promoted@justdial.com"})
    users.update_one({"email": "promoted@justdial.com"}, {"$set": {"role": "admin"}})

    token = auth_module.issue_token_for_sso_profile({"empcode": "E5", "email": "promoted@justdial.com"})
    decoded = auth_module.jwt.decode(token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["role"] == "admin"


def test_issue_token_for_sso_profile_falls_back_to_empcode_when_no_email():
    token = auth_module.issue_token_for_sso_profile({"empcode": "E3", "empname": "No Email"})
    decoded = auth_module.jwt.decode(token, auth_module.JWT_SECRET, algorithms=[auth_module.JWT_ALGO])
    assert decoded["sub"] == "e3@justdial.com"


def test_sso_login_endpoint_redirects_to_idp(client, monkeypatch):
    monkeypatch.setattr(sso_module, "SSO_CLIENT_ID", "test_client")
    monkeypatch.setattr(sso_module, "SSO_REDIRECT_URL", "http://example.com/callback")
    resp = client.get("/api/auth/sso/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "accounts.justdial.com" in resp.headers["location"]


def test_sso_login_endpoint_500_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(sso_module, "SSO_CLIENT_ID", "")
    resp = client.get("/api/auth/sso/login", follow_redirects=False)
    assert resp.status_code == 500


def test_sso_callback_endpoint_redirects_to_frontend_with_token(client, monkeypatch):
    sso_module.sso_states.insert_one({"state": "cb1", "code_verifier": "v1", "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    token_resp = MagicMock(ok=True)
    token_resp.json.return_value = {"access_token": "tok"}
    validate_resp = MagicMock(ok=True)
    validate_resp.json.return_value = {"data": {"user": {"empcode": "E4", "email": "e4@justdial.com"}}}

    with patch.object(sso_module.requests, "post", return_value=token_resp), \
         patch.object(sso_module.requests, "get", return_value=validate_resp):
        resp = client.get("/api/auth/sso/callback?code=abc&state=cb1", follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert "sso_token=" in resp.headers["location"]


def test_sso_callback_endpoint_redirects_to_frontend_with_error_on_bad_state(client):
    resp = client.get("/api/auth/sso/callback?code=abc&state=nonexistent", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "sso_error=" in resp.headers["location"]


def test_sso_logout_url_endpoint(client):
    resp = client.get("/api/auth/sso/logout-url")
    assert resp.status_code == 200
    assert "logoutServiceAuth" in resp.json()["url"]
