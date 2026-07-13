import logging
from datetime import datetime, timezone

from .db import audit_log

_log = logging.getLogger("voicebot_admin")


def log_audit(user: dict, action: str, resource_type: str, resource_id: str = "", details: dict | None = None) -> None:
    """Records one admin-mutation event. Called from route handlers after a write succeeds
    (not before) so a failed mutation never produces a misleading log entry.

    Best-effort: a logging failure must never fail the request that triggered it, so this
    swallows and does not re-raise — the mutation itself already committed.
    """
    try:
        audit_log.insert_one(
            {
                "actor": user.get("sub", "unknown"),
                "actor_role": user.get("role", ""),
                "action": action,
                "resource_type": resource_type,
                "resource_id": str(resource_id) if resource_id else "",
                "details": details or {},
                "created_at": datetime.now(timezone.utc),
            }
        )
    except Exception:
        _log.warning("[AUDIT] failed to record audit log entry for action=%r resource_type=%r", action, resource_type, exc_info=True)
