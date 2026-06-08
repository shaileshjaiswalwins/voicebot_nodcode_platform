"""Request logging + audit trail middleware.

Writes one line per request to loguru with method, path, actor, status,
duration. For mutations on platform resources (bots, versions, campaigns,
settings, library) also writes an audit document to Mongo so we can answer
"who changed X and when" even with the X-JD-User header trust gap.

Once real SSO ships, swap the actor source from the header to the verified
identity — the rest of this module doesn't change.
"""

from __future__ import annotations

import time
import asyncio
from datetime import datetime
from typing import Any

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .mongo import get_db

AUDIT_COLLECTION = "tbl_ai_vb_audit_log"

# Resource path -> action label. Anything matching the prefix becomes an audit row.
_AUDITED_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/bots", "bot"),
    ("/api/campaigns", "campaign"),
    ("/api/settings/", "settings"),
    ("/api/observability/", "observability"),
    ("/api/library/", "library"),
)

_AUDITED_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def _classify(path: str) -> str | None:
    for prefix, label in _AUDITED_PREFIXES:
        if path.startswith(prefix):
            return label
    return None


async def _write_audit(
    actor: str, method: str, path: str, status: int, resource: str, duration_ms: float
) -> None:
    def _insert() -> None:
        get_db()[AUDIT_COLLECTION].insert_one(
            {
                "actor": actor,
                "method": method,
                "path": path,
                "status": status,
                "resource": resource,
                "duration_ms": round(duration_ms, 1),
                "created_at": datetime.utcnow(),
            }
        )

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _insert)
    except Exception as exc:
        # Audit failures must never break the request path.
        logger.warning(f"[AUDIT] write failed: {exc}")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        actor = request.headers.get("x-jd-user") or "anon"
        method = request.method
        path = request.url.path
        try:
            response: Response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(f"[REQ] {method} {path} actor={actor!r} duration_ms={duration_ms:.0f} status=500")
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            f"[REQ] {method} {path} actor={actor!r} status={response.status_code} duration_ms={duration_ms:.0f}"
        )

        if method in _AUDITED_METHODS:
            resource = _classify(path)
            if resource:
                # Fire-and-forget; await briefly so a slow Mongo doesn't gate the response.
                await _write_audit(actor, method, path, response.status_code, resource, duration_ms)
        return response
