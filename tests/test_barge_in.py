"""Tests for barge-in: a cancellable SPEAKING state (Phase 3 goal G3.1).

Written before the ``jarvis.loop`` barge-in support exists (TDD, per ADR-0005).
The G3.1 target: while Jarvis is SPEAKING the mic stays hot, and speech onset
cancels playback within 300 ms *and* tears down the in-flight ``claude`` stream —
no further sentences are spoken — returning the machine to LISTENING.

Three concurrent edges are injected so the contract is exercised without
hardware: the token stream (a fake generator whose ``finally`` records that it was
closed — in the real :class:`~jarvis.brain.Brain.stream` that ``GeneratorExit``
terminates the ``claude`` child), the onset watcher (fires on demand instead of
reading a microphone), and a :class:`Speaker` whose ``play`` blocks until ``stop``
aborts it mid-clip — the seam that lets barge-in beat the sentence length. The
latency is read off an injected clock, never the wall clock.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator

from jarvis.audio import Clip
from jarvis.loop import State, VoiceLoop


class StoppableSpeaker:
    """A fake speaker whose ``play`` blocks (a clip "playing") until ``stop``.

    ``stop`` releases the in-flight ``play`` immediately, modelling the real
    ``sd.stop()`` aborting ``sd.wait()`` mid-clip. With ``block=False`` it is a
    plain recorder for the no-interrupt path. ``on_first_play`` fires once, as the
    first clip starts — the tests use it to spring the onset watcher precisely
    while sentence one is on the speaker.
    """

    def __init__(
        self, *, block: bool = True, on_first_play: Callable[[], None] | None = None
    ) -> None:
        self.played: list[Clip] = []
        self.stopped = False
        self._block = block
        self._on_first_play = on_first_play
        self._release = threading.Event()

    def play(self, clip: Clip) -> None:
        self.played.append(clip)
        if self._on_first_play is not None and len(self.played) == 1:
            self._on_first_play()
        if self._block:
            assert self._release.wait(timeout=5), "play() was not released by stop()"

    def stop(self) -> None:
        self.stopped = True
        self._release.set()


class FakeBargeInWatcher:
    """A barge-in watcher that fires onset on command instead of reading a mic.

    The loop runs it in a thread with ``(on_onset, stop)``; it waits until the
    test sets :attr:`fire`, then calls ``on_onset`` once. If SPEAKING ends first
    the loop sets ``stop`` and the watcher returns without firing — proving the
    watcher is torn down rather than left running.
    """

    def __init__(self) -> None:
        self.fire = threading.Event()
        self.returned = threading.Event()

    def __call__(self, on_onset: Callable[[], None], stop: threading.Event) -> None:
        while not self.fire.wait(timeout=0.02):
            if stop.is_set():
                self.returned.set()
                return
        on_onset()
        self.returned.set()


def _clip() -> Clip:
    return Clip(samples=b"\x00\x00", sample_rate=16_000)


def _loop(stream: object, speaker: StoppableSpeaker, **kw: object) -> VoiceLoop:
    return VoiceLoop(
        record_turn=_clip,
        transcribe=lambda _c: "what time is it",
        stream=stream,  # type: ignore[arg-type]
        synthesize=lambda text: Clip(samples=text.encode(), sample_rate=24_000),
        speaker=speaker,
        **kw,  # type: ignore[arg-type]
    )


def test_barge_in_cancels_playback() -> None:
    """Onset while sentence one plays halts it, skips sentence two, returns to LISTENING."""
    states: list[State] = []
    watcher = FakeBargeInWatcher()
    speaker = StoppableSpeaker(on_first_play=watcher.fire.set)

    def stream(_p: str) -> Iterator[str]:
        yield "Sentence one. "
        yield "Sentence two. "

    turn = _loop(stream, speaker, on_state=states.append, watch_barge_in=watcher).one_turn()

    assert speaker.stopped is True  # the player was halted mid-clip
    assert len(speaker.played) == 1  # sentence two never reached the speaker
    assert turn.reply == "Sentence one."
    assert turn.barged_in is True
    # Barge-in returns to LISTENING (the user is now talking), not IDLE.
    assert states == [State.LISTENING, State.THINKING, State.SPEAKING, State.LISTENING]


def test_barge_in_cancels_in_flight_brain() -> None:
    """A mid-stream interrupt closes the token generator and speaks nothing more."""
    closed = threading.Event()
    watcher = FakeBargeInWatcher()
    speaker = StoppableSpeaker(on_first_play=watcher.fire.set)

    def stream(_p: str) -> Iterator[str]:
        try:
            yield "Sentence one. "
            while True:  # in-flight tokens that must never be spoken
                yield "Filler. "
        finally:
            # In the real Brain.stream this GeneratorExit terminates the claude
            # child; here it proves the stream was torn down, not drained.
            closed.set()

    turn = _loop(stream, speaker, watch_barge_in=watcher).one_turn()

    assert closed.wait(timeout=2), "the in-flight token stream was not closed"
    assert turn.barged_in is True
    assert len(speaker.played) == 1
    assert turn.reply == "Sentence one."


def test_no_barge_in_runs_to_completion() -> None:
    """Without an onset, SPEAKING runs to the end — guards the G2.4 overlap."""
    states: list[State] = []
    speaker = StoppableSpeaker(block=False)
    loop = _loop(
        lambda _p: iter(["First sentence. ", "Second sentence."]),
        speaker,
        on_state=states.append,
    )
    turn = loop.one_turn()

    assert turn.barged_in is False
    assert turn.barge_in_latency_s is None
    assert turn.reply == "First sentence. Second sentence."
    assert len(speaker.played) == 2
    assert speaker.stopped is False
    assert states == [State.LISTENING, State.THINKING, State.SPEAKING, State.IDLE]


def test_barge_in_latency_within_budget() -> None:
    """Onset → player-halt is measured off the injected clock and is ≤ 300 ms."""
    # The clock is read exactly twice on the barge-in path: at onset and once the
    # player is halted. 180 ms apart is within the 300 ms budget.
    times = iter([100.0, 100.18])
    watcher = FakeBargeInWatcher()
    speaker = StoppableSpeaker(on_first_play=watcher.fire.set)
    loop = _loop(
        lambda _p: iter(["One. ", "Two. "]),
        speaker,
        watch_barge_in=watcher,
        clock=lambda: next(times),
    )
    turn = loop.one_turn()

    assert turn.barged_in is True
    assert turn.barge_in_latency_s is not None
    assert 0.0 < turn.barge_in_latency_s <= 0.300
