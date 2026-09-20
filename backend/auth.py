import hashlib
import hmac
import os
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import bots, users

JWT_SECRET = os.getenv("DASHBOARD_JWT_SECRET", "dev-only-secret-change-me-32-bytes-min")
JWT_ALGO = "HS256"
TOKEN_TTL_SEC = 60 * 60 * 12

# Identity values are env-driven so the source carries no real company domain.
# Production MUST set DEFAULT_ADMIN_EMAIL and SSO_EMAIL_DOMAIN to the real values —
# an existing admin row in Mongo is matched by this exact string.
DEFAULT_ADMIN_EMAIL = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@acmecorp.com")
SSO_EMAIL_DOMAIN = os.getenv("SSO_EMAIL_DOMAIN", "acmecorp.com")
DEFAULT_ADMIN_PASSWORD = "password"

_bearer = HTTPBearer(auto_error=False)


def _normalize_email(email: str) -> str:
    """Single source of truth for how a `users.email` is compared/stored. Mongo string
    equality is case-sensitive, so every entry point that queries or writes users.email
    (signup, login, SSO, role/delete-by-email) must funnel through this exact function —
    otherwise a user who signs up as Foo@Bar.com and later types the same address with
    different casing gets a case-sensitive lookup miss and is locked out."""
    return email.strip().lower()


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000).hex()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    return f"{salt.hex()}${_hash_password(password, salt)}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    salt = bytes.fromhex(salt_hex)
    return hmac.compare_digest(_hash_password(password, salt), digest_hex)


def ensure_default_admin() -> None:
    """Seed the single hardcoded admin login if no users exist yet.

    This used to also demote every non-default-admin account with role="admin" back to
    "user" on every startup, to correct fallout from an old SSO/auth bug where every SSO
    login (and every user with no role) defaulted to admin. That bug is fixed at its source
    now — issue_token_for_sso_profile() only defaults new accounts to admin when the email
    is DEFAULT_ADMIN_EMAIL, and uses $setOnInsert so it never clobbers a role an admin later
    granted via the Accounts page. Running the demotion sweep unconditionally on every boot
    was a standing invariant, not a one-time cleanup, and it silently reverted any admin a
    user promoted through Accounts on the next deploy. See backend/tests/test_sso.py."""
    if users.count_documents({}) == 0:
        users.insert_one(
            {
                "email": DEFAULT_ADMIN_EMAIL,
                "password_hash": hash_password(DEFAULT_ADMIN_PASSWORD),
                "role": "admin",
            }
        )


def authenticate(email: str, password: str) -> str | None:
    email = _normalize_email(email)
    user = users.find_one({"email": email})
    if not user or not verify_password(password, user["password_hash"]):
        return None
    payload = {"sub": user["email"], "role": user.get("role", "user"), "exp": int(time.time()) + TOKEN_TTL_SEC}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def create_user(email: str, password: str, role: str = "user") -> str | None:
    """Self-service signup. Returns a signed-in token, or None if the email is taken."""
    email = _normalize_email(email)
    if users.find_one({"email": email}):
        return None
    users.insert_one({"email": email, "password_hash": hash_password(password), "role": role})
    payload = {"sub": email, "role": role, "exp": int(time.time()) + TOKEN_TTL_SEC}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def issue_token_for_sso_profile(profile: dict) -> str:
    """Mint this platform's own JWT for an SSO-authenticated employee. Only
    Only DEFAULT_ADMIN_EMAIL is an admin — every other SSO login is a regular user by default,
    same as local signup. Uses $setOnInsert for role so a first-time login doesn't clobber
    a role an admin later granted through the Accounts page."""
    email = _normalize_email(profile.get("email") or f"{profile.get('empcode')}@{SSO_EMAIL_DOMAIN}")
    default_role = "admin" if email == DEFAULT_ADMIN_EMAIL else "user"
    user = users.find_one_and_update(
        {"email": email},
        {
            "$set": {"email": email, "sso_empcode": profile.get("empcode"), "sso_empname": profile.get("empname")},
            "$setOnInsert": {"role": default_role},
        },
        upsert=True,
        return_document=True,
    )
    role = user.get("role", default_role)
    payload = {"sub": email, "role": role, "exp": int(time.time()) + TOKEN_TTL_SEC}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def require_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    # Role lives in the DB, not just the (up to 12h-old) token — re-read it every request so
    # a role change or demotion from the Accounts page takes effect immediately instead of
    # waiting for the token to expire, and so a deleted account stops working right away.
    user = users.find_one({"email": payload.get("sub")})
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    payload["role"] = user.get("role", "user")
    return payload


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def bot_owner_filter(user: dict) -> dict:
    """Admins see every bot (and everything joined off bot ownership below); everyone else
    only their own. Merge into any Mongo filter targeting the bots collection so a non-admin
    can't read or mutate another user's bot by guessing/pasting its id — same 404 either
    way, existence isn't leaked."""
    return {} if user.get("role") == "admin" else {"owner": user.get("sub")}


def resolve_owned_bot_ids(email: str, role: str) -> list[str] | None:
    """Plain, request-independent version of the ownership logic below — usable from
    contexts with no HTTP request/JWT (e.g. the alert worker), which re-read the rule
    creator's current role from `users` at eval time rather than trusting a stale value,
    same "don't trust a stale token" principle `require_user` already applies. None means
    admin (no restriction, caller should skip filtering)."""
    if role == "admin":
        return None
    return [str(b["_id"]) for b in bots.find({"owner": email}, {"_id": 1})]


def owned_bot_ids(user: dict) -> list[str] | None:
    """None means admin (no restriction, caller should skip filtering). Otherwise the hex
    _id strings of bots this user owns, for scoping bot-joined collections (transcripts,
    campaigns, ...) that only carry bot_id, not their own owner field."""
    return resolve_owned_bot_ids(user.get("sub"), user.get("role"))
