from fastapi import APIRouter, Depends, Query

from ..auth import require_admin
from ..db import audit_log

router = APIRouter(prefix="/api/audit-log", tags=["audit-log"])


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    created = doc.get("created_at")
    if hasattr(created, "isoformat"):
        doc["created_at"] = created.isoformat()
    return doc


@router.get("")
def list_audit_log(
    resource_type: str = Query(""),
    action: str = Query(""),
    actor: str = Query(""),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_admin),
) -> dict:
    query: dict = {}
    if resource_type:
        query["resource_type"] = resource_type
    if action:
        query["action"] = action
    if actor:
        query["actor"] = actor

    total = audit_log.count_documents(query)
    docs = audit_log.find(query).sort("created_at", -1).skip(offset).limit(limit)
    return {"items": [_serialize(d) for d in docs], "total": total}
