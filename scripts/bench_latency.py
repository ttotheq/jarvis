"""Measure Phase 2 latency: VAD endpoint decision (G2.2) and time-to-first-audio (G2.3).

Two modes, both reusing :class:`LatencyResult` and its percentile helpers:

``--mode vad`` (default) — the VAD endpoint *decision* latency (G2.2). ADR-0002
puts a Silero VAD endpointer in the LISTENING state to decide when the user
stopped talking; the metric is responsiveness: once speech ends, how much compute
does the endpointer spend before it fires and hands off to STT? It feeds a
synthetic frame stream (speech frames then trailing silence) through
:class:`jarvis.vad.Endpointer` and times, per run, the wall-clock interval from
the last speech frame to the endpoint firing. That interval is the *decision
compute* the architecture's "VAD endpoint decision: 150-300 ms" budget refers
to — deliberately **not** the real-time ``vad_silence_ms`` hangover (a fixed UX
pause, not a cost) and **not** total turn time.

``--mode ttfa`` — time-to-first-audio (G2.3): end-of-speech to the first TTS
sample. It composes the real cascade behind injected per-stage timers — the
``vad_silence_ms`` hangover, then whisper.cpp STT, then ``claude -p`` first-token,
then Kokoro's first audio chunk — and sums them per run. The cascade up to first
audio is strictly sequential (STT waits on the endpoint, the brain on the
transcript, TTS on the first sentence), so TTFA is the **sum** of the stages, not
their max; the G2.4 streaming overlap shortens *total* turn time, not
time-to-*first*-audio. ``claude`` is spawned per run (matching real per-turn
behaviour, ADR-0003), so the dominant first-token cost is honest, not a warm
reuse. ``--no-hangover`` reframes the metric as endpoint-fire -> first audio.

Every stage is injected (like ``scripts/bench_brain.py``'s subprocess runner):
the real models when run live, fakes in ``tests/test_bench_latency.py`` /
``tests/test_bench_ttfa.py`` so the timing/aggregation never touches torch,
whisper, Kokoro, or the network. Run live (voice extra)::

    uv run python scripts/bench_latency.py --mode vad  --runs 20
    uv run python scripts/bench_latency.py --mode ttfa --runs 20
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


# --- Time-to-first-audio (G2.3) -------------------------------------------

#: One cascade stage: does (or simulates) its work and returns elapsed seconds.
StageTimer = Callable[[], float]


@dataclass(frozen=True)
class TtfaStages:
    """The injectable seams of the time-to-first-audio cascade.

    ``hangover_s`` is the fixed ``vad_silence_ms`` wait between true end-of-speech
    and the endpoint firing; the other three are timers that perform (live) or
    simulate (tests) STT, brain first-token, and TTS-to-first-chunk, each
    returning its own elapsed seconds.
    """

    hangover_s: float
    transcribe: StageTimer
    brain_ttft: StageTimer
    tts_first_chunk: StageTimer


def measure_ttfa(stages: TtfaStages, *, include_hangover: bool = True) -> float:
    """Return seconds from end-of-speech to the first TTS sample for one turn.

    The first-audio cascade is strictly sequential — STT cannot start until the
    endpoint fires, the brain cannot start until the transcript exists, and TTS
    cannot synthesize until the first sentence arrives — so TTFA is the **sum** of
    the stage costs, not their max. (Streaming overlap, G2.4, shortens *total*
    turn time by speaking sentence one while sentence two generates; it does not
    shorten time-to-*first*-audio.)

    With ``include_hangover=False`` the 700 ms ``vad_silence_ms`` wait is dropped,
    measuring endpoint-fire -> first audio — one of the G2.3 renegotiation framings.
    """
    total = stages.hangover_s if include_hangover else 0.0
    total += stages.transcribe()
    total += stages.brain_ttft()
    total += stages.tts_first_chunk()
    return total


def run_ttfa_benchmark(
    runs: int,
    *,
    stages: TtfaStages,
    include_hangover: bool = True,
) -> LatencyResult:
    """Measure time-to-first-audio ``runs`` times and aggregate the samples."""
    samples = [measure_ttfa(stages, include_hangover=include_hangover) for _ in range(runs)]
    return LatencyResult(runs=runs, samples_s=samples)


def _format_ttfa_summary(result: LatencyResult, *, include_hangover: bool, hangover_ms: int) -> str:
    if include_hangover:
        hangover_note = (
            f"end-of-speech -> first audio; {hangover_ms} ms vad_silence_ms hangover included"
        )
    else:
        hangover_note = (
            f"endpoint-fire -> first audio; {hangover_ms} ms vad_silence_ms hangover excluded"
        )
    return (
        f"Time-to-first-audio over {result.runs} run(s) ({hangover_note}):\n"
        f"  p50 {result.median_s:.2f} s | "
        f"p95 {result.p95_s:.2f} s | "
        f"mean {result.mean_s:.2f} s | "
        f"min {result.min_s:.2f} s | "
        f"max {result.max_s:.2f} s\n"
        f"  (sum of hangover + STT + brain first-token + TTS first chunk; "
        f"sequential, so summed not maxed)"
    )


#: A short utterance to drive the live cascade (synthesized for STT, sent to brain).
LIVE_PROMPT = "What time is it?"


def _build_live_stages(  # pragma: no cover - wires whisper, claude, and Kokoro
    settings: object,
) -> TtfaStages:
    """Wire the real cascade: TTS-synthesize a probe clip, then time each stage.

    The probe utterance is synthesized once with Kokoro and transcribed by
    whisper.cpp each run (real STT compute on a real short clip); the brain stage
    spawns ``claude -p`` per run via ``bench_brain.measure_ttft`` so the dominant
    first-token latency reflects real per-turn startup; the TTS stage times Kokoro
    to its first audio chunk.
    """
    import time

    from bench_brain import measure_ttft
    from kokoro import KPipeline

    from jarvis.stt import WhisperCppTranscriber
    from jarvis.tts import KokoroSynthesizer

    synth = KokoroSynthesizer(settings)  # type: ignore[arg-type]
    probe_clip = synth(LIVE_PROMPT)  # one fixed clip for the STT stage
    transcriber = WhisperCppTranscriber(settings)  # type: ignore[arg-type]
    pipeline = KPipeline(lang_code="b")  # 'b' = British English, as in KokoroSynthesizer

    def transcribe() -> float:
        start = time.perf_counter()
        transcriber(probe_clip)
        return time.perf_counter() - start

    def brain_ttft() -> float:
        return measure_ttft(LIVE_PROMPT, binary=settings.claude_binary)  # type: ignore[attr-defined]

    def tts_first_chunk() -> float:
        # Time Kokoro to the *first* yielded chunk — the streaming first-audio cost.
        start = time.perf_counter()
        for _ in pipeline(
            LIVE_PROMPT,
            voice=settings.tts_voice,  # type: ignore[attr-defined]
            speed=settings.tts_speed,  # type: ignore[attr-defined]
        ):
            break
        return time.perf_counter() - start

    return TtfaStages(
        hangover_s=settings.vad_silence_ms / 1000,  # type: ignore[attr-defined]
        transcribe=transcribe,
        brain_ttft=brain_ttft,
        tts_first_chunk=tts_first_chunk,
    )


def main(  # pragma: no cover - wires the live Silero/whisper/claude/Kokoro stack
    argv: Sequence[str] | None = None,
    *,
    write: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Phase 2 latency (VAD / TTFA).")
    parser.add_argument(
        "--mode",
        choices=("vad", "ttfa"),
        default="vad",
        help="vad: endpoint decision latency (G2.2); ttfa: time-to-first-audio (G2.3)",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="number of runs")
    parser.add_argument(
        "--speech-frames",
        type=int,
        default=DEFAULT_SPEECH_FRAMES,
        help="(vad mode) speech frames to prime each utterance before the pause",
    )
    parser.add_argument(
        "--no-hangover",
        action="store_true",
        help="(ttfa mode) measure endpoint-fire -> first audio, excluding the hangover",
    )
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.mode == "ttfa":
        include_hangover = not args.no_hangover
        result = run_ttfa_benchmark(
            args.runs,
            stages=_build_live_stages(settings),
            include_hangover=include_hangover,
        )
        write(
            _format_ttfa_summary(
                result, include_hangover=include_hangover, hangover_ms=settings.vad_silence_ms
            )
        )
        return 0

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
