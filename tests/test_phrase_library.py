"""Phrase library round-trip + analysis-fallback behavior."""

from __future__ import annotations

import pytest

from callback_worker.analysis import generate_call_analysis
from voicebot_platform import phrase_library


def test_seed_is_idempotent_and_populates_each_category():
    phrase_library.seed_default_phrases()
    phrase_library.seed_default_phrases()  # second call must not duplicate
    rows = phrase_library.list_phrases()
    by_category: dict[str, int] = {}
    for row in rows:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
    assert "voicemail" in by_category
    assert "hold_music" in by_category
    assert "dnc_trigger" in by_category


def test_create_phrase_validates_inputs():
    phrase_library.seed_default_phrases()
    with pytest.raises(ValueError):
        phrase_library.create_phrase({"category": "nope", "text": "x"}, "alice")
    with pytest.raises(ValueError):
        phrase_library.create_phrase({"category": "voicemail", "text": ""}, "alice")
    with pytest.raises(ValueError):
        phrase_library.create_phrase({"category": "voicemail", "text": "x" * 600}, "alice")


def test_create_then_get_phrase_texts_includes_new_entry():
    phrase_library.seed_default_phrases()
    phrase_library._invalidate_cache()  # ensure fresh read after seed
    before = phrase_library.get_phrase_texts("voicemail")
    phrase_library.create_phrase(
        {"category": "voicemail", "text": "PLEASE LEAVE A BRAND NEW MESSAGE", "language": "en"},
        "alice",
    )
    after = phrase_library.get_phrase_texts("voicemail")
    assert any("brand new message" in t for t in after)
    assert len(after) > len(before)


def test_update_and_delete_phrase():
    phrase_library.seed_default_phrases()
    created = phrase_library.create_phrase(
        {"category": "hold_music", "text": "totally unique hold tone", "language": "en"},
        "alice",
    )
    updated = phrase_library.update_phrase(created["_id"], {"notes": "needs review"}, "bob")
    assert updated["notes"] == "needs review"
    assert updated["updated_by"] == "bob"

    phrase_library.delete_phrase(created["_id"])
    with pytest.raises(KeyError):
        phrase_library.update_phrase(created["_id"], {"notes": "x"}, "bob")


def test_get_phrase_texts_falls_back_when_collection_empty():
    # Don't seed — make sure analysis-side reader still returns the hardcoded defaults.
    phrase_library._invalidate_cache()
    voicemail = phrase_library.get_phrase_texts("voicemail")
    assert voicemail  # non-empty
    assert all(isinstance(t, str) for t in voicemail)


@pytest.mark.asyncio
async def test_dnc_phrase_short_circuits_analysis():
    phrase_library.seed_default_phrases()
    result = await generate_call_analysis(
        [{"role": "user", "text": "Please remove my number from your list"}],
        "completed",
        {},
        http_session=None,  # not used because the deterministic guard returns first
    )
    assert result["call_outcome"] == "DNC Client : Don't Call Further"
