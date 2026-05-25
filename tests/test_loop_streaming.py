"""Tests for the streaming state-machine loop (Phase 2 goal G2.4).

Written before the ``jarvis.loop`` rewrite (TDD, per ADR-0005). G2.4's target:
the first complete sentence is *spoken* before Claude's full response completes
— i.e. THINKING and SPEAKING overlap. Overlap is concurrency (a producer
streaming tokens into a sentence queue, a consumer speaking them), so the proof
here is an *ordering* one: the first sentence is played before the stream has
produced its remaining tokens. Every edge is injected; no hardware, no network.
"""

from __future__ import annotations

import threading

from jarvis.audio import Clip
from jarvis.loop import State, VoiceLoop


class RecordingSpeaker:
    def __init__(self, on_play: object = None) -> None:
        self.played: list[Clip] = []
        self._on_play = on_play

    def play(self, clip: Clip) -> None:
        self.played.append(clip)
        if callable(self._on_play):
            self._on_play()


def _clip() -> Clip:
    return Clip(samples=b"\x00\x00", sample_rate=16_000)


def _loop(stream: object, speaker: RecordingSpeaker, **kw: object) -> VoiceLoop:
    return VoiceLoop(
        record_turn=_clip,
        transcribe=lambda _c: "what time is it",
        stream=stream,  # type: ignore[arg-type]
        synthesize=lambda text: Clip(samples=text.encode(), sample_rate=24_000),
        speaker=speaker,
        **kw,  # type: ignore[arg-type]
    )


def test_loop_speaks_first_sentence_before_completion() -> None:
    """The first sentence is spoken before the stream finishes producing tokens."""
    events: list[str] = []
    release = threading.Event()

    def stream(_prompt: str):  # type: ignore[no-untyped-def]
        yield "First sentence. "
        events.append("stream:first-done")
        # Block until the first sentence has been spoken. If overlap works, the
        # consumer plays it now — before these remaining tokens are produced.
        assert release.wait(timeout=5), "first sentence was not spoken before completion"
        events.append("stream:resumed")
        yield "Second sentence."

    speaker = RecordingSpeaker(on_play=lambda: (events.append("speak"), release.set()))
    turn = _loop(stream, speaker).one_turn()

    # TTS fired at the first sentence boundary, not at the end of the stream.
    assert events.index("speak") < events.index("stream:resumed")
    # Both sentences were ultimately spoken.
    assert turn.reply == "First sentence. Second sentence."
    assert turn.spoke is True
    assert len(speaker.played) == 2


def test_state_transitions() -> None:
    states: list[State] = []
    speaker = RecordingSpeaker()
    loop = _loop(
        lambda _p: iter(["Half past three, sir. ", "Anything else?"]),
        speaker,
        on_state=states.append,
    )
    loop.one_turn()
    assert states == [State.LISTENING, State.THINKING, State.SPEAKING, State.IDLE]


def test_blank_transcript_skips_thinking_and_speaking() -> None:
    states: list[State] = []
    called = {"stream": False}

    def stream(_p: str):  # type: ignore[no-untyped-def]
        called["stream"] = True
        yield "should not happen"

    speaker = RecordingSpeaker()
    loop = VoiceLoop(
        record_turn=_clip,
        transcribe=lambda _c: "   ",
        stream=stream,
        synthesize=lambda text: Clip(samples=b"", sample_rate=24_000),
        speaker=speaker,
        on_state=states.append,
    )
    turn = loop.one_turn()
    assert turn.spoke is False
    assert turn.reply == ""
    assert called["stream"] is False
    assert states == [State.LISTENING, State.IDLE]
    assert speaker.played == []


def test_code_only_reply_is_never_spoken() -> None:
    """A reply that is all code (fence opens, never closes) speaks nothing."""
    states: list[State] = []
    speaker = RecordingSpeaker()
    loop = _loop(
        lambda _p: iter(["```python\n", "secret = 42\n"]),
        speaker,
        on_state=states.append,
    )
    turn = loop.one_turn()
    assert turn.spoke is False
    assert speaker.played == []
    # Reached THINKING but never SPEAKING.
    assert states == [State.LISTENING, State.THINKING, State.IDLE]


def test_converse_runs_five_consecutive_turns() -> None:
    speaker = RecordingSpeaker()
    loop = _loop(lambda _p: iter(["Answer, sir. "]), speaker)
    turns = loop.converse(should_continue=lambda done: done < 5)
    assert len(turns) == 5
    assert all(t.reply == "Answer, sir." for t in turns)
    assert len(speaker.played) == 5


def test_producer_error_propagates() -> None:
    def stream(_p: str):  # type: ignore[no-untyped-def]
        yield "Starting. "
        raise RuntimeError("brain blew up")

    speaker = RecordingSpeaker()
    loop = _loop(stream, speaker)
    try:
        loop.one_turn()
    except RuntimeError as exc:
        assert "brain blew up" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected the producer error to propagate")
