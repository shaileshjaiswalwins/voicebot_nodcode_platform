from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..audit import log_audit
from ..auth import require_user
from ..db import db, language_settings
from ..models import LanguageSettingsUpsert, LibraryPhraseCreate, OutcomeEntryUpdate

router = APIRouter(prefix="/api/library", tags=["library"])

phrases = db["tbl_ai_vb_library_phrases"]
outcomes = db["tbl_ai_vb_outcome_catalog"]


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc


@router.get("/phrases")
def list_phrases(_: dict = Depends(require_user)) -> list[dict]:
    return [_serialize(p) for p in phrases.find({})]


@router.post("/phrases")
def create_phrase(payload: LibraryPhraseCreate, user: dict = Depends(require_user)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    doc = {**payload.model_dump(), "created_by": user.get("sub"), "updated_at": now}
    inserted_id = phrases.insert_one(doc).inserted_id
    doc["_id"] = inserted_id
    return _serialize(doc)


@router.put("/phrases/{phrase_id}")
def update_phrase(phrase_id: str, payload: dict, _: dict = Depends(require_user)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    payload = {k: v for k, v in payload.items() if k in {"text", "language", "notes", "category"}}
    result = phrases.find_one_and_update(
        {"_id": ObjectId(phrase_id)},
        {"$set": {**payload, "updated_at": now}},
        return_document=True,
    )
    if not result:
        raise HTTPException(404, "Phrase not found")
    return _serialize(result)


@router.delete("/phrases/{phrase_id}")
def delete_phrase(phrase_id: str, _: dict = Depends(require_user)) -> dict:
    result = phrases.delete_one({"_id": ObjectId(phrase_id)})
    if result.deleted_count == 0:
        raise HTTPException(404, "Phrase not found")
    return {"ok": True}


@router.get("/outcomes")
def list_outcomes(_: dict = Depends(require_user)) -> list[dict]:
    return [_serialize(o) for o in outcomes.find({})]


@router.put("/outcomes/{key}")
def update_outcome(key: str, payload: OutcomeEntryUpdate, _: dict = Depends(require_user)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    outcomes.update_one(
        {"key": key},
        {"$set": {"key": key, **payload.model_dump(), "updated_at": now}},
        upsert=True,
    )
    return _serialize(outcomes.find_one({"key": key}))


@router.get("/languages")
def list_language_settings(_: dict = Depends(require_user)) -> list[dict]:
    return [_serialize(l) for l in language_settings.find({})]


@router.put("/languages")
def upsert_language_settings(payload: LanguageSettingsUpsert, _: dict = Depends(require_user)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    language_settings.update_one(
        {"id": payload.id},
        {"$set": {**payload.model_dump(), "updated_at": now}},
        upsert=True,
    )
    return _serialize(language_settings.find_one({"id": payload.id}))


@router.delete("/languages/{language_id}")
def delete_language_setting(language_id: str, user: dict = Depends(require_user)) -> dict:
    result = language_settings.delete_one({"id": language_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Language setting not found")
    log_audit(user, "delete", "language_setting", language_id)
    return {"ok": True}
