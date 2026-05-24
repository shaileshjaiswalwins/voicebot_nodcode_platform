"""Validation contract tests — what we accept, what we reject."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voicebot_platform.schemas import (
    CreateBotPayload,
    PublishPayload,
    RuntimeConfigModel,
    RuntimeSettingsUpdatePayload,
    SaveDraftPayload,
    TestSessionPayload as SessionPayload,
    assert_object_id,
)


def test_temperature_out_of_range_rejected():
    with pytest.raises(ValidationError):
        RuntimeConfigModel(temperature=5)
    with pytest.raises(ValidationError):
        RuntimeConfigModel(temperature=-0.1)


def test_max_call_duration_bounded():
    with pytest.raises(ValidationError):
        RuntimeConfigModel(max_call_duration=2)
    with pytest.raises(ValidationError):
        RuntimeConfigModel(max_call_duration=10000)
    RuntimeConfigModel(max_call_duration=120)  # OK


def test_unknown_model_rejected():
    with pytest.raises(ValidationError):
        RuntimeConfigModel(model="claude-future")
    RuntimeConfigModel(model="gemini-2.5-flash")  # OK


def test_huge_system_prompt_rejected():
    with pytest.raises(ValidationError):
        RuntimeConfigModel(system_prompt="x" * (40 * 1024))


def test_extra_keys_preserved():
    cfg = RuntimeConfigModel(model="gemini-2.5-flash", custom_knob="x", another=42)
    dumped = cfg.model_dump(exclude_none=True)
    assert dumped["custom_knob"] == "x"
    assert dumped["another"] == 42


def test_create_bot_requires_name():
    with pytest.raises(ValidationError):
        CreateBotPayload(name="")
    CreateBotPayload(name="OK")


def test_save_draft_requires_config():
    with pytest.raises(ValidationError):
        SaveDraftPayload()  # type: ignore[call-arg]
    SaveDraftPayload(config=RuntimeConfigModel())


def test_publish_payload_accepts_no_version_id():
    p = PublishPayload()
    assert p.version_id is None


def test_publish_payload_rejects_garbage_id():
    with pytest.raises(ValidationError):
        PublishPayload(version_id="not-hex")


def test_runtime_settings_agent_name_validates():
    with pytest.raises(ValidationError):
        RuntimeSettingsUpdatePayload(livekit_agent_name="bad name with spaces!")
    RuntimeSettingsUpdatePayload(livekit_agent_name="voice-bot-justdial-test")


def test_test_session_worker_agent_name_validates():
    with pytest.raises(ValidationError):
        SessionPayload(test_worker_agent_name="bad name with spaces!")
    payload = SessionPayload(test_worker_agent_name="voice-bot-justdial-test")
    assert payload.test_worker_agent_name == "voice-bot-justdial-test"


def test_assert_object_id_validates():
    with pytest.raises(ValueError):
        assert_object_id("not-hex")
    assert_object_id("64a1f1f1f1f1f1f1f1f1f1f1")  # 24 hex chars
