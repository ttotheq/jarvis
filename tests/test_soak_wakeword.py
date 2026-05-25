"""Tests for the ambient false-accept soak (scripts/soak_wakeword.py, G2.1).

The mic and openWakeWord model are hardware shims; the testable core is
:func:`soak`, which drives an injected frame source + listener + clock and tallies
*distinct* false-wake events (a single wake that spans several frames counts once).
Its count over a 30-minute run is recorded in the phase doc Outcomes. Fakes stand
in for the hardware here.
"""

from __future__ import annotations

from collections.abc import Callable

from soak_wakeword import SoakResult, soak

from jarvis.wakeword import FRAME_BYTES, WakeWordListener

_FRAME = b"\x00" * FRAME_BYTES


def _listener_from(scores: list[float]) -> WakeWordListener:
    it = iter(scores)
    return WakeWordListener(detect=lambda _frame: next(it), threshold=0.5)


def _clock(ticks: list[float]) -> Callable[[], float]:
    it = iter(ticks)
    return lambda: next(it)


def test_soak_counts_a_burst_as_one_false_accept() -> None:
    # Score stays above threshold for three consecutive frames: one wake event.
    listener = _listener_from([0.1, 0.8, 0.9, 0.85, 0.1])
    result = soak(listener, [_FRAME] * 5, duration_s=100.0, clock=_clock([0, 1, 2, 3, 4, 5]))
    assert result.false_accepts == 1
    assert result.frames_scanned == 5


def test_soak_counts_separated_events_distinctly() -> None:
    # Two bursts separated by a sub-threshold frame: two distinct false accepts.
    listener = _listener_from([0.9, 0.1, 0.9])
    result = soak(listener, [_FRAME] * 3, duration_s=100.0, clock=_clock([0, 1, 2, 3]))
    assert result.false_accepts == 2


def test_soak_silent_ambient_has_zero_false_accepts() -> None:
    listener = _listener_from([0.0, 0.1, 0.2, 0.05])
    result = soak(listener, [_FRAME] * 4, duration_s=100.0, clock=_clock([0, 1, 2, 3, 4]))
    assert result.false_accepts == 0


def test_soak_stops_at_duration() -> None:
    # Deadline (3.0) is reached before the frame stream is exhausted.
    listener = _listener_from([0.0, 0.0, 0.0, 0.9])
    result = soak(listener, [_FRAME] * 4, duration_s=3.0, clock=_clock([0.0, 1.0, 2.0, 2.5, 3.0]))
    assert result.frames_scanned == 3  # the 4th frame (the wake) is never reached
    assert result.false_accepts == 0
    assert result.elapsed_s == 3.0


def test_false_accepts_per_30min_projection() -> None:
    half_hour = SoakResult(false_accepts=1, elapsed_s=1800.0, frames_scanned=1)
    quarter_hour = SoakResult(false_accepts=3, elapsed_s=900.0, frames_scanned=1)
    empty = SoakResult(false_accepts=0, elapsed_s=0.0, frames_scanned=0)
    assert half_hour.false_accepts_per_30min == 1.0
    assert quarter_hour.false_accepts_per_30min == 6.0
    assert empty.false_accepts_per_30min == 0.0  # a zero-length run never divides by zero
