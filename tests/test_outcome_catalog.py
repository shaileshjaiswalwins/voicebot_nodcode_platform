"""Outcome catalog: seed, edit, cache, and runtime mapping."""

from __future__ import annotations

import pytest

from voicebot_platform import outcome_catalog


def test_seed_populates_all_defaults():
    outcome_catalog.seed_default_outcomes()
    outcome_catalog.seed_default_outcomes()  # idempotent
    rows = outcome_catalog.list_outcomes()
    assert len(rows) == len(outcome_catalog.DEFAULT_OUTCOMES)
    keys = {row["key"] for row in rows}
    assert "Approved" in keys
    assert "Wrong Number" in keys


def test_get_disposition_map_returns_dict_of_keys_to_descriptions():
    outcome_catalog.seed_default_outcomes()
    outcome_catalog._invalidate()
    mapping = outcome_catalog.get_disposition_map()
    assert isinstance(mapping, dict)
    assert "Approved" in mapping
    assert mapping["Approved"].lower().startswith("the customer confirmed")


def test_update_outcome_changes_description():
    outcome_catalog.seed_default_outcomes()
    outcome_catalog._invalidate()
    updated = outcome_catalog.update_outcome(
        "Approved",
        {"description": "Custom description for testing."},
        "alice",
    )
    assert updated["description"] == "Custom description for testing."
    outcome_catalog._invalidate()
    fresh_map = outcome_catalog.get_disposition_map()
    assert fresh_map["Approved"] == "Custom description for testing."


def test_update_unknown_key_raises_keyerror():
    outcome_catalog.seed_default_outcomes()
    with pytest.raises(KeyError):
        outcome_catalog.update_outcome("DoesNotExist", {"description": "x"}, "alice")


def test_update_validates_lengths():
    outcome_catalog.seed_default_outcomes()
    with pytest.raises(ValueError):
        outcome_catalog.update_outcome("Approved", {"description": ""}, "alice")
    with pytest.raises(ValueError):
        outcome_catalog.update_outcome("Approved", {"description": "x" * 3000}, "alice")
