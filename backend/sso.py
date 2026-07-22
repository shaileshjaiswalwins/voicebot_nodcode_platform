"""Justdial IAM SSO (OAuth2 + PKCE) client — ported from search_mis's SecurityController.php
per the integration doc (SSO_Integration_Reference.docx, section 3/7). Only the IdP contract
is reused (PKCE generation, the auth/token/validate-token calls); role derivation and session
shape are this platform's own, not search_mis's hardcoded rules.

Config comes from env vars — nothing is registered with the SSO/IAM team yet, so these are
placeholders until Suhail Saifi's team issues a client_id/client_secret for this system.
"""

import base64
import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import requests

from .db import db

SSO_IAM_BASE_URL = os.getenv("SSO_IAM_BASE_URL", "https://accounts.justdial.com/jdiam")
SSO_CLIENT_ID = os.getenv("SSO_CLIENT_ID", "")
SSO_CLIENT_SECRET = os.getenv("SSO_CLIENT_SECRET", "")
SSO_REDIRECT_URL = os.getenv("SSO_REDIRECT_URL", "")
SSO_LOGOUT_SERVICE_PARAM = os.getenv("SSO_LOGOUT_SERVICE_PARAM", "")

# Same lease-style expiry idea as the campaign job queue / callback worker — an
# abandoned login attempt's stashed code_verifier shouldn't live forever.
STATE_TTL = timedelta(minutes=10)

sso_states = db["tbl_ai_vb_sso_states"]
sso_states.create_index("created_at", expireAfterSeconds=int(STATE_TTL.total_seconds()))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) — code_challenge = base64url(sha256(verifier)),
    matching SsoTokenManager::saveCodeVerifier's contract exactly (doc section 3, step 3)."""
    code_verifier = _b64url(secrets.token_bytes(64))
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
    return code_verifier, code_challenge


def start_login() -> str:
    """Generates a PKCE pair, stashes the verifier keyed by a fresh state, and returns the
    URL to redirect the browser to (doc section 3, steps 3-4). Mirrors search_mis's use of
    an internal microservice for verifier storage, but uses this platform's own Mongo
    instead — that storage detail is explicitly called out in the doc as non-portable."""
    if not SSO_CLIENT_ID or not SSO_REDIRECT_URL:
        raise RuntimeError("SSO_CLIENT_ID / SSO_REDIRECT_URL not configured — see SSO_Integration_Reference.docx section 5")

    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(24)
    sso_states.insert_one({"state": state, "code_verifier": code_verifier, "created_at": datetime.now(timezone.utc)})

    return (
        f"{SSO_IAM_BASE_URL}/auth"
        f"?response_type=code&client_id={SSO_CLIENT_ID}&state={state}"
        f"&code_challenge={code_challenge}&code_challenge_method=SHA256"
    )


def exchange_code_for_profile(code: str, state: str) -> dict:
    """Doc section 3, steps 7-9: look up the stashed verifier by state, exchange the code
    for an access token, then fetch the employee profile. Raises ValueError on any failure
    (unknown/expired state, bad code, IdP error) — callers should turn that into a 400/401,
    never a silent login."""
    stashed = sso_states.find_one_and_delete({"state": state})
    if not stashed:
        raise ValueError("Unknown or expired SSO state — the login attempt may have timed out")

    token_resp = requests.post(
        f"{SSO_IAM_BASE_URL}/api/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": SSO_CLIENT_ID,
            "client_secret": SSO_CLIENT_SECRET,
            "redirect_uri": SSO_REDIRECT_URL,
            "code_verifier": stashed["code_verifier"],
        },
        timeout=10,
    )
    if not token_resp.ok:
        raise ValueError(f"SSO token exchange failed: {token_resp.status_code} {token_resp.text}")
    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise ValueError("SSO token exchange returned no access_token")

    validate_resp = requests.get(
        f"{SSO_IAM_BASE_URL}/api/validate-token",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not validate_resp.ok:
        raise ValueError(f"SSO token validation failed: {validate_resp.status_code} {validate_resp.text}")

    user = (validate_resp.json().get("data") or {}).get("user")
    if not user:
        raise ValueError("SSO validate-token response missing data.user")
    return user


def logout_redirect_url() -> str:
    """Doc section 3, step 14 — end the IdP-side session too, so a fresh login doesn't
    silently reuse it."""
    return f"{SSO_IAM_BASE_URL.rsplit('/jdiam', 1)[0]}/logout/logoutServiceAuth?logout_param={SSO_LOGOUT_SERVICE_PARAM}"
