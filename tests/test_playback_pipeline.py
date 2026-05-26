"""Tests for the smooth streaming-playback pipeline (Phase 4 goal G4.6).

Written before the ``jarvis.loop`` synth-stage rewrite (TDD, per ADR-0005). The
live check showed multi-sentence replies stalled because synthesis and playback
were serialized in one thread. The fix makes SPEAKING a three-stage pipeline —
tokens → sentences → audio → speaker — so sentence N+1 is rendered *while* N is
playing (no inter-sentence gap), and the speaker drains buffered audio on a clean
finish but is aborted on barge-in. Every edge is injected; no hardware.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator

from jarvis.audio import Clip
from jarvis.loop import VoiceLoop


def _loop(stream: object, speaker: object, **kw: object) -> VoiceLoop:
    return VoiceLoop(
        record_turn=lambda: Clip(samples=b"\x00\x00", sample_rate=16_000),
        transcribe=lambda _c: "go",
        stream=stream,  # type: ignore[arg-type]
        synthesize=lambda text: Clip(samples=text.encode(), sample_rate=24_000),
        speaker=speaker,  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


def test_synthesis_runs_ahead_of_playback() -> None:
    """Sentence N+1 is synthesized while N is still playing (pipelined)."""
    second_synthesized = threading.Event()

    def synthesize(text: str) -> Clip:
        if text.strip() == "Second.":
            second_synthesized.set()
        return Clip(samples=text.encode(), sample_rate=24_000)

    class FirstPlayBlocksUntilSecondSynthesized:
        def __init__(self) -> None:
            self.played: list[Clip] = []

        def play(self, clip: Clip) -> None:
            self.played.append(clip)
            if len(self.played) == 1:
                # If synthesis is pipelined, "Second." is already rendered while
                # "First." plays. If it were serialized in this consumer thread,
                # nothing would synthesize "Second." until play() returns — so this
                # wait would time out and fail.
                assert second_synthesized.wait(timeout=5), "synthesis was not pipelined ahead"

        def stop(self) -> None: ...

    speaker = FirstPlayBlocksUntilSecondSynthesized()
    loop = VoiceLoop(
        record_turn=lambda: Clip(samples=b"\x00\x00", sample_rate=16_000),
        transcribe=lambda _c: "go",
        stream=lambda _p: iter(["First. ", "Second. "]),
        synthesize=synthesize,
        speaker=speaker,
    )
    turn = loop.one_turn()

    assert turn.reply == "First. Second."
    assert [c.samples.decode().strip() for c in speaker.played] == ["First.", "Second."]


def test_speaker_drained_on_clean_finish() -> None:
    """On a normal end the speaker is drained once, so buffered audio is heard."""
    events: list[str] = []

    class DrainingSpeaker:
        def __init__(self) -> None:
            self.played: list[Clip] = []

        def play(self, clip: Clip) -> None:
            self.played.append(clip)
            events.append("play")

        def stop(self) -> None: ...

        def wait(self) -> None:
            events.append("drain")

    speaker = DrainingSpeaker()
    _loop(lambda _p: iter(["One. ", "Two. "]), speaker).one_turn()

    assert len(speaker.played) == 2
    assert events == ["play", "play", "drain"]  # drained once, after both clips


def test_speaker_not_drained_when_nothing_spoken() -> None:
    """A code-only reply never enters SPEAKING, so the speaker is not drained."""
    events: list[str] = []

    class DrainingSpeaker:
        def play(self, clip: Clip) -> None:
            events.append("play")

        def stop(self) -> None: ...

        def wait(self) -> None:
            events.append("drain")

    # An unclosed code fence is withheld by SentenceStreamer → no sentence voiced.
    _loop(lambda _p: iter(["```python\n", "x = 1\n"]), DrainingSpeaker()).one_turn()

    assert events == []


def test_synthesis_error_propagates() -> None:
    """A failure in the synth stage surfaces to the caller instead of hanging."""

    def synthesize(_text: str) -> Clip:
        raise RuntimeError("kokoro blew up")

    class Speaker:
        def play(self, clip: Clip) -> None: ...

        def stop(self) -> None: ...

    loop = VoiceLoop(
        record_turn=lambda: Clip(samples=b"\x00\x00", sample_rate=16_000),
        transcribe=lambda _c: "go",
        stream=lambda _p: iter(["One sentence. "]),
        synthesize=synthesize,
        speaker=Speaker(),
    )
    try:
        loop.one_turn()
    except RuntimeError as exc:
        assert "kokoro blew up" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected the synth error to propagate")


def test_speaker_aborted_not_drained_on_barge_in() -> None:
    """Barge-in aborts buffered audio (stop), it does not drain (wait)."""
    fire = threading.Event()

    def watcher(on_onset: Callable[[], None], stop: threading.Event) -> None:
        assert fire.wait(timeout=5)
        on_onset()

    events: list[str] = []

    class StoppableDrainSpeaker:
        def __init__(self) -> None:
            self.played: list[Clip] = []
            self._release = threading.Event()

        def play(self, clip: Clip) -> None:
            self.played.append(clip)
            fire.set()  # spring the watcher while sentence one is on the speaker
            assert self._release.wait(timeout=5)

        def stop(self) -> None:
            events.append("abort")
            self._release.set()

        def wait(self) -> None:
            events.append("drain")

    def stream(_p: str) -> Iterator[str]:
        yield "Sentence one. "
        yield "Sentence two. "

    speaker = StoppableDrainSpeaker()
    turn = _loop(stream, speaker, watch_barge_in=watcher).one_turn()

    assert turn.barged_in is True
    assert events == ["abort"]  # aborted, never drained
    assert turn.reply == "Sentence one."
