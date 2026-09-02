from fastapi import APIRouter, Depends

from ..audit import log_audit
from ..auth import require_admin
from ..db import platform_settings
from ..models import PlatformSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])

_DOC_ID = "platform_settings"


def _load() -> PlatformSettings:
    doc = platform_settings.find_one({"_id": _DOC_ID})
    if not doc:
        return PlatformSettings()
    doc.pop("_id", None)
    return PlatformSettings(**doc)


@router.get("/platform", response_model=PlatformSettings)
def get_platform_settings(_: dict = Depends(require_admin)) -> PlatformSettings:
    return _load()


@router.put("/platform", response_model=PlatformSettings)
def update_platform_settings(payload: PlatformSettings, user: dict = Depends(require_admin)) -> PlatformSettings:
    platform_settings.update_one({"_id": _DOC_ID}, {"$set": payload.model_dump()}, upsert=True)
    log_audit(user, "update", "platform_settings", _DOC_ID)
    return payload


@router.get("/platform/active-endpoints", response_model=dict)
def active_endpoints() -> dict:
    """Unauthenticated read used by backend workers (callback_worker, bot.py) to resolve
    which environment's API endpoints are currently active. Not a PM-facing route."""
    settings = _load()
    active = settings.dev if settings.active_environment == "dev" else settings.prod
    return {"active_environment": settings.active_environment, **active.model_dump()}
