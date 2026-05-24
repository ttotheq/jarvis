"""Tests for the runtime configuration layer."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.config import PermissionMode, Settings, get_settings


def test_defaults_are_sane() -> None:
    settings = Settings()
    assert settings.sample_rate == 16_000
    assert settings.wake_word == "hey_jarvis"
    assert settings.stt_model == "large-v3-turbo"
    assert settings.tts_voice == "bm_george"
    assert settings.permission_mode is PermissionMode.accept_edits


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_SAMPLE_RATE", "48000")
    monkeypatch.setenv("JARVIS_TTS_VOICE", "bm_lewis")
    monkeypatch.setenv("JARVIS_PERMISSION_MODE", "bypassPermissions")
    settings = Settings()
    assert settings.sample_rate == 48_000
    assert settings.tts_voice == "bm_lewis"
    assert settings.permission_mode is PermissionMode.bypass


def test_threshold_must_be_within_unit_interval() -> None:
    with pytest.raises(ValidationError):
        Settings(wake_threshold=1.5)


def test_sample_rate_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(sample_rate=0)


def test_whisper_dir_defaults_under_home() -> None:
    settings = Settings()
    assert settings.stt_model_dir.is_absolute()
    assert "jarvis" in settings.stt_model_dir.parts


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
