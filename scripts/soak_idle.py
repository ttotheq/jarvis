"""Idle stability soak for the always-on loop (Phase 4 goal G4.3).

Runs the daemon's idle activity — the ``wait_for_wake_phrase`` hot path, scoring
every mic frame through openWakeWord — for a fixed duration and checks two
things: that it never crashes and that resident memory growth stays within
budget (target: 0 crashes, growth <= 50 MB over 1 hour).

The testable core is :func:`soak`, which drives an injected frame source +
per-frame score function + RSS sampler + clock. It samples RSS at intervals and
reports growth from a **post-settle steady-state baseline**, so G4.2's
background warm-up (torch + Kokoro loading in the first seconds, hundreds of MB)
is excluded and only true steady-state drift counts as a leak. A scoring
exception is tallied as a crash rather than propagated, so a transient failure
is recorded instead of ending the run blind. The mic, the openWakeWord detector,
and the ``ps``-based RSS sampler are hardware/OS shims (``# pragma: no cover``);
the result over a 1-hour run is recorded in ``docs/phases/phase-4-daemon.md``.

The live source paces frames at the real mic cadence (one ~80 ms frame at a
time), so a wall-clock hour of soaking represents a real hour of idle listening.
Run live (requires the voice extra)::

    python scripts/soak_idle.py --minutes 60
    python scripts/soak_idle.py --minutes 60 --source mic   # real ambient instead
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

#: G4.3 target: idle resident-memory growth over the soak.
GROWTH_BUDGET_MB = 50.0
#: Default RSS sample cadence — fine enough to see drift, cheap enough to ignore.
DEFAULT_SAMPLE_INTERVAL_S = 10.0
#: Default warm-up window excluded from the growth baseline (covers G4.2's
#: background torch + Kokoro load settling into steady state).
DEFAULT_SETTLE_S = 30.0


@dataclass(frozen=True)
class SoakResult:
    """Outcome of one idle soak run."""

    elapsed_s: float
    frames_scanned: int
    rss_start_mb: float  # steady-state baseline (first sample at/after settle_s)
    rss_peak_mb: float  # peak over the post-settle window
    rss_end_mb: float  # last sample
    crashes: int

    @property
    def rss_growth_mb(self) -> float:
        """Resident growth from the steady-state baseline to the end of the run."""
        return self.rss_end_mb - self.rss_start_mb

    def passed(self, *, growth_budget_mb: float = GROWTH_BUDGET_MB) -> bool:
        """Whether the run met G4.3: no crashes and growth within budget."""
        return self.crashes == 0 and self.rss_growth_mb <= growth_budget_mb


def soak(
    *,
    score_frame: Callable[[bytes], float],
    next_frame: Callable[[], bytes],
    sample_rss_mb: Callable[[], float],
    duration_s: float,
    sample_interval_s: float,
    settle_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> SoakResult:
    """Score frames for ``duration_s`` seconds, sampling RSS and tallying crashes.

    Each iteration scores one frame (a scoring exception is counted as a crash
    and the loop continues) and, when due, samples RSS. Growth is computed across
    the samples taken at or after ``settle_s`` — the steady-state window — so the
    warm-up ramp does not masquerade as a leak. The clock and sampler are injected
    so the accounting is deterministic in tests.
    """
    start: float | None = None
    samples: list[tuple[float, float]] = []  # (elapsed_s, rss_mb)
    frames_scanned = 0
    crashes = 0
    next_sample_at = 0.0
    elapsed = 0.0
    while True:
        now = clock()
        if start is None:
            start = now  # anchor on the first loop read so sample #1 is at elapsed 0
        elapsed = now - start
        if elapsed >= next_sample_at:
            samples.append((elapsed, sample_rss_mb()))
            next_sample_at += sample_interval_s
        if elapsed >= duration_s:
            break
        frame = next_frame()
        try:
            score_frame(frame)
        except Exception:  # a transient scoring failure is recorded, not fatal
            crashes += 1
        frames_scanned += 1

    measured = [rss for (e, rss) in samples if e >= settle_s] or [rss for (_, rss) in samples]
    return SoakResult(
        elapsed_s=elapsed,
        frames_scanned=frames_scanned,
        rss_start_mb=measured[0],
        rss_peak_mb=max(measured),
        rss_end_mb=measured[-1],
        crashes=crashes,
    )


def _ps_rss_mb(pid: int) -> float:  # pragma: no cover - shells out to ps
    """Current resident set size of ``pid`` in MB, via ``ps`` (macOS reports KB)."""
    import subprocess

    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=True
    )
    return int(out.stdout.strip()) / 1024.0


def main() -> int:  # pragma: no cover - live microphone/model + real RSS + real time
    import argparse
    import os

    from jarvis.config import get_settings
    from jarvis.loop import _to_wakeword_frame
    from jarvis.wakeword import FRAME_SAMPLES, SAMPLE_RATE, build_default_listener

    parser = argparse.ArgumentParser(description="Idle stability soak (G4.3).")
    parser.add_argument("--minutes", type=float, default=60.0, help="soak duration in minutes")
    parser.add_argument(
        "--source",
        choices=("silence", "mic"),
        default="silence",
        help="silence: synthetic silent frames (deterministic); mic: real ambient",
    )
    parser.add_argument(
        "--sample-interval", type=float, default=DEFAULT_SAMPLE_INTERVAL_S, help="RSS sample (s)"
    )
    parser.add_argument(
        "--settle", type=float, default=DEFAULT_SETTLE_S, help="warm-up window excluded (s)"
    )
    args = parser.parse_args()

    settings = get_settings()
    listener = build_default_listener(settings)
    rate = settings.sample_rate
    block_frames = max(1, round(rate * FRAME_SAMPLES / SAMPLE_RATE))
    frame_duration_s = block_frames / rate

    if args.source == "mic":
        from jarvis.audio import make_sounddevice_source

        mic = make_sounddevice_source(rate, block_frames=block_frames, device=settings.input_device)

        def next_frame() -> bytes:
            return mic()  # the blocking mic read paces itself at the frame cadence
    else:
        silent = b"\x00" * (block_frames * 2)

        def next_frame() -> bytes:
            time.sleep(frame_duration_s)  # pace at the real mic cadence
            return silent

    def score_frame(raw: bytes) -> float:
        return listener.score(_to_wakeword_frame(raw, rate))

    pid = os.getpid()
    print(
        f"Idle soak: {args.minutes:g} min, source={args.source}, "
        f"sampling RSS every {args.sample_interval:g}s (excluding first {args.settle:g}s)…"
    )
    result = soak(
        score_frame=score_frame,
        next_frame=next_frame,
        sample_rss_mb=lambda: _ps_rss_mb(pid),
        duration_s=args.minutes * 60.0,
        sample_interval_s=args.sample_interval,
        settle_s=args.settle,
        clock=time.monotonic,
    )
    verdict = "PASS" if result.passed() else "FAIL"
    print(
        f"\nIdle soak over {result.elapsed_s / 60:.1f} min "
        f"({result.frames_scanned} frames scored):\n"
        f"  crashes = {result.crashes}\n"
        f"  RSS baseline {result.rss_start_mb:.1f} MB | peak {result.rss_peak_mb:.1f} MB | "
        f"end {result.rss_end_mb:.1f} MB\n"
        f"  growth {result.rss_growth_mb:+.1f} MB (budget <= {GROWTH_BUDGET_MB:.0f} MB)\n"
        f"  -> {verdict}"
    )
    return 0 if result.passed() else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
