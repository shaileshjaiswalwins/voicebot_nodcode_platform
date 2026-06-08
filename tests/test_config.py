from __future__ import annotations

import pytest

from voicebot_platform import config


def test_config_warnings_flags_placeholder_livekit_values(monkeypatch):
    monkeypatch.setattr(config, "_MONGO_URI_FROM_ENV", False)
    monkeypatch.setattr(config, "VOICEBOT_ENV", "staging")
    monkeypatch.setattr(config, "DASHBOARD_ORIGINS", ["http://localhost:5173"])
    monkeypatch.setattr(config, "LIVEKIT_URL", "")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "devkey")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "secret")
    monkeypatch.setattr(config, "LANGFUSE_ENABLED", False)

    warnings = config.config_warnings()

    assert any("MONGO_URI" in item for item in warnings)
    assert any("LIVEKIT_URL" in item for item in warnings)
    assert any("LIVEKIT_API_KEY" in item for item in warnings)
    assert any("LIVEKIT_API_SECRET" in item for item in warnings)


def test_validate_startup_config_strict_raises(monkeypatch):
    monkeypatch.setattr(config, "config_warnings", lambda: ["missing LIVEKIT_API_SECRET"])

    with pytest.raises(RuntimeError, match="missing LIVEKIT_API_SECRET"):
        config.validate_startup_config(strict=True)


def test_validate_startup_config_non_strict_returns_warnings(monkeypatch):
    monkeypatch.setattr(config, "config_warnings", lambda: ["set MONGO_URI"])

    assert config.validate_startup_config(strict=False) == ["set MONGO_URI"]
