"""Voice-activity endpointing: deciding when the user stopped talking (G2.2).

In the LISTENING state the orchestrator (docs/architecture.md) captures the
utterance and watches for end-of-speech: a trailing pause long enough to mean
"your turn is over". Detection is Silero VAD run per frame — each 32 ms frame
yields a speech probability in [0, 1] — and the endpoint fires once silence has
accumulated for ``vad_silence_ms`` after speech was heard.

The native model is wrapped as a :data:`Detector` callable — one PCM16 frame in,
one probability out — so the endpointing logic depends on the interface, not
Silero. That keeps the :class:`Endpointer` (a rolling trailing-silence
accumulator) pure and fully unit-testable with a fake detector (no microphone, no
model in CI). The Silero backend (:class:`SileroDetector`) is a native shim
excluded from coverage (ADR-0005); its decision latency is measured live by
``scripts/bench_latency.py`` and recorded in the phase doc Outcomes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from jarvis.audio import Clip
from jarvis.config import Settings, get_settings

#: Silero VAD at 16 kHz consumes exactly 512-sample frames: 1024 bytes (PCM16),
#: 32 ms. Unlike openWakeWord's 80 ms frame, this size is a hard model
#: requirement — other lengths are rejected by the TorchScript graph.
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2
SAMPLE_RATE = 16_000
#: One frame's duration in milliseconds (512 / 16000 s = 32 ms).
FRAME_MS = FRAME_SAMPLES * 1000 / SAMPLE_RATE

#: A detector: scores one PCM16 frame's speech probability, a value in [0, 1].
Detector = Callable[[bytes], float]


def iter_frames(clip: Clip, frame_bytes: int = FRAME_BYTES) -> Iterator[bytes]:
    """Split a clip into fixed-size frames; any trailing short frame is dropped.

    Silero expects whole 512-sample frames, so a partial tail (fewer than
    ``frame_bytes``) is not yielded rather than padded with silence.
    """
    data = clip.samples
    for start in range(0, len(data) - frame_bytes + 1, frame_bytes):
        yield data[start : start + frame_bytes]


@dataclass
class Endpointer:
    """Fires end-of-speech once trailing silence reaches ``silence_ms``.

    ``detect`` is the (stateful) per-frame speech scorer; the endpointer owns the
    threshold comparison and a rolling silence accumulator, so it is tested with a
    fake scorer. A frame scoring ``>= threshold`` is speech: it marks that an
    utterance is under way and resets the accumulator. A sub-threshold frame adds
    one frame's duration to the accumulator, but *only after speech has been
    heard* — leading silence never endpoints. The endpoint fires on the single
    frame that carries the accumulator to ``silence_ms``; it then latches, so a
    long pause yields one endpoint, not one per silent frame.
    """

    detect: Detector
    threshold: float
    silence_ms: int
    frame_ms: float = FRAME_MS

    _silence_ms: float = field(default=0.0, init=False, repr=False)
    _speech_seen: bool = field(default=False, init=False, repr=False)
    _fired: bool = field(default=False, init=False, repr=False)

    def feed(self, frame: bytes) -> bool:
        """Process one frame; return True only on the frame that fires the endpoint.

        Returns True exactly once (the rising edge of "enough trailing silence")
        and False on every frame before and after, so an endpoint is reported once.
        """
        if self._fired:
            return False
        if self.detect(frame) >= self.threshold:  # speech (threshold inclusive)
            self._speech_seen = True
            self._silence_ms = 0.0
            return False
        if not self._speech_seen:  # leading silence: nothing to end yet
            return False
        self._silence_ms += self.frame_ms
        if self._silence_ms >= self.silence_ms:
            self._fired = True
            return True
        return False

    def endpoint(self, frames: Iterable[bytes]) -> int | None:
        """Consume frames until the endpoint fires; return the firing frame index.

        Returns the 0-based index of the frame that ended speech, or None if
        ``frames`` is exhausted first. Short-circuits, so a live mic stream is fine.
        """
        for i, frame in enumerate(frames):
            if self.feed(frame):
                return i
        return None

    def reset(self) -> None:
        """Clear the accumulator and latch (use between independent utterances)."""
        self._silence_ms = 0.0
        self._speech_seen = False
        self._fired = False


class SileroDetector:  # pragma: no cover - requires the silero-vad model + torch
    """Score frame speech probability with Silero VAD's bundled pretrained model."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else get_settings()
        from silero_vad import load_silero_vad

        # The pretrained model ships bundled with the silero-vad wheel; load it
        # directly (no torch.hub download, no network).
        self._model = load_silero_vad()

    def __call__(self, frame: bytes) -> float:
        import numpy as np
        import torch

        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        tensor = torch.from_numpy(samples)
        return float(self._model(tensor, SAMPLE_RATE).item())

    def reset(self) -> None:
        """Clear Silero's recurrent state (use between independent utterances)."""
        self._model.reset_states()


def build_default_detector() -> Detector:  # pragma: no cover - native
    return SileroDetector()


def build_default_endpointer(  # pragma: no cover - native
    settings: Settings | None = None,
) -> Endpointer:
    settings = settings if settings is not None else get_settings()
    return Endpointer(
        detect=build_default_detector(),
        threshold=settings.vad_threshold,
        silence_ms=settings.vad_silence_ms,
    )
