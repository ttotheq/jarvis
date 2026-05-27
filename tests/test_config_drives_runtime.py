"""G4.4 — the runtime is config-driven: voice, model, and permission mode are
all changeable via ``.env`` (``JARVIS_``-prefixed env vars) with **no code edit**.

These are the acceptance tests for Phase 4 goal G4.4. They prove that an env
override flows all the way to the seam the live component reads — the ``claude``
argv for the brain, and the settings object the native TTS/STT backends consume.
Per ADR-0005 the native backends (Kokoro, whisper.cpp) are ``# pragma: no cover``
and are not spawned here: TTS/STT are proven at the settings/builder seam, the
brain at the settings -> argv seam. Env isolation rides the autouse
``get_settings.cache_clear()`` fixture in ``conftest.py``; each ``get_settings()``
below is parsed fresh from the monkeypatched environment.
"""

from __future__ import annotations

import json

import pytest

from jarvis.brain import Brain
from jarvis.config import PermissionMode, get_settings
from jarvis.stt import WhisperCppTranscriber


class _Runner:
    """Records each blocking argv and returns one canned ``claude -p`` JSON."""

    def __init__(self, session_id: str = "sess-1", result: str = "ok") -> None:
        self._json = json.dumps({"type": "result", "session_id": session_id, "result": result})
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        return self._json


class _StreamRunner:
    """Records each streaming argv and replays one canned ``result`` line."""

    def __init__(self, session_id: str = "sess-1", result: str = "ok") -> None:
        self._lines = [
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": session_id,
                    "result": result,
                }
            )
        ]
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> list[str]:
        self.calls.append(list(argv))
        return list(self._lines)


# --- Permission mode (already wired; documented here as a G4.4 contract) ------


def test_permission_mode_env_drives_brain_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """JARVIS_PERMISSION_MODE reaches --permission-mode on both call shapes,
    with no settings passed in code (Brain pulls get_settings())."""
    monkeypatch.setenv("JARVIS_PERMISSION_MODE", "bypassPermissions")

    runner = _Runner()
    stream_runner = _StreamRunner()
    brain = Brain(runner=runner, stream_runner=stream_runner)

    brain.ask("ping")
    list(brain.stream("ping"))

    ask_argv = runner.calls[0]
    stream_argv = stream_runner.calls[0]
    assert ask_argv[ask_argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert stream_argv[stream_argv.index("--permission-mode") + 1] == "bypassPermissions"


# --- Claude model (the G4.4 gap being closed) ---------------------------------


def test_claude_model_env_drives_brain_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """JARVIS_CLAUDE_MODEL reaches --model on both ask() and stream() argv."""
    monkeypatch.setenv("JARVIS_CLAUDE_MODEL", "claude-opus-4-7")

    runner = _Runner()
    stream_runner = _StreamRunner()
    brain = Brain(runner=runner, stream_runner=stream_runner)

    brain.ask("ping")
    list(brain.stream("ping"))

    ask_argv = runner.calls[0]
    stream_argv = stream_runner.calls[0]
    assert ask_argv[ask_argv.index("--model") + 1] == "claude-opus-4-7"
    assert stream_argv[stream_argv.index("--model") + 1] == "claude-opus-4-7"


def test_claude_model_absent_when_unset() -> None:
    """With no JARVIS_CLAUDE_MODEL, --model is omitted so the CLI default holds."""
    runner = _Runner()
    stream_runner = _StreamRunner()
    brain = Brain(runner=runner, stream_runner=stream_runner)

    brain.ask("ping")
    list(brain.stream("ping"))

    assert "--model" not in runner.calls[0]
    assert "--model" not in stream_runner.calls[0]
    # The default setting is the "not set" sentinel.
    assert get_settings().claude_model is None


def test_claude_model_preserves_argv_invariants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding --model leaves the --output-format insert and trailing --resume intact."""
    monkeypatch.setenv("JARVIS_CLAUDE_MODEL", "claude-opus-4-7")

    runner = _Runner(session_id="sess-xyz")
    brain = Brain(runner=runner)

    brain.ask("turn 1")  # captures the session id
    brain.ask("turn 2")  # second turn must resume it

    second = runner.calls[1]
    # --output-format json still present and the model rides alongside it.
    assert second[second.index("--output-format") + 1] == "json"
    assert second[second.index("--model") + 1] == "claude-opus-4-7"
    # --resume still appended at the tail with the captured session id.
    assert second[second.index("--resume") + 1] == "sess-xyz"


# --- Voice / speed (already wired into KokoroSynthesizer.__call__) -------------


def test_tts_voice_and_speed_driven_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """JARVIS_TTS_VOICE / JARVIS_TTS_SPEED reach the settings the Kokoro backend
    reads (KokoroSynthesizer.__call__ passes settings.tts_voice / .tts_speed to the
    pipeline). The native synth is not spawned (pragma no-cover); the settings seam
    is the config-drive contract."""
    monkeypatch.setenv("JARVIS_TTS_VOICE", "bm_lewis")
    monkeypatch.setenv("JARVIS_TTS_SPEED", "1.25")

    settings = get_settings()
    assert settings.tts_voice == "bm_lewis"
    assert settings.tts_speed == 1.25


# --- STT model (already wired into WhisperCppTranscriber.__call__) -------------


def test_stt_model_driven_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """JARVIS_STT_MODEL reaches the transcriber object the whisper backend uses.
    Constructing the transcriber is CI-safe (its __init__ only stores settings);
    __call__ resolves <stt_model_dir>/ggml-<stt_model>.bin from this same field."""
    monkeypatch.setenv("JARVIS_STT_MODEL", "small.en")

    transcriber = WhisperCppTranscriber()
    assert transcriber._settings.stt_model == "small.en"


# --- Defaults sanity ----------------------------------------------------------


def test_permission_mode_default_is_accept_edits() -> None:
    assert get_settings().permission_mode is PermissionMode.accept_edits
