"""Tests for the runtime configuration layer."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.config import PermissionMode, RunMode, Settings, get_settings


def test_defaults_are_sane() -> None:
    settings = Settings()
    assert settings.sample_rate == 16_000
    assert settings.wake_word == "hey_jarvis"
    assert settings.stt_model == "large-v3-turbo"
    assert settings.tts_voice == "bm_george"
    assert settings.permission_mode is PermissionMode.accept_edits


def test_run_mode_defaults_to_wake_word() -> None:
    """The always-on wake-word runtime is the product default."""
    settings = Settings()
    assert settings.run_mode is RunMode.wake_word
    assert settings.listen_max_seconds > 0


def test_run_mode_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_RUN_MODE", "push_to_talk")
    monkeypatch.setenv("JARVIS_LISTEN_MAX_SECONDS", "12.5")
    settings = Settings()
    assert settings.run_mode is RunMode.push_to_talk
    assert settings.listen_max_seconds == 12.5


def test_chimes_enabled_defaults_true_and_env_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Status chimes default on (eyes-free service feedback); .env can mute them."""
    assert Settings().chimes_enabled is True
    monkeypatch.setenv("JARVIS_CHIMES_ENABLED", "false")
    assert Settings().chimes_enabled is False


def test_listen_max_seconds_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(listen_max_seconds=0)


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
