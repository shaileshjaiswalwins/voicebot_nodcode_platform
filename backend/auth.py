import hashlib
import hmac
import os
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import users

JWT_SECRET = os.getenv("DASHBOARD_JWT_SECRET", "dev-only-secret-change-me-32-bytes-min")
JWT_ALGO = "HS256"
TOKEN_TTL_SEC = 60 * 60 * 12

DEFAULT_ADMIN_EMAIL = "admin@justdial.com"
DEFAULT_ADMIN_PASSWORD = "password"

_bearer = HTTPBearer(auto_error=False)


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
    """Seed the single hardcoded admin login if no users exist yet."""
    if users.count_documents({}) > 0:
        return
    users.insert_one(
        {
            "email": DEFAULT_ADMIN_EMAIL,
            "password_hash": hash_password(DEFAULT_ADMIN_PASSWORD),
            "role": "admin",
        }
    )


def authenticate(email: str, password: str) -> str | None:
    user = users.find_one({"email": email})
    if not user or not verify_password(password, user["password_hash"]):
        return None
    payload = {"sub": user["email"], "role": user.get("role", "admin"), "exp": int(time.time()) + TOKEN_TTL_SEC}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def issue_token_for_sso_profile(profile: dict) -> str:
    """Mint this platform's own JWT for an SSO-authenticated employee. Deliberately does
    NOT port search_mis's hardcoded department/section role table (doc section 6 says not
    to) — every SSO login gets 'admin' for now, same as the single local admin account.
    Refine this once the platform has more than one role to actually assign."""
    email = profile.get("email") or f"{profile.get('empcode')}@justdial.com"
    users.update_one(
        {"email": email},
        {"$set": {"email": email, "role": "admin", "sso_empcode": profile.get("empcode"), "sso_empname": profile.get("empname")}},
        upsert=True,
    )
    payload = {"sub": email, "role": "admin", "exp": int(time.time()) + TOKEN_TTL_SEC}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def require_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        return jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
