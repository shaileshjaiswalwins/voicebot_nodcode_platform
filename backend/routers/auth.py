import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from .. import auth as auth_module
from .. import sso as sso_module
from ..models import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Where the browser lands after a successful (or failed) SSO login — the SPA, not the API.
SSO_FRONTEND_URL = os.getenv("SSO_FRONTEND_URL", "http://localhost:5173")


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    token = auth_module.authenticate(payload.email, payload.password)
    if not token:
        raise HTTPException(401, "Invalid email or password")
    return LoginResponse(token=token, email=payload.email)


@router.get("/sso/login")
def sso_login() -> RedirectResponse:
    """Doc section 3, steps 2-4: generate PKCE, stash the verifier, redirect to the IdP."""
    try:
        redirect_url = sso_module.start_login()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return RedirectResponse(redirect_url)


@router.get("/sso/callback")
def sso_callback(code: str, state: str) -> RedirectResponse:
    """Doc section 3, steps 6-9: the IdP's registered redirect_uri. Exchanges the code for
    an employee profile, mints this platform's own JWT (not search_mis's session/roles —
    see the doc's section 6 caveat that role derivation is app-specific), then redirects
    the browser to the frontend with the token so it can call setToken() and proceed."""
    try:
        profile = sso_module.exchange_code_for_profile(code, state)
        token = auth_module.issue_token_for_sso_profile(profile)
    except ValueError as exc:
        return RedirectResponse(f"{SSO_FRONTEND_URL}/?sso_error={exc}")
    return RedirectResponse(f"{SSO_FRONTEND_URL}/?sso_token={token}")


@router.get("/sso/logout-url")
def sso_logout_url() -> dict:
    """Doc section 3, step 14 — frontend hits this to know where to send the browser to
    also end the IdP-side session on logout, not just clear the local JWT."""
    return {"url": sso_module.logout_redirect_url()}
