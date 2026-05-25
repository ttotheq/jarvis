"""Tests for the streaming brain (Phase 2 goal G2.4).

Written before ``jarvis.brain.Brain.stream`` exists (TDD, per ADR-0005). The
streaming brain drives ``claude -p --output-format stream-json
--include-partial-messages`` and yields assistant *text* deltas as they arrive,
so TTS can begin on the first sentence. Tool-call deltas and session/init events
are not text and must not be yielded. The subprocess is injected as a line
iterator, so these tests never spawn ``claude`` or touch the network.
"""

from __future__ import annotations

import json

from jarvis.brain import Brain
from jarvis.config import Settings


class FakeStreamRunner:
    """Records each argv and replays canned ``stream-json`` lines, in order."""

    def __init__(self, line_groups: list[list[str]]) -> None:
        self._groups = list(line_groups)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> list[str]:
        self.calls.append(list(argv))
        return self._groups.pop(0)


def _text_delta(text: str, session_id: str = "sess-1") -> str:
    return json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
            "session_id": session_id,
        }
    )


def _result(text: str, session_id: str = "sess-1") -> str:
    return json.dumps(
        {"type": "result", "subtype": "success", "result": text, "session_id": session_id}
    )


def _settings() -> Settings:
    return Settings(claude_binary="claude-test")


def test_stream_yields_text_deltas_in_order() -> None:
    lines = [
        _text_delta("Hello there"),
        _text_delta(". How are you?"),
        _result("Hello there. How are you?"),
    ]
    brain = Brain(settings=_settings(), stream_runner=FakeStreamRunner([lines]))
    assert list(brain.stream("hi")) == ["Hello there", ". How are you?"]


def test_stream_skips_non_text_events() -> None:
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}),
        json.dumps(
            {"type": "stream_event", "event": {"type": "message_start"}, "session_id": "sess-1"}
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                },
                "session_id": "sess-1",
            }
        ),
        _text_delta("Right away, sir."),
        _result("Right away, sir."),
    ]
    brain = Brain(settings=_settings(), stream_runner=FakeStreamRunner([lines]))
    assert list(brain.stream("go")) == ["Right away, sir."]


def test_stream_argv_uses_stream_json_and_partial_messages() -> None:
    runner = FakeStreamRunner([[_result("ok")]])
    brain = Brain(settings=_settings(), stream_runner=runner)
    list(brain.stream("ping"))
    argv = runner.calls[0]
    assert argv[0] == "claude-test"
    assert "-p" in argv and "ping" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" in argv
    assert "--verbose" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_stream_first_call_has_no_resume() -> None:
    runner = FakeStreamRunner([[_text_delta("hi"), _result("hi")]])
    brain = Brain(settings=_settings(), stream_runner=runner)
    list(brain.stream("hello"))
    assert "--resume" not in runner.calls[0]


def test_stream_captures_and_resumes_session() -> None:
    runner = FakeStreamRunner(
        [
            [
                _text_delta("My name is Jarvis.", "sess-abc"),
                _result("My name is Jarvis.", "sess-abc"),
            ],
            [_text_delta("You are Ty.", "sess-abc"), _result("You are Ty.", "sess-abc")],
        ]
    )
    brain = Brain(settings=_settings(), stream_runner=runner)
    list(brain.stream("who are you"))
    assert brain.session_id == "sess-abc"
    list(brain.stream("who am I"))
    idx = runner.calls[1].index("--resume")
    assert runner.calls[1][idx + 1] == "sess-abc"


def test_stream_stops_at_result_event() -> None:
    # Anything after the result event is not part of this turn's output.
    lines = [_text_delta("Done."), _result("Done."), _text_delta("LEAKED")]
    brain = Brain(settings=_settings(), stream_runner=FakeStreamRunner([lines]))
    assert list(brain.stream("x")) == ["Done."]


def test_stream_ignores_malformed_lines() -> None:
    lines = ["not json at all", "", _text_delta("Fine."), _result("Fine.")]
    brain = Brain(settings=_settings(), stream_runner=FakeStreamRunner([lines]))
    assert list(brain.stream("x")) == ["Fine."]
