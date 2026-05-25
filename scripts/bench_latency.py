"""Measure the VAD endpoint *decision* latency (Phase 2 goal G2.2).

ADR-0002 puts a Silero VAD endpointer in the LISTENING state to decide when the
user stopped talking. The acceptance metric is responsiveness: once speech ends,
how much compute does the endpointer spend before it fires and hands off to STT?
This benchmark feeds a synthetic frame stream — speech frames followed by trailing
silence — through :class:`jarvis.vad.Endpointer` and times, per run, the
wall-clock interval from the last speech frame to the endpoint firing.

That interval is the endpointer's *decision compute* (running the detector over
the trailing-silence frames), which is what the architecture's "VAD endpoint
decision: 150-300 ms" budget refers to. It is deliberately **not** the real-time
``vad_silence_ms`` hangover — that pause is a fixed UX tunable, not a latency
cost — and **not** total turn time. Frames are fed as fast as the loop runs.

The detector is injected (like ``scripts/bench_brain.py``'s subprocess runner):
the real Silero model when run live, a fake in ``tests/test_bench_latency.py`` so
the timing/aggregation logic never touches torch. Run live (voice extra)::

    uv run python scripts/bench_latency.py --runs 20
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from jarvis.config import get_settings
from jarvis.vad import FRAME_BYTES, FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE, Detector, Endpointer

DEFAULT_RUNS = 20
#: ~0.5 s of speech to prime the detector and mark the utterance before the pause.
DEFAULT_SPEECH_FRAMES = 16


@dataclass(frozen=True)
class LatencyResult:
    """Aggregated endpoint-decision latencies across a benchmark run."""

    runs: int
    samples_s: list[float]

    @property
    def median_s(self) -> float:
        return statistics.median(self.samples_s)

    @property
    def p95_s(self) -> float:
        """95th percentile by the nearest-rank method (robust for small N)."""
        ordered = sorted(self.samples_s)
        rank = max(1, math.ceil(0.95 * len(ordered)))
        return ordered[rank - 1]

    @property
    def mean_s(self) -> float:
        return statistics.fmean(self.samples_s)

    @property
    def min_s(self) -> float:
        return min(self.samples_s)

    @property
    def max_s(self) -> float:
        return max(self.samples_s)


def max_silence_frames(silence_ms: int, frame_ms: float = FRAME_MS) -> int:
    """Silent frames needed to close the window, plus a small guard margin."""
    return math.ceil(silence_ms / frame_ms) + 2


def make_silence_frame() -> bytes:
    """One frame of digital silence (PCM16 zeros)."""
    return b"\x00" * FRAME_BYTES


def make_speech_frame() -> bytes:  # pragma: no cover - numpy, exercised live only
    """One frame of synthetic voiced audio Silero scores as speech (> 0.5).

    A harmonic stack at a male-voice fundamental (150 Hz + overtones) — verified
    to clear the default threshold, so the live benchmark primes the real model
    without needing a recorded fixture.
    """
    import numpy as np

    t = np.arange(FRAME_SAMPLES) / SAMPLE_RATE
    signal = sum(np.sin(2 * np.pi * f * t) for f in (150, 300, 450, 600)) * 4000
    return signal.astype(np.int16).tobytes()


def measure_decision_latency(
    endpointer: Endpointer,
    speech_frame: bytes,
    silence_frame: bytes,
    *,
    speech_frames: int,
    max_silence: int,
    clock: Callable[[], float] = time.perf_counter,
) -> float:
    """Return seconds of compute from the last speech frame to the endpoint firing.

    Feeds ``speech_frames`` speech frames (priming the detector and marking the
    utterance), starts the clock — this is the moment the user goes quiet — then
    feeds silence frames until the endpoint fires, returning the elapsed compute.
    """
    for _ in range(speech_frames):
        endpointer.feed(speech_frame)
    start = clock()
    for _ in range(max_silence):
        if endpointer.feed(silence_frame):
            return clock() - start
    raise RuntimeError("endpoint did not fire within the silence window")


def run_benchmark(
    runs: int,
    *,
    detect: Detector,
    threshold: float,
    silence_ms: int,
    speech_frame: bytes,
    silence_frame: bytes,
    speech_frames: int = DEFAULT_SPEECH_FRAMES,
    reset: Callable[[], None] = lambda: None,
    clock: Callable[[], float] = time.perf_counter,
) -> LatencyResult:
    """Measure decision latency ``runs`` times and aggregate the samples.

    A fresh :class:`Endpointer` is built per run and ``reset`` is called first so
    a stateful (recurrent) detector starts each utterance clean.
    """
    guard = max_silence_frames(silence_ms)
    samples: list[float] = []
    for _ in range(runs):
        reset()
        endpointer = Endpointer(detect=detect, threshold=threshold, silence_ms=silence_ms)
        samples.append(
            measure_decision_latency(
                endpointer,
                speech_frame,
                silence_frame,
                speech_frames=speech_frames,
                max_silence=guard,
                clock=clock,
            )
        )
    return LatencyResult(runs=runs, samples_s=samples)


def _format_summary(result: LatencyResult, *, silence_ms: int) -> str:
    return (
        f"VAD endpoint decision latency over {result.runs} run(s) "
        f"(vad_silence_ms={silence_ms}, frame={FRAME_MS:.0f} ms):\n"
        f"  p50 {result.median_s * 1000:.1f} ms | "
        f"p95 {result.p95_s * 1000:.1f} ms | "
        f"mean {result.mean_s * 1000:.1f} ms | "
        f"min {result.min_s * 1000:.1f} ms | "
        f"max {result.max_s * 1000:.1f} ms\n"
        f"  (decision compute only — the {silence_ms} ms silence hangover is a "
        f"fixed UX wait, not counted)"
    )


def main(  # pragma: no cover - wires the live Silero detector
    argv: Sequence[str] | None = None,
    *,
    write: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description="Benchmark VAD endpoint decision latency.")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="number of runs")
    parser.add_argument(
        "--speech-frames",
        type=int,
        default=DEFAULT_SPEECH_FRAMES,
        help="speech frames to prime each utterance before the pause",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    from jarvis.vad import SileroDetector

    detector = SileroDetector(settings)
    result = run_benchmark(
        args.runs,
        detect=detector,
        threshold=settings.vad_threshold,
        silence_ms=settings.vad_silence_ms,
        speech_frame=make_speech_frame(),
        silence_frame=make_silence_frame(),
        speech_frames=args.speech_frames,
        reset=detector.reset,
    )
    write(_format_summary(result, silence_ms=settings.vad_silence_ms))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by direct execution
    sys.exit(main())
