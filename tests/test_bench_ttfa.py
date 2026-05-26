"""Tests for the time-to-first-audio benchmark path (Phase 2 goal G2.3).

The live script composes the real cascade — the ``vad_silence_ms`` hangover, then
whisper.cpp STT, then ``claude -p`` first-token, then Kokoro's first audio chunk —
and times, per run, end-of-speech to the first TTS sample. Each stage is an
injected callable that returns its own elapsed seconds, so here the timing and
aggregation are exercised with deterministic fakes: no whisper, no claude, no
Kokoro. Written before the TTFA path exists in ``scripts/bench_latency.py``
(TDD, per ADR-0005).

The first-audio cascade is strictly sequential (STT waits on the endpoint, the
brain waits on the transcript, TTS waits on the first sentence), so TTFA is the
*sum* of the stage costs — the G2.4 streaming overlap shortens total turn time,
not time-to-*first*-audio.
"""

from __future__ import annotations

import bench_latency
import pytest


def _const(value: float) -> object:
    """A stage timer that always reports the same elapsed seconds."""
    return lambda: value


def test_measure_ttfa_sums_the_stage_costs() -> None:
    stages = bench_latency.TtfaStages(
        hangover_s=0.7,
        transcribe=_const(0.3),  # type: ignore[arg-type]
        brain_ttft=_const(2.8),  # type: ignore[arg-type]
        tts_first_chunk=_const(0.2),  # type: ignore[arg-type]
    )
    assert bench_latency.measure_ttfa(stages) == pytest.approx(0.7 + 0.3 + 2.8 + 0.2)


def test_measure_ttfa_excludes_hangover_when_disabled() -> None:
    """``include_hangover=False`` measures endpoint-fire -> first audio instead."""
    stages = bench_latency.TtfaStages(
        hangover_s=0.7,
        transcribe=_const(0.3),  # type: ignore[arg-type]
        brain_ttft=_const(2.8),  # type: ignore[arg-type]
        tts_first_chunk=_const(0.2),  # type: ignore[arg-type]
    )
    assert bench_latency.measure_ttfa(stages, include_hangover=False) == pytest.approx(
        0.3 + 2.8 + 0.2
    )


def test_measure_ttfa_runs_each_stage_exactly_once() -> None:
    calls = {"transcribe": 0, "brain": 0, "tts": 0}

    def counted(key: str, value: float):  # type: ignore[no-untyped-def]
        def timer() -> float:
            calls[key] += 1
            return value

        return timer

    stages = bench_latency.TtfaStages(
        hangover_s=0.7,
        transcribe=counted("transcribe", 0.3),
        brain_ttft=counted("brain", 2.8),
        tts_first_chunk=counted("tts", 0.2),
    )
    bench_latency.measure_ttfa(stages)
    assert calls == {"transcribe": 1, "brain": 1, "tts": 1}


def test_run_ttfa_benchmark_aggregates_the_per_run_distribution() -> None:
    """A varying brain stage drives a known TTFA distribution; assert p50/p95."""
    # Brain TTFT varies run to run; the other stages are fixed. With hangover
    # 0.7 + STT 0.3 + TTS 0.2 = 1.2 s of fixed cost, each run's TTFA is 1.2 + ttft.
    ttfts = iter([2.0, 2.5, 3.0, 3.5, 4.0]).__next__
    stages = bench_latency.TtfaStages(
        hangover_s=0.7,
        transcribe=_const(0.3),  # type: ignore[arg-type]
        brain_ttft=ttfts,
        tts_first_chunk=_const(0.2),  # type: ignore[arg-type]
    )
    result = bench_latency.run_ttfa_benchmark(runs=5, stages=stages)
    assert result.runs == 5
    assert result.samples_s == pytest.approx([3.2, 3.7, 4.2, 4.7, 5.2])
    assert result.median_s == pytest.approx(4.2)  # median of the five sums
    assert result.p95_s == pytest.approx(5.2)  # nearest-rank: ceil(0.95*5)=5 -> last


def test_run_ttfa_benchmark_can_drop_the_hangover() -> None:
    stages = bench_latency.TtfaStages(
        hangover_s=0.7,
        transcribe=_const(0.3),  # type: ignore[arg-type]
        brain_ttft=_const(2.8),  # type: ignore[arg-type]
        tts_first_chunk=_const(0.2),  # type: ignore[arg-type]
    )
    result = bench_latency.run_ttfa_benchmark(runs=3, stages=stages, include_hangover=False)
    assert result.samples_s == pytest.approx([3.3, 3.3, 3.3])


def test_format_ttfa_summary_reports_percentiles_and_names_the_hangover() -> None:
    result = bench_latency.LatencyResult(runs=20, samples_s=[3.5] * 20)
    summary = bench_latency._format_ttfa_summary(result, include_hangover=True, hangover_ms=700)
    assert "time-to-first-audio" in summary.lower()
    assert "p50" in summary and "p95" in summary
    assert "700 ms" in summary  # the hangover term is named
    assert "included" in summary.lower()


def test_format_ttfa_summary_notes_when_the_hangover_is_excluded() -> None:
    result = bench_latency.LatencyResult(runs=20, samples_s=[2.8] * 20)
    summary = bench_latency._format_ttfa_summary(result, include_hangover=False, hangover_ms=700)
    assert "excluded" in summary.lower()
    assert "endpoint" in summary.lower()  # framed as endpoint-fire -> first audio
