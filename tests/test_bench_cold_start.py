"""Tests for the cold-start benchmark path (Phase 4 goal G4.2).

G4.2's metric is boot -> ready-for-wake-word <= 10 s. "Ready" is defined as the
moment the always-on loop can block in ``wait_for_wake`` on a working mic — i.e.
the persistent mic is open and the openWakeWord listener is loaded. The heavier
components (Silero VAD endpointer, Kokoro synthesizer, the barge-in watcher) are
*not* needed to hear "hey jarvis"; they are warmed in the background after
readiness, so their build cost must be excluded from ``ready_s``.

Each component is built behind an injected timer that returns its elapsed
seconds, so here the readiness accounting is exercised with deterministic fakes:
no torch, no Kokoro, no openWakeWord, no microphone. Written before the
cold-start path exists in ``scripts/bench_latency.py`` (TDD, per ADR-0005).
"""

from __future__ import annotations

import bench_latency
import pytest


def _const(value: float) -> object:
    """A stage timer that always reports the same elapsed build seconds."""
    return lambda: value


def _stages(
    *, mic: float, wake: float, vad: float, tts: float, barge_in: float
) -> bench_latency.ColdStartStages:
    return bench_latency.ColdStartStages(
        build_mic=_const(mic),  # type: ignore[arg-type]
        build_wake=_const(wake),  # type: ignore[arg-type]
        build_vad=_const(vad),  # type: ignore[arg-type]
        build_tts=_const(tts),  # type: ignore[arg-type]
        build_barge_in=_const(barge_in),  # type: ignore[arg-type]
    )


def test_ready_counts_only_the_mic_and_wake_listener() -> None:
    """``ready_s`` gates on the wake path alone — mic open + openWakeWord load."""
    result = bench_latency.measure_cold_start(
        _stages(mic=0.2, wake=1.3, vad=4.0, tts=6.0, barge_in=1.5)
    )
    assert result.ready_s == pytest.approx(0.2 + 1.3)


def test_ready_excludes_the_deferred_components() -> None:
    """The deferred heavy loads (Silero/Kokoro/barge-in) never inflate ``ready_s``.

    This is the whole point of G4.2: torch/Kokoro/Silero sit off the critical
    path. Inflating the deferred builds must not move the readiness figure.
    """
    fast = bench_latency.measure_cold_start(
        _stages(mic=0.2, wake=1.3, vad=4.0, tts=6.0, barge_in=1.5)
    )
    slow_deferred = bench_latency.measure_cold_start(
        _stages(mic=0.2, wake=1.3, vad=40.0, tts=60.0, barge_in=15.0)
    )
    assert fast.ready_s == pytest.approx(slow_deferred.ready_s)


def test_deferred_breakdown_is_captured_per_component() -> None:
    result = bench_latency.measure_cold_start(
        _stages(mic=0.2, wake=1.3, vad=4.0, tts=6.0, barge_in=1.5)
    )
    assert result.deferred_s == pytest.approx({"vad": 4.0, "tts": 6.0, "barge_in": 1.5})


def test_warm_total_sums_readiness_and_deferred() -> None:
    """The full warm cost is reported too, so the deferral's savings are visible."""
    result = bench_latency.measure_cold_start(
        _stages(mic=0.2, wake=1.3, vad=4.0, tts=6.0, barge_in=1.5)
    )
    assert result.warm_total_s == pytest.approx(0.2 + 1.3 + 4.0 + 6.0 + 1.5)


def test_measure_cold_start_builds_each_component_exactly_once() -> None:
    calls = {"mic": 0, "wake": 0, "vad": 0, "tts": 0, "barge_in": 0}

    def counted(key: str, value: float):  # type: ignore[no-untyped-def]
        def timer() -> float:
            calls[key] += 1
            return value

        return timer

    stages = bench_latency.ColdStartStages(
        build_mic=counted("mic", 0.2),
        build_wake=counted("wake", 1.3),
        build_vad=counted("vad", 4.0),
        build_tts=counted("tts", 6.0),
        build_barge_in=counted("barge_in", 1.5),
    )
    bench_latency.measure_cold_start(stages)
    assert calls == {"mic": 1, "wake": 1, "vad": 1, "tts": 1, "barge_in": 1}


def test_run_cold_start_benchmark_aggregates_readiness_into_a_latency_result() -> None:
    """A varying wake-load drives a known readiness distribution; assert p50/p95.

    Cold start is fundamentally a first-process point measurement, but the runner
    reuses :class:`LatencyResult` so repeated samples (e.g. across reboots, fed
    one per run) get the same percentile helpers as the other modes.
    """
    wakes = iter([1.0, 1.5, 2.0]).__next__

    stages = bench_latency.ColdStartStages(
        build_mic=_const(0.2),  # type: ignore[arg-type]
        build_wake=wakes,
        build_vad=_const(4.0),  # type: ignore[arg-type]
        build_tts=_const(6.0),  # type: ignore[arg-type]
        build_barge_in=_const(1.5),  # type: ignore[arg-type]
    )
    ready, breakdown = bench_latency.run_cold_start_benchmark(runs=3, stages=stages)
    assert ready.runs == 3
    assert ready.samples_s == pytest.approx([1.2, 1.7, 2.2])  # mic 0.2 + each wake
    assert ready.median_s == pytest.approx(1.7)
    assert ready.p95_s == pytest.approx(2.2)  # nearest-rank: ceil(0.95*3)=3 -> last
    # The deferred breakdown is reported from the (canonical) first run.
    assert breakdown.deferred_s == pytest.approx({"vad": 4.0, "tts": 6.0, "barge_in": 1.5})


def test_format_cold_start_summary_reports_readiness_target_and_breakdown() -> None:
    ready = bench_latency.LatencyResult(runs=1, samples_s=[1.5])
    breakdown = bench_latency.ColdStartResult(
        ready_s=1.5, deferred_s={"vad": 4.0, "tts": 6.0, "barge_in": 1.5}
    )
    summary = bench_latency._format_cold_start_summary(ready, breakdown, target_s=10.0)
    assert "cold start" in summary.lower()
    assert "ready" in summary.lower()
    assert "10" in summary  # the target is named
    assert "PASS" in summary  # 1.5 s is under the 10 s target
    # The deferred components are named so the deferral's effect is visible.
    assert "vad" in summary.lower()
    assert "tts" in summary.lower()
    assert "barge_in" in summary.lower()


def test_format_cold_start_summary_flags_a_miss_over_target() -> None:
    ready = bench_latency.LatencyResult(runs=1, samples_s=[15.0])
    breakdown = bench_latency.ColdStartResult(ready_s=15.0, deferred_s={})
    summary = bench_latency._format_cold_start_summary(ready, breakdown, target_s=10.0)
    assert "FAIL" in summary
