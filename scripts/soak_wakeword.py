"""Live ambient false-accept soak for the wake word (Phase 2 goal G2.1).

Runs the openWakeWord ``hey_jarvis`` detector against live microphone ambient
audio for a fixed duration and counts *spurious* wakes (target: <= 1 per 30 min).
The mic and detector are hardware shims (built behind ``jarvis.wakeword`` and
``jarvis.audio``); the testable core is :func:`soak`, which drives an injected
frame source + listener + clock and tallies distinct false-wake events — a single
wake that spans several consecutive frames counts once (rising-edge debounce). The
resulting count over a 30-minute run is recorded in
``docs/phases/phase-2-wakeword-streaming.md`` Outcomes.

Run live (requires the voice extra and a microphone)::

    python scripts/soak_wakeword.py --minutes 30
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from jarvis.wakeword import WakeWordListener


@dataclass(frozen=True)
class SoakResult:
    """Outcome of one ambient soak run."""

    false_accepts: int
    elapsed_s: float
    frames_scanned: int

    @property
    def false_accepts_per_30min(self) -> float:
        """False accepts projected to a 30-minute window (target <= 1)."""
        if self.elapsed_s <= 0:
            return 0.0
        return self.false_accepts * 1800.0 / self.elapsed_s


def soak(
    listener: WakeWordListener,
    frames: Iterable[bytes],
    *,
    duration_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> SoakResult:
    """Scan ``frames`` for up to ``duration_s`` seconds, counting false wakes.

    Every fire here is spurious (ambient audio contains no real wake word).
    Consecutive firing frames are one event: a new false accept is counted only on
    a rising edge (score crosses up after dropping back below the threshold). The
    clock is injected so the deadline is deterministic in tests.
    """
    start = clock()
    false_accepts = 0
    frames_scanned = 0
    armed = True  # rising-edge debounce: only count a fire while armed
    now = start
    for frame in frames:
        now = clock()
        if now - start >= duration_s:
            break
        fired = listener.scored(frame)
        frames_scanned += 1
        if fired and armed:
            false_accepts += 1
            armed = False
        elif not fired:
            armed = True
    return SoakResult(
        false_accepts=false_accepts,
        elapsed_s=now - start,
        frames_scanned=frames_scanned,
    )


def main() -> None:  # pragma: no cover - live microphone + model path
    import argparse

    from jarvis.audio import make_sounddevice_source
    from jarvis.config import get_settings
    from jarvis.wakeword import FRAME_SAMPLES, build_default_listener

    parser = argparse.ArgumentParser(description="Ambient wake-word false-accept soak.")
    parser.add_argument("--minutes", type=float, default=30.0, help="soak duration in minutes")
    args = parser.parse_args()

    settings = get_settings()
    listener = build_default_listener(settings)
    source = make_sounddevice_source(settings.sample_rate, block_frames=FRAME_SAMPLES)

    def frame_stream() -> Iterable[bytes]:
        while True:
            yield source()

    duration_s = args.minutes * 60.0
    print(f"Soaking for {args.minutes:g} min — stay quiet (no 'hey jarvis')…")
    result = soak(listener, frame_stream(), duration_s=duration_s)
    print(
        f"false_accepts={result.false_accepts} over {result.elapsed_s / 60:.1f} min "
        f"({result.false_accepts_per_30min:.2f} per 30 min)"
    )


if __name__ == "__main__":  # pragma: no cover
    main()
