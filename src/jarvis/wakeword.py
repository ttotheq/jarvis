"""Wake-word detection: the IDLE-state "Hey Jarvis" trigger (Phase 2 goal G2.1).

In the IDLE state the orchestrator (docs/architecture.md) watches the microphone
for the phrase "hey jarvis" and only then wakes into LISTENING. Detection is
openWakeWord's pretrained ``hey_jarvis`` model run on a rolling buffer of 80 ms
audio frames; each frame yields a score in [0, 1] and a wake fires once the score
crosses ``wake_threshold`` (``jarvis.config``).

The native model is wrapped as a :data:`Detector` callable — one PCM16 frame in,
one score out — so the listening logic depends on the interface, not openWakeWord.
That keeps the :class:`WakeWordListener` frame loop and the true-accept /
false-accept :class:`Accuracy` metric pure and fully unit-testable with a fake
detector (no microphone, no model download in CI). The openWakeWord backend is a
native shim excluded from coverage (ADR-0005). True-accept is verified over
labeled recordings; the 30-min ambient false-accept soak runs live
(``scripts/soak_wakeword.py``) and its count is recorded in the phase doc Outcomes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass

from jarvis.audio import Clip
from jarvis.config import Settings, get_settings

#: openWakeWord consumes 80 ms frames: 1280 samples * 2 bytes (PCM16) at 16 kHz.
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * 2

#: A detector: scores one PCM16 frame for the wake word, returning a value in [0, 1].
Detector = Callable[[bytes], float]


def iter_frames(clip: Clip, frame_bytes: int = FRAME_BYTES) -> Iterator[bytes]:
    """Split a clip into fixed-size frames; any trailing short frame is dropped.

    openWakeWord expects whole frames, so a partial tail (fewer than
    ``frame_bytes``) is not yielded rather than padded with silence.
    """
    data = clip.samples
    for start in range(0, len(data) - frame_bytes + 1, frame_bytes):
        yield data[start : start + frame_bytes]


@dataclass
class WakeWordListener:
    """Fires when the wake score crosses ``threshold`` across a stream of frames.

    ``detect`` is the (stateful) per-frame scorer; the listener owns only the
    threshold comparison and the frame loop, so it is tested with a fake scorer.
    """

    detect: Detector
    threshold: float

    def scored(self, frame: bytes) -> bool:
        """Score one frame; True if it crosses (>=) the threshold."""
        return self.detect(frame) >= self.threshold

    def wait_for_wake(self, frames: Iterable[bytes]) -> bool:
        """Consume frames until one crosses the threshold.

        Returns True on the first crossing — it short-circuits, so an unbounded
        live-mic stream is fine — or False if ``frames`` is exhausted first.
        """
        return any(self.scored(frame) for frame in frames)

    def fires_on(self, clip: Clip) -> bool:
        """Whether the wake word is detected anywhere in ``clip`` (offline use)."""
        return self.wait_for_wake(iter_frames(clip))


@dataclass(frozen=True)
class Accuracy:
    """The G2.1 metric: wake-word true-accept and false-accept rates.

    ``positives`` labeled "hey jarvis" utterances are presented and
    ``true_accepts`` of them fire; ``false_accepts`` spurious wakes occur over
    ``ambient_seconds`` of negative material. The false-accept rate is projected
    to a 30-minute window to match the target (<= 1 per 30 min).
    """

    positives: int
    true_accepts: int
    false_accepts: int
    ambient_seconds: float

    @property
    def true_accept_rate(self) -> float:
        """Fraction of positive utterances that woke Jarvis (target >= 0.95)."""
        if self.positives == 0:
            return 1.0  # vacuously perfect; never divide by zero
        return self.true_accepts / self.positives

    @property
    def false_accepts_per_30min(self) -> float:
        """Spurious wakes projected to a 30-minute window (target <= 1)."""
        if self.ambient_seconds <= 0:
            return 0.0
        return self.false_accepts * 1800.0 / self.ambient_seconds


def evaluate(
    fires_on: Callable[[Clip], bool],
    positives: Sequence[Clip],
    ambient: Sequence[Clip],
) -> Accuracy:
    """Run ``fires_on`` over labeled clips and tally the G2.1 metrics.

    ``positives`` are "hey jarvis" utterances (each should fire); ``ambient`` are
    negative clips where any fire is a false accept. ``fires_on`` is a per-clip
    detector — a fresh-state :meth:`WakeWordListener.fires_on` — injected so the
    aggregation is testable without the real model.
    """
    true_accepts = sum(1 for clip in positives if fires_on(clip))
    false_accepts = sum(1 for clip in ambient if fires_on(clip))
    ambient_seconds = sum(clip.duration_s for clip in ambient)
    return Accuracy(
        positives=len(positives),
        true_accepts=true_accepts,
        false_accepts=false_accepts,
        ambient_seconds=ambient_seconds,
    )


class OpenWakeWordDetector:  # pragma: no cover - requires the openWakeWord model
    """Score frames with openWakeWord's pretrained model (default ``hey_jarvis``)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else get_settings()
        import openwakeword
        from openwakeword.model import Model

        # The pretrained wake-word + feature ONNX models ship bundled with the
        # package; resolve the configured wake word's path and load just it.
        model_path = openwakeword.models[self._settings.wake_word]["model_path"]
        self._model = Model(wakeword_model_paths=[model_path])
        self._wake_word = self._settings.wake_word

    def __call__(self, frame: bytes) -> float:
        import numpy as np

        samples = np.frombuffer(frame, dtype=np.int16)
        scores = self._model.predict(samples)
        # Pretrained keys may be versioned (e.g. "hey_jarvis_v0.1"); match by name.
        for name, score in scores.items():
            if self._wake_word in name:
                return float(score)
        return 0.0

    def reset(self) -> None:
        """Clear the rolling feature buffer (use between independent clips)."""
        self._model.reset()


def build_default_detector() -> Detector:  # pragma: no cover - native
    return OpenWakeWordDetector()


def build_default_listener(  # pragma: no cover - native
    settings: Settings | None = None,
) -> WakeWordListener:
    settings = settings if settings is not None else get_settings()
    return WakeWordListener(detect=build_default_detector(), threshold=settings.wake_threshold)
