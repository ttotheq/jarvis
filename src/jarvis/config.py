"""Runtime configuration for Jarvis.

All configuration is twelve-factor: every setting has a safe default, can be
overridden by a ``JARVIS_``-prefixed environment variable, and can be sourced
from a local ``.env`` file (see ``.env.example``). No behaviour-changing
constant should live anywhere but here, so that the running assistant can be
retargeted without code edits.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PermissionMode(StrEnum):
    """Permission modes passed through to the Claude Code headless process."""

    default = "default"
    accept_edits = "acceptEdits"
    bypass = "bypassPermissions"
    plan = "plan"


def _default_whisper_dir() -> Path:
    return Path.home() / ".cache" / "jarvis" / "whisper"


class Settings(BaseSettings):
    """Strongly-typed, validated runtime settings.

    Field groups mirror the voice cascade documented in ``docs/architecture.md``:
    audio I/O -> wake word -> VAD -> STT -> Claude brain -> TTS.
    """

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Audio I/O ---------------------------------------------------------
    sample_rate: int = Field(default=16_000, gt=0)
    input_device: str | None = None
    output_device: str | None = None

    # --- Wake word ---------------------------------------------------------
    wake_word: str = "hey_jarvis"
    wake_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- Voice activity detection / endpointing ---------------------------
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    vad_silence_ms: int = Field(default=700, ge=100)

    # --- Speech-to-text ----------------------------------------------------
    stt_model: str = "large-v3-turbo"
    stt_model_dir: Path = Field(default_factory=_default_whisper_dir)

    # --- Text-to-speech ----------------------------------------------------
    tts_voice: str = "bm_george"
    tts_speed: float = Field(default=1.0, gt=0.0)

    # --- Claude Code brain -------------------------------------------------
    claude_binary: str = "claude"
    permission_mode: PermissionMode = PermissionMode.accept_edits

    # --- Observability -----------------------------------------------------
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so configuration is parsed once. Call ``get_settings.cache_clear()``
    in tests that mutate the environment.
    """
    return Settings()
