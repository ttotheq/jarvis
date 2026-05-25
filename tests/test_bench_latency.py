"""Tests for the VAD endpoint-latency benchmark (Phase 2 goal G2.2).

The real script feeds frames through a live Silero detector and times the
endpoint decision. Here the detector and clock are injected fakes, so the suite
exercises the timing/aggregation logic deterministically — no torch, no model
(the live measurement runs only when the script is executed directly). Written
before ``scripts/bench_latency.py`` exists, per ADR-0005.
"""

from __future__ import annotations

import bench_latency
import pytest

from jarvis.vad import FRAME_MS, Endpointer


def _speech_then_silence_detector(speech_calls: int):  # type: ignore[no-untyped-def]
    """A detector that scores speech for its first ``speech_calls`` frames."""
    state = {"n": 0}

    def detect(_frame: bytes) -> float:
        state["n"] += 1
        return 0.9 if state["n"] <= speech_calls else 0.1

    return detect


def test_max_silence_frames_covers_the_window_with_margin() -> None:
    # 300 ms / 100 ms = 3 frames to fire, plus the 2-frame guard.
    assert bench_latency.max_silence_frames(300, frame_ms=100.0) == 5


def test_measure_decision_latency_times_only_the_trailing_silence() -> None:
    """The clock starts after the last speech frame; the delta is returned."""
    clock = iter([100.0, 100.123]).__next__
    # 3 speech frames primed, then a 100 ms window at 50 ms/frame = 2 silent frames.
    ep = Endpointer(
        detect=_speech_then_silence_detector(speech_calls=3),
        threshold=0.5,
        silence_ms=100,
        frame_ms=50.0,
    )
    elapsed = bench_latency.measure_decision_latency(
        ep, b"speech", b"silence", speech_frames=3, max_silence=10, clock=clock
    )
    assert elapsed == pytest.approx(0.123)


def test_measure_decision_latency_raises_if_endpoint_never_fires() -> None:
    ep = Endpointer(detect=lambda _f: 0.9, threshold=0.5, silence_ms=100, frame_ms=50.0)
    with pytest.raises(RuntimeError, match="did not fire"):
        bench_latency.measure_decision_latency(
            ep, b"speech", b"silence", speech_frames=1, max_silence=3
        )


def test_run_benchmark_collects_one_sample_per_run() -> None:
    # A monotonic fake clock: every call advances 1 ms, so each run's start->fire
    # delta is deterministic and positive.
    ticks = iter(float(i) / 1000 for i in range(10_000)).__next__
    state = {"n": 0}

    def detect(_frame: bytes) -> float:
        state["n"] += 1
        return 0.9 if state["n"] <= 2 else 0.1  # 2 speech frames, then silence

    result = bench_latency.run_benchmark(
        runs=20,
        detect=detect,
        threshold=0.5,
        silence_ms=100,
        speech_frame=b"s",
        silence_frame=b"q",
        speech_frames=2,
        reset=lambda: state.__setitem__("n", 0),
        clock=ticks,
    )
    assert result.runs == 20
    assert len(result.samples_s) == 20
    assert all(s > 0 for s in result.samples_s)
    assert result.p95_s >= result.median_s >= result.min_s


def test_run_benchmark_resets_a_stateful_detector_each_run() -> None:
    # A stateful detector: without a per-run reset its counter would run past the
    # priming frames and the second run would never see speech, never endpoint.
    state = {"n": 0, "resets": 0}

    def detect(_frame: bytes) -> float:
        state["n"] += 1
        return 0.9 if state["n"] <= 1 else 0.1

    def reset() -> None:
        state["n"] = 0
        state["resets"] += 1

    bench_latency.run_benchmark(
        runs=3,
        detect=detect,
        threshold=0.5,
        silence_ms=100,
        speech_frame=b"s",
        silence_frame=b"q",
        speech_frames=1,
        reset=reset,
    )
    assert state["resets"] == 3


def test_latency_result_percentiles() -> None:
    result = bench_latency.LatencyResult(runs=5, samples_s=[0.01, 0.02, 0.03, 0.04, 0.05])
    assert result.median_s == pytest.approx(0.03)
    assert result.p95_s == pytest.approx(0.05)  # nearest-rank: ceil(0.95*5)=5 -> last
    assert result.mean_s == pytest.approx(0.03)
    assert result.min_s == pytest.approx(0.01)
    assert result.max_s == pytest.approx(0.05)


def test_format_summary_reports_p50_and_notes_the_hangover_is_excluded() -> None:
    result = bench_latency.LatencyResult(runs=20, samples_s=[0.002] * 20)
    summary = bench_latency._format_summary(result, silence_ms=700)
    assert "p50" in summary
    assert "700 ms" in summary  # the hangover is named as excluded
    assert "hangover" in summary.lower()


def test_silence_frame_is_one_vad_frame_of_zeros() -> None:
    frame = bench_latency.make_silence_frame()
    assert frame == b"\x00" * len(frame)
    assert len(frame) == int(FRAME_MS / 1000 * 16_000) * 2
