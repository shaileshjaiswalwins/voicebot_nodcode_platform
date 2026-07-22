"""Tests for the claim/lease and retry-cap/dead-letter behavior in callback_worker/worker.py.

Two failure modes motivated these tests:
  1. Two worker processes running concurrently (deploy overlap, accidental double
     systemd unit) could both pick up the same untagged doc and double-process it.
  2. A permanently-failing callback (downstream API down, bad data) retried forever,
     re-running two paid Gemini calls on every retry with no cap.
"""

from datetime import datetime, timedelta, timezone

import mongomock
import pytest

from callback_worker import worker


@pytest.fixture()
def collection():
    client = mongomock.MongoClient()
    return client["test_db"]["test_transcripts"]


def _insert_untagged_doc(collection, **overrides) -> dict:
    doc = {
        "lead_id": "lead-123",
        "status": "completed",
        "transcript": [{"role": "user", "text": "hi"}],
        "tagged": False,
        "created_at": datetime.now(timezone.utc),
    }
    doc.update(overrides)
    result = collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


def test_claim_one_marks_doc_as_processing_so_a_second_worker_cannot_claim_it_too(collection):
    doc = _insert_untagged_doc(collection)

    first_claim = worker._claim_one(collection)
    assert first_claim is not None
    assert first_claim["_id"] == doc["_id"]
    assert first_claim["processing"] is True

    second_claim = worker._claim_one(collection)
    assert second_claim is None, "a second worker must not be able to claim an already-processing doc"


def test_claim_one_reclaims_a_doc_whose_lease_has_expired(collection):
    stale_claimed_at = datetime.now(timezone.utc) - worker.LEASE_TIMEOUT - timedelta(minutes=1)
    doc = _insert_untagged_doc(collection, processing=True, claimed_at=stale_claimed_at)

    reclaimed = worker._claim_one(collection)
    assert reclaimed is not None
    assert reclaimed["_id"] == doc["_id"], "a stale (crashed-worker) claim must be reclaimable"


def test_claim_one_skips_dead_lettered_docs(collection):
    _insert_untagged_doc(collection, dead_letter=True)
    assert worker._claim_one(collection) is None


@pytest.mark.asyncio
async def test_process_doc_reuses_persisted_analysis_on_retry_without_recomputing(collection, monkeypatch):
    doc = _insert_untagged_doc(collection, analysis={
        "call_outcome": "Interested", "call_outcome_description": "", "call_summary": "",
        "is_business": "no", "business_intent": "", "b2b_user": "no", "business_name": "",
        "business_city": "", "qna": [], "product_change": {}, "rescheduled_to": "",
        "deal_value": "", "lead_intent_score": "", "urgency_flag": "no",
    })

    async def _should_not_be_called(*args, **kwargs):
        raise AssertionError("generate_call_analysis must not be re-run when analysis is already persisted")

    monkeypatch.setattr(worker, "generate_call_analysis", _should_not_be_called)
    monkeypatch.setattr(worker, "generate_b2b_score", _should_not_be_called)

    async def _fake_send_callback(payload, session, url):
        return True

    monkeypatch.setattr(worker, "send_callback", _fake_send_callback)

    await worker._process_doc(doc, collection, http_session=None)

    updated = collection.find_one({"_id": doc["_id"]})
    assert updated["tagged"] is True
    assert "processing" not in updated
    assert "claimed_at" not in updated


@pytest.mark.asyncio
async def test_process_doc_marks_dead_letter_after_threshold_failed_attempts(collection, monkeypatch):
    doc = _insert_untagged_doc(collection, analysis={
        "call_outcome": "Interested", "call_outcome_description": "", "call_summary": "",
        "is_business": "no", "business_intent": "", "b2b_user": "no", "business_name": "",
        "business_city": "", "qna": [], "product_change": {}, "rescheduled_to": "",
        "deal_value": "", "lead_intent_score": "", "urgency_flag": "no",
    })

    async def _should_not_be_called(*args, **kwargs):
        raise AssertionError("analysis must not be re-run on retries — it's already persisted")

    monkeypatch.setattr(worker, "generate_call_analysis", _should_not_be_called)
    monkeypatch.setattr(worker, "generate_b2b_score", _should_not_be_called)

    async def _always_fails(payload, session, url):
        return False

    monkeypatch.setattr(worker, "send_callback", _always_fails)

    for _ in range(worker.DEAD_LETTER_THRESHOLD):
        current = collection.find_one({"_id": doc["_id"]})
        await worker._process_doc(current, collection, http_session=None)

    final = collection.find_one({"_id": doc["_id"]})
    assert final["callback_attempts"] == worker.DEAD_LETTER_THRESHOLD
    assert final["dead_letter"] is True
    assert final["tagged"] is False, "a dead-lettered doc is not tagged — it's excluded from retry via dead_letter, not tagged"
    assert worker._claim_one(collection) is None, "a dead-lettered doc must not be claimable anymore"
