"""Tests for the idle stability soak (Phase 4 goal G4.3).

G4.3's metric is a 1-hour idle run of the always-on loop: 0 crashes and memory
growth <= 50 MB. "Idle" is the daemon parked in ``wait_for_wake_phrase``,
scoring every mic frame through openWakeWord. The pure :func:`soak` core drives
that scoring against an injected frame source, samples RSS at intervals off an
injected sampler + clock, tallies crashes (a scoring exception is counted, not
propagated, so the run completes and is recorded), and reports memory growth
from a **post-settle steady-state baseline** — so G4.2's background warm-up
(torch + Kokoro loading in the first seconds) is not mistaken for a leak.

Everything is injected, so the accounting is exercised with deterministic fakes:
no microphone, no model, no real RSS, no real time. Written before the soak path
exists in ``scripts/soak_idle.py`` (TDD, per ADR-0005).
"""

from __future__ import annotations

import soak_idle


def _stepping_clock(step: float = 1.0):  # type: ignore[no-untyped-def]
    """A clock that advances by ``step`` on each call (first call returns 0)."""
    t = [-step]

    def clock() -> float:
        t[0] += step
        return t[0]

    return clock


def _series_sampler(values: list[float]):  # type: ignore[no-untyped-def]
    """An RSS sampler that returns the next scripted value on each call."""
    it = iter(values)
    return lambda: next(it)


def _silence() -> bytes:
    return b"\x00" * 2560  # one 1280-sample PCM16 frame


def test_growth_measured_from_steady_state_baseline_not_launch() -> None:
    """A warm-up RSS spike before ``settle_s`` must not count toward growth.

    Clock steps 1 s/iteration over a 5 s run sampling every 1 s -> samples at
    elapsed 0,1,2,3,4,5. With settle_s=2 the launch spike (elapsed 0,1) is
    excluded; growth is measured across the post-settle window only.
    """
    result = soak_idle.soak(
        score_frame=lambda _frame: 0.0,
        next_frame=_silence,
        sample_rss_mb=_series_sampler([200.0, 400.0, 150.0, 151.0, 152.0, 153.0]),
        duration_s=5.0,
        sample_interval_s=1.0,
        settle_s=2.0,
        clock=_stepping_clock(1.0),
    )
    assert result.rss_start_mb == 150.0  # baseline = first post-settle sample
    assert result.rss_end_mb == 153.0
    assert result.rss_peak_mb == 153.0  # the 400 warm-up spike is excluded
    assert result.rss_growth_mb == 3.0


def test_flat_memory_reports_zero_growth() -> None:
    result = soak_idle.soak(
        score_frame=lambda _frame: 0.0,
        next_frame=_silence,
        sample_rss_mb=_series_sampler([120.0] * 6),
        duration_s=5.0,
        sample_interval_s=1.0,
        settle_s=2.0,
        clock=_stepping_clock(1.0),
    )
    assert result.rss_growth_mb == 0.0


def test_rising_memory_is_detected_as_growth() -> None:
    result = soak_idle.soak(
        score_frame=lambda _frame: 0.0,
        next_frame=_silence,
        sample_rss_mb=_series_sampler([100.0, 110.0, 120.0, 140.0, 170.0, 210.0]),
        duration_s=5.0,
        sample_interval_s=1.0,
        settle_s=2.0,
        clock=_stepping_clock(1.0),
    )
    # post-settle window is samples at elapsed 2,3,4,5 -> [120,140,170,210].
    assert result.rss_start_mb == 120.0
    assert result.rss_end_mb == 210.0
    assert result.rss_growth_mb == 90.0


def test_scoring_exception_is_counted_as_a_crash_not_raised() -> None:
    """A transient scoring error is tallied and the soak keeps going."""
    calls = {"n": 0}

    def flaky(_frame: bytes) -> float:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("model blew up")
        return 0.0

    result = soak_idle.soak(
        score_frame=flaky,
        next_frame=_silence,
        sample_rss_mb=_series_sampler([100.0] * 6),
        duration_s=5.0,
        sample_interval_s=1.0,
        settle_s=2.0,
        clock=_stepping_clock(1.0),
    )
    assert result.crashes == 1
    assert result.frames_scanned == 5  # all five scoring iterations still ran


def test_deadline_bounds_frames_and_elapsed() -> None:
    result = soak_idle.soak(
        score_frame=lambda _frame: 0.0,
        next_frame=_silence,
        sample_rss_mb=_series_sampler([100.0] * 6),
        duration_s=5.0,
        sample_interval_s=1.0,
        settle_s=2.0,
        clock=_stepping_clock(1.0),
    )
    assert result.frames_scanned == 5  # scored on iterations at elapsed 0..4; 5 breaks
    assert result.elapsed_s == 5.0
    assert result.crashes == 0


def test_passes_when_no_crashes_and_growth_within_budget() -> None:
    result = soak_idle.SoakResult(
        elapsed_s=3600.0,
        frames_scanned=45000,
        rss_start_mb=140.0,
        rss_peak_mb=150.0,
        rss_end_mb=148.0,
        crashes=0,
    )
    assert result.rss_growth_mb == 8.0
    assert result.passed(growth_budget_mb=50.0) is True


def test_fails_when_growth_exceeds_budget_or_a_crash_occurred() -> None:
    leaky = soak_idle.SoakResult(
        elapsed_s=3600.0,
        frames_scanned=45000,
        rss_start_mb=140.0,
        rss_peak_mb=210.0,
        rss_end_mb=205.0,
        crashes=0,
    )
    crashed = soak_idle.SoakResult(
        elapsed_s=3600.0,
        frames_scanned=45000,
        rss_start_mb=140.0,
        rss_peak_mb=145.0,
        rss_end_mb=142.0,
        crashes=1,
    )
    assert leaky.passed(growth_budget_mb=50.0) is False  # 65 MB growth
    assert crashed.passed(growth_budget_mb=50.0) is False  # a crash fails outright
