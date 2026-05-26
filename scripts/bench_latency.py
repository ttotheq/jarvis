"""Measure latency: VAD endpoint decision (G2.2), time-to-first-audio (G2.3), cold start (G4.2).

Three modes, all reusing :class:`LatencyResult` and its percentile helpers:

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

``--mode cold_start`` — boot to ready-for-wake-word (G4.2): how long after launch
before the always-on loop can block in ``wait_for_wake`` and hear "hey jarvis".
In the ``wake_word`` runtime only the persistent mic and the openWakeWord listener
gate readiness; the Silero VAD endpointer, Kokoro synthesizer, and the barge-in
watcher's second openWakeWord model are needed only *after* a wake, so they are
warmed in the background and excluded from ``ready_s``. The mode times each
component's real construction, reports ``ready_s`` against the 10 s target, and
breaks down what was deferred. It is a first-process point measurement (the
one-time ``import torch`` and the OS file cache warm later builds), so it defaults
to a single run.

Every stage is injected (like ``scripts/bench_brain.py``'s subprocess runner):
the real models when run live, fakes in ``tests/test_bench_latency.py`` /
``tests/test_bench_ttfa.py`` / ``tests/test_bench_cold_start.py`` so the
timing/aggregation never touches torch, whisper, Kokoro, or the network. Run live
(voice extra)::

    uv run python scripts/bench_latency.py --mode vad  --runs 20
    uv run python scripts/bench_latency.py --mode ttfa --runs 20
    uv run python scripts/bench_latency.py --mode cold_start
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


# --- Cold start (G4.2) -----------------------------------------------------


@dataclass(frozen=True)
class ColdStartStages:
    """The injectable builder-timers of the always-on cold start (G4.2).

    Each timer builds (or, in tests, simulates building) one runtime component
    and returns its own elapsed seconds. The metric is boot -> ready-for-wake-
    word: in the ``wake_word`` runtime only the persistent mic and the
    openWakeWord listener must exist before the loop can block in
    ``wait_for_wake`` and hear "hey jarvis". The Silero VAD endpointer, the
    Kokoro synthesizer, and the barge-in watcher are needed only *after* a wake
    (capture, reply, interrupt), so they are warmed in the background once the
    wake gate is up and their cost is reported as a breakdown, not counted toward
    readiness.
    """

    build_mic: StageTimer
    build_wake: StageTimer
    build_vad: StageTimer
    build_tts: StageTimer
    build_barge_in: StageTimer


@dataclass(frozen=True)
class ColdStartResult:
    """One cold-start measurement: time-to-ready plus the deferred breakdown."""

    ready_s: float  # boot -> ready-for-wake-word (mic open + wake listener loaded)
    deferred_s: dict[str, float]  # warmed after readiness; not on the wake path

    @property
    def warm_total_s(self) -> float:
        """Full warm cost (readiness + every deferred component), to show savings."""
        return self.ready_s + sum(self.deferred_s.values())


def measure_cold_start(stages: ColdStartStages) -> ColdStartResult:
    """Build the readiness components, then time the deferred ones, once.

    ``ready_s`` is the wake path alone (mic + openWakeWord listener) — the G4.2
    figure. The deferred components are still built (and timed) so the benchmark
    can report what was moved off the critical path, but their cost never enters
    ``ready_s``.
    """
    ready_s = stages.build_mic() + stages.build_wake()
    deferred_s = {
        "vad": stages.build_vad(),
        "tts": stages.build_tts(),
        "barge_in": stages.build_barge_in(),
    }
    return ColdStartResult(ready_s=ready_s, deferred_s=deferred_s)


def run_cold_start_benchmark(
    runs: int, *, stages: ColdStartStages
) -> tuple[LatencyResult, ColdStartResult]:
    """Measure cold start ``runs`` times; aggregate readiness, keep run 1's breakdown.

    Cold start is fundamentally a first-process point measurement (the one-time
    ``import torch`` and the OS file cache warm every later build), so the
    canonical figure is the first run; ``--runs`` > 1 measures warm rebuilds and
    is offered only so repeated samples reuse :class:`LatencyResult`'s percentile
    helpers like the other modes. The returned breakdown is from the first run.
    """
    first: ColdStartResult | None = None
    ready_samples: list[float] = []
    for _ in range(runs):
        result = measure_cold_start(stages)
        if first is None:
            first = result
        ready_samples.append(result.ready_s)
    assert first is not None  # runs >= 1
    return LatencyResult(runs=runs, samples_s=ready_samples), first


def _format_cold_start_summary(
    ready: LatencyResult, breakdown: ColdStartResult, *, target_s: float
) -> str:
    verdict = "PASS" if ready.median_s <= target_s else "FAIL"
    deferred = ", ".join(f"{name} {sec:.2f} s" for name, sec in breakdown.deferred_s.items())
    deferred_line = deferred if deferred else "(none)"
    return (
        f"Cold start (boot -> ready-for-wake-word) over {ready.runs} run(s):\n"
        f"  ready p50 {ready.median_s:.2f} s | p95 {ready.p95_s:.2f} s | "
        f"min {ready.min_s:.2f} s | max {ready.max_s:.2f} s\n"
        f"  target <= {target_s:.0f} s -> {verdict}\n"
        f"  ready = persistent mic + openWakeWord listener only (the wake path)\n"
        f"  deferred (warmed in the background after readiness): {deferred_line}\n"
        f"  full warm cost if loaded eagerly: {breakdown.warm_total_s:.2f} s"
    )


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


#: G4.2 target: boot -> ready-for-wake-word.
COLD_START_TARGET_S = 10.0


def _build_live_cold_start_stages(  # pragma: no cover - wires the live native stack
    settings: object,
) -> ColdStartStages:
    """Time each runtime component's real construction (G4.2 cold start).

    Mirrors what ``jarvis.cli.run`` builds: the persistent mic and openWakeWord
    listener gate readiness; the Silero endpointer, Kokoro synthesizer, and the
    barge-in watcher's second openWakeWord model are the deferred (background-
    warmed) components. The mic stream is closed after timing so repeated runs do
    not leak PortAudio streams.
    """
    import time

    from jarvis.audio import SoundDeviceMicrophone
    from jarvis.tts import build_default_synthesizer
    from jarvis.vad import build_default_endpointer
    from jarvis.wakeword import FRAME_SAMPLES, build_default_detector, build_default_listener
    from jarvis.wakeword import SAMPLE_RATE as WAKEWORD_SAMPLE_RATE

    def build_mic() -> float:
        block_frames = max(
            1,
            round(settings.sample_rate * FRAME_SAMPLES / WAKEWORD_SAMPLE_RATE),  # type: ignore[attr-defined]
        )
        start = time.perf_counter()
        mic = SoundDeviceMicrophone(
            settings.sample_rate,  # type: ignore[attr-defined]
            block_frames=block_frames,
            device=settings.input_device,  # type: ignore[attr-defined]
        )
        elapsed = time.perf_counter() - start
        mic.close()
        return elapsed

    def _timed(build: Callable[[], object]) -> float:
        start = time.perf_counter()
        build()
        return time.perf_counter() - start

    return ColdStartStages(
        build_mic=build_mic,
        build_wake=lambda: _timed(lambda: build_default_listener(settings)),  # type: ignore[arg-type]
        build_vad=lambda: _timed(lambda: build_default_endpointer(settings)),  # type: ignore[arg-type]
        build_tts=lambda: _timed(build_default_synthesizer),
        build_barge_in=lambda: _timed(build_default_detector),
    )


def main(  # pragma: no cover - wires the live Silero/whisper/claude/Kokoro stack
    argv: Sequence[str] | None = None,
    *,
    write: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description="Benchmark latency (VAD / TTFA / cold start).")
    parser.add_argument(
        "--mode",
        choices=("vad", "ttfa", "cold_start"),
        default="vad",
        help=(
            "vad: endpoint decision latency (G2.2); ttfa: time-to-first-audio (G2.3); "
            "cold_start: boot -> ready-for-wake-word (G4.2)"
        ),
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="number of runs (default 20; cold_start defaults to 1 — the true first boot)",
    )
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

    if args.mode == "cold_start":
        runs = 1 if args.runs is None else args.runs
        ready, breakdown = run_cold_start_benchmark(
            runs, stages=_build_live_cold_start_stages(settings)
        )
        write(_format_cold_start_summary(ready, breakdown, target_s=COLD_START_TARGET_S))
        return 0

    runs = DEFAULT_RUNS if args.runs is None else args.runs

    if args.mode == "ttfa":
        include_hangover = not args.no_hangover
        result = run_ttfa_benchmark(
            runs,
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
        runs,
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
