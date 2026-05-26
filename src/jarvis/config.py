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


class RunMode(StrEnum):
    """How ``jarvis run`` enters a turn.

    ``wake_word`` is the always-on runtime (IDLE waits for "hey jarvis", then VAD
    endpoints the utterance) and the product default — it needs no keyboard, so it
    runs headless under the launchd service. ``push_to_talk`` (Enter-gated) and
    ``timed`` (fixed ``ptt_seconds`` window) are the developer-harness modes.
    """

    wake_word = "wake_word"
    push_to_talk = "push_to_talk"
    timed = "timed"


def _default_whisper_dir() -> Path:
    return Path.home() / ".cache" / "jarvis" / "whisper"


def _default_service_log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "jarvis"


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
    # Tuned against the G2.1 soak (docs/phases/phase-2-wakeword-streaming.md): 0.9
    # holds false-accepts within budget against near-miss phrases while keeping
    # 100% true-accept. Lower it if real-voice true-accept proves marginal.
    wake_threshold: float = Field(default=0.9, ge=0.0, le=1.0)

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

    # --- Runtime mode ------------------------------------------------------
    # wake_word (default) is the always-on cascade: IDLE waits for "hey jarvis",
    # then VAD endpoints the utterance — no keyboard, so it runs under launchd.
    # push_to_talk and timed are the developer-harness modes.
    run_mode: RunMode = RunMode.wake_word
    # Safety cap on a single LISTENING capture so a stuck endpointer can never
    # record forever (wake_word mode).
    listen_max_seconds: float = Field(default=30.0, gt=0)

    # --- Push-to-talk runtime (developer harness) -------------------------
    # In `timed` run mode, ptt_seconds is the fixed record window per turn
    # (hands-free, spoken cue). max_turns stops the loop after N turns (None runs
    # until Ctrl-C); the always-on service leaves it None.
    ptt_seconds: float | None = Field(default=None, gt=0)
    max_turns: int | None = Field(default=None, gt=0)

    # --- launchd service (G4.1) -------------------------------------------
    # Reverse-DNS label for the LaunchAgent; also names the plist file
    # (~/Library/LaunchAgents/<label>.plist) and the launchctl service target.
    service_label: str = "com.jarvis.voice"
    # Where the LaunchAgent writes stdout/stderr (created on install).
    service_log_dir: Path = Field(default_factory=_default_service_log_dir)

    # --- Observability -----------------------------------------------------
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so configuration is parsed once. Call ``get_settings.cache_clear()``
    in tests that mutate the environment.
    """
    return Settings()
