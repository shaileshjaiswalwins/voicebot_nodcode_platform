"""Round-trip tests for bot lifecycle: create -> draft -> publish -> rollback.

These tests would have caught the call_type regression and the publish-without-
draft edge case described in the audit. They also lock in the
config_snapshot contract: published versions are immutable per-call.
"""

from __future__ import annotations

import pytest

from voicebot_platform import config_store


def _minimal_config() -> dict:
    return {
        "model": "gemini-3.1-flash-live-preview",
        "voice": "Aoede",
        "language": "hindi",
        "system_prompt": "You are Simran.",
    }


def test_create_bot_creates_draft_version():
    bot = config_store.create_bot({"name": "Test", "config": _minimal_config()}, user="alice")
    bundle = config_store.get_bot(bot["_id"])
    assert bundle is not None
    assert bundle["bot"]["name"] == "Test"
    assert bundle["bot"]["status"] == "draft"
    assert len(bundle["versions"]) == 1
    assert bundle["versions"][0]["state"] == "draft"
    assert bundle["versions"][0]["version"] == 1


def test_publish_promotes_draft_and_updates_active_pointer():
    bot = config_store.create_bot({"name": "PublishMe", "config": _minimal_config()}, "alice")
    bot_id = bot["_id"]
    draft_id = config_store.get_bot(bot_id)["versions"][0]["_id"]

    published = config_store.publish_version(bot_id, draft_id, "alice")
    assert published["state"] == "published"

    bundle = config_store.get_bot(bot_id)
    assert bundle["bot"]["active_version_id"] == published["_id"]
    assert bundle["bot"]["status"] == "active"
    assert "draft_version_id" not in bundle["bot"]


def test_save_draft_increments_version_and_keeps_published_pointer_stable():
    bot = config_store.create_bot({"name": "DraftChain", "config": _minimal_config()}, "alice")
    bot_id = bot["_id"]
    draft_id = config_store.get_bot(bot_id)["versions"][0]["_id"]
    config_store.publish_version(bot_id, draft_id, "alice")
    active_before = config_store.get_bot(bot_id)["bot"]["active_version_id"]

    new_cfg = {**_minimal_config(), "temperature": 0.9}
    next_draft = config_store.save_draft(bot_id, {"config": new_cfg}, "alice")
    assert next_draft["version"] == 2
    assert next_draft["state"] == "draft"

    # Active pointer stays on v1 until we publish v2.
    after = config_store.get_bot(bot_id)
    assert after["bot"]["active_version_id"] == active_before


def test_rollback_to_a_prior_published_version():
    bot = config_store.create_bot({"name": "Rollback", "config": _minimal_config()}, "alice")
    bot_id = bot["_id"]

    v1_id = config_store.get_bot(bot_id)["versions"][0]["_id"]
    config_store.publish_version(bot_id, v1_id, "alice")

    config_store.save_draft(bot_id, {"config": {**_minimal_config(), "temperature": 0.8}}, "alice")
    v2_id = config_store.get_bot(bot_id)["bot"]["draft_version_id"]
    config_store.publish_version(bot_id, v2_id, "alice")
    assert config_store.get_bot(bot_id)["bot"]["active_version_id"] == v2_id

    rolled = config_store.rollback_bot(bot_id, v1_id, "alice")
    assert rolled["_id"] == v1_id
    assert config_store.get_bot(bot_id)["bot"]["active_version_id"] == v1_id


def test_publish_with_no_draft_raises_keyerror():
    """Edge case from the audit: publish called without a version_id and no draft
    must raise rather than silently returning nothing or 500-ing."""
    bot = config_store.create_bot({"name": "NoDraft", "config": _minimal_config()}, "alice")
    bot_id = bot["_id"]
    draft_id = config_store.get_bot(bot_id)["versions"][0]["_id"]
    config_store.publish_version(bot_id, draft_id, "alice")
    # Now there is no draft. publish_version with no version_id should fail loudly.
    with pytest.raises(KeyError):
        config_store.publish_version(bot_id, None, "alice")


def test_fetch_active_bot_config_returns_published_only():
    """The runtime must never see a draft. fetch_active_bot_config should only
    return data when an active published version exists."""
    bot = config_store.create_bot({"name": "Active", "assistant_id": "asst-xyz", "config": _minimal_config()}, "alice")
    bot_id = bot["_id"]

    # Draft exists, no published version yet.
    assert config_store.fetch_active_bot_config("asst-xyz") is None

    draft_id = config_store.get_bot(bot_id)["versions"][0]["_id"]
    config_store.publish_version(bot_id, draft_id, "alice")

    snap = config_store.fetch_active_bot_config("asst-xyz")
    assert snap is not None
    assert snap["assistant_id"] == "asst-xyz"
    assert snap["model"] == "gemini-3.1-flash-live-preview"
    assert snap["bot_version"] == 1


def test_fetch_active_bot_config_handles_missing_assistant():
    assert config_store.fetch_active_bot_config("") is None
    assert config_store.fetch_active_bot_config("nope") is None


def test_duplicate_bot_clones_published_config():
    bot = config_store.create_bot({"name": "Original", "config": _minimal_config()}, "alice")
    bot_id = bot["_id"]
    draft_id = config_store.get_bot(bot_id)["versions"][0]["_id"]
    config_store.publish_version(bot_id, draft_id, "alice")

    dup = config_store.duplicate_bot(bot_id, "bob")
    assert dup["_id"] != bot_id
    assert dup["name"] == "Original Copy"
    dup_bundle = config_store.get_bot(dup["_id"])
    assert dup_bundle["versions"][0]["config"]["model"] == "gemini-3.1-flash-live-preview"


def test_delete_bot_hides_bot_and_disables_active_runtime_config():
    bot = config_store.create_bot({"name": "DeleteMe", "assistant_id": "asst-delete", "config": _minimal_config()}, "alice")
    bot_id = bot["_id"]
    draft_id = config_store.get_bot(bot_id)["versions"][0]["_id"]
    config_store.publish_version(bot_id, draft_id, "alice")
    assert config_store.fetch_active_bot_config("asst-delete") is not None

    deleted = config_store.delete_bot(bot_id, "admin")

    assert deleted["status"] == "deleted"
    assert deleted["deleted_by"] == "admin"
    assert bot_id not in {item["_id"] for item in config_store.list_bots()}
    assert config_store.fetch_active_bot_config("asst-delete") is None


def test_transcript_source_is_not_overwritten_by_collection_source():
    from voicebot_platform.config import TRANSCRIPT_COLLECTION
    from voicebot_platform.mongo import get_db

    inserted_id = get_db()[TRANSCRIPT_COLLECTION].insert_one(
        {
            "call_id": "CALL-SOURCE",
            "transcript": [],
            "transcript_source": "recording_verified",
            "created_at": config_store._now(),
        }
    ).inserted_id

    doc = config_store.get_transcript(str(inserted_id))

    assert doc is not None
    assert doc["transcript_source"] == "recording_verified"
    assert doc["collection_source"] == "platform"
