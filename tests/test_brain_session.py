"""Tests for multi-turn session continuity (Phase 1 goal G1.4).

The brain holds one Claude Code session across turns: the first call returns a
``session_id``; every later call must pass ``--resume <session_id>`` so turn 3
can reference turn 1. The subprocess is injected as a fake runner, so these
tests never spawn ``claude`` or touch the network.
"""

from __future__ import annotations

import json

from jarvis.brain import Brain
from jarvis.config import Settings


class FakeRunner:
    """Records each argv and returns canned ``claude -p`` JSON, in order."""

    def __init__(self, results: list[str]) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        return self._results.pop(0)


def _json(session_id: str, result: str) -> str:
    return json.dumps({"type": "result", "session_id": session_id, "result": result})


def _settings() -> Settings:
    return Settings(claude_binary="claude-test")


def test_first_call_has_no_resume_flag() -> None:
    runner = FakeRunner([_json("sess-abc", "Hello, sir.")])
    brain = Brain(settings=_settings(), runner=runner)
    brain.ask("Hello")
    assert "--resume" not in runner.calls[0]


def test_first_call_captures_session_id() -> None:
    runner = FakeRunner([_json("sess-abc", "Hello, sir.")])
    brain = Brain(settings=_settings(), runner=runner)
    reply = brain.ask("Hello")
    assert reply.session_id == "sess-abc"


def test_second_call_resumes_first_session() -> None:
    runner = FakeRunner(
        [
            _json("sess-abc", "My name is Jarvis, sir."),
            _json("sess-abc", "You said your name was Ty."),
        ]
    )
    brain = Brain(settings=_settings(), runner=runner)
    brain.ask("My name is Ty.")
    brain.ask("What is my name?")
    assert runner.calls[1].count("--resume") == 1
    idx = runner.calls[1].index("--resume")
    assert runner.calls[1][idx + 1] == "sess-abc"


def test_third_call_still_resumes_same_session() -> None:
    runner = FakeRunner([_json("sess-abc", f"turn {n}") for n in range(3)])
    brain = Brain(settings=_settings(), runner=runner)
    brain.ask("turn 1")
    brain.ask("turn 2")
    brain.ask("turn 3")
    idx = runner.calls[2].index("--resume")
    assert runner.calls[2][idx + 1] == "sess-abc"


def test_argv_carries_prompt_format_and_permission_mode() -> None:
    runner = FakeRunner([_json("s", "ok")])
    brain = Brain(settings=_settings(), runner=runner)
    brain.ask("ping")
    argv = runner.calls[0]
    assert argv[0] == "claude-test"
    assert "-p" in argv
    assert "ping" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    # Settings default permission mode is acceptEdits.
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_reply_text_is_speakable() -> None:
    runner = FakeRunner([_json("s", "Done.\n```py\nx=1\n```\nSaved, sir.")])
    brain = Brain(settings=_settings(), runner=runner)
    reply = brain.ask("write x")
    assert "```" not in reply.text
    assert "x=1" not in reply.text
    assert "Saved, sir." in reply.text
