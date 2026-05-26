"""Tests for the always-on entry point primitives (Phase 4 always-on runtime).

These are the two pure pieces that turn the developer harness into the
always-on cascade: ``wait_for_wake_phrase`` (the IDLE primitive — block until
"hey jarvis") and ``capture_until_endpoint`` (the LISTENING primitive — capture
one utterance, ending on Silero VAD trailing silence). Both take injected frame
sources / detectors, so the wake-gated, VAD-endpointed turn is exercised with no
microphone and no native models (ADR-0005). The live sounddevice + openWakeWord
+ Silero builders are coverage-excluded shims verified manually.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest

from jarvis.loop import capture_until_endpoint, wait_for_wake_phrase
from jarvis.vad import FRAME_BYTES as VAD_FRAME_BYTES
from jarvis.vad import Endpointer
from jarvis.wakeword import FRAME_BYTES as WAKEWORD_FRAME_BYTES
from jarvis.wakeword import WakeWordListener


class ResettableDetector:
    """A detector that returns scripted scores and records reset() calls."""

    def __init__(self, scores: list[float]) -> None:
        self._it = iter(scores)
        self.resets = 0
        self.seen: list[int] = []

    def __call__(self, frame: bytes) -> float:
        self.seen.append(len(frame))
        return next(self._it)

    def reset(self) -> None:
        self.resets += 1


def _source(frames: list[bytes]) -> Callable[[], bytes]:
    """A FrameSource that yields each frame once, then raises (over-read = bug)."""
    it = iter(frames)

    def _read() -> bytes:
        return next(it)

    return _read


def _repeating_source(frame: bytes) -> Callable[[], bytes]:
    """A FrameSource that yields the same frame forever (for cap/loop tests)."""

    def _read() -> bytes:
        return frame

    return _read


def _scripted_detector(scores: list[float]) -> tuple[Callable[[bytes], float], list[int]]:
    """A detector returning scripted scores; records each fed frame's byte length."""
    seen: list[int] = []
    it = iter(scores)

    def _detect(frame: bytes) -> float:
        seen.append(len(frame))
        return next(it)

    return _detect, seen


# --- wait_for_wake_phrase (IDLE) -------------------------------------------


def test_wait_for_wake_returns_on_phrase() -> None:
    detect, seen = _scripted_detector([0.0, 0.1, 0.95, 0.99])
    listener = WakeWordListener(detect=detect, threshold=0.9)
    # More frames are available than needed: it must short-circuit on the 3rd.
    source = _source([b"\x01\x02" * 100 for _ in range(5)])

    wait_for_wake_phrase(source, listener=listener, source_sample_rate=16_000)

    assert len(seen) == 3  # stopped on the first frame to cross threshold


def test_wait_for_wake_ignores_below_threshold_frames() -> None:
    detect, seen = _scripted_detector([0.5, 0.89, 0.9])
    listener = WakeWordListener(detect=detect, threshold=0.9)
    source = _source([b"\x00\x00" * 100 for _ in range(3)])

    wait_for_wake_phrase(source, listener=listener, source_sample_rate=16_000)

    assert len(seen) == 3  # 0.5 and 0.89 did not fire; 0.9 did


def test_wait_for_wake_coerces_frames_to_wakeword_geometry() -> None:
    detect, seen = _scripted_detector([0.95])
    listener = WakeWordListener(detect=detect, threshold=0.9)
    # A 48 kHz source frame is resampled to 16 kHz and coerced to openWakeWord's
    # exact frame size before scoring.
    source = _source([b"\x01\x02" * 1536])

    wait_for_wake_phrase(source, listener=listener, source_sample_rate=48_000)

    assert seen == [WAKEWORD_FRAME_BYTES]


def test_wait_for_wake_resets_source_and_detector() -> None:
    """Each wake watch starts from a clean mic buffer + detector state."""
    detector = ResettableDetector([0.95])
    listener = WakeWordListener(detect=detector, threshold=0.9)
    flushes = 0

    def reset_source() -> None:
        nonlocal flushes
        flushes += 1

    wait_for_wake_phrase(
        _source([b"\x01\x02" * 100]),
        listener=listener,
        source_sample_rate=16_000,
        reset_source=reset_source,
    )

    assert flushes == 1
    assert detector.resets == 1


def test_wait_for_wake_logs_score_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    detect, _ = _scripted_detector([0.95])
    listener = WakeWordListener(detect=detect, threshold=0.9)
    with caplog.at_level(logging.DEBUG, logger="jarvis.loop"):
        wait_for_wake_phrase(
            _source([b"\x04\x05" * 100]), listener=listener, source_sample_rate=16_000
        )
    assert any("idle wake watch" in r.message for r in caplog.records)


# --- capture_until_endpoint (LISTENING) ------------------------------------


def _endpointer(scores: list[float], *, silence_ms: int) -> tuple[Endpointer, list[int]]:
    detect, seen = _scripted_detector(scores)
    # frame_ms left at the real 32 ms default (512 samples @ 16 kHz).
    return Endpointer(detect=detect, threshold=0.5, silence_ms=silence_ms), seen


def test_capture_stops_on_endpoint() -> None:
    # speech, speech, silence(+32), silence(+32 => 64 >= 64 fires on the 4th frame)
    endpointer, _ = _endpointer([0.9, 0.9, 0.0, 0.0, 0.0, 0.0], silence_ms=64)
    # 512-sample (1024-byte) reads: one VAD frame per read.
    frames = [b"\x07\x07" * 512 for _ in range(6)]
    source = _source(frames)

    clip = capture_until_endpoint(
        source, endpointer=endpointer, sample_rate=16_000, max_seconds=10.0
    )

    # Captured exactly the 4 reads through the endpoint, no more.
    assert clip.num_samples == 4 * 512
    assert clip.sample_rate == 16_000


def test_capture_rechunks_mic_blocks_to_512_sample_vad_frames() -> None:
    # Mic blocks are 1280 samples (openWakeWord geometry); Silero needs 512.
    # 1280 = 2*512 + 256, so the remainder must carry across reads or frames drop.
    endpointer, seen = _endpointer([0.9] * 8, silence_ms=100_000)  # never endpoints
    source = _repeating_source(b"\x09\x09" * 1280)

    # Cap after exactly two 1280-sample reads (2560 samples / 16 kHz = 0.16 s).
    capture_until_endpoint(source, endpointer=endpointer, sample_rate=16_000, max_seconds=0.16)

    # Correct carry yields 5 frames (2560 / 512); naive per-read chunking drops
    # the remainder and yields only 4.
    assert seen == [VAD_FRAME_BYTES] * 5


def test_capture_respects_max_seconds_cap() -> None:
    # Detector always silent => endpointer never fires; the cap must terminate.
    endpointer, _ = _endpointer([0.0] * 10_000, silence_ms=64)
    source = _repeating_source(b"\x00\x00" * 512)  # 512 samples per read

    clip = capture_until_endpoint(
        source, endpointer=endpointer, sample_rate=16_000, max_seconds=0.064
    )

    # 0.064 s @ 16 kHz = 1024 samples = two 512-sample reads, then stop.
    assert clip.num_samples == 2 * 512


def test_capture_resamples_when_rate_differs() -> None:
    endpointer, seen = _endpointer([0.9, 0.0, 0.0, 0.0], silence_ms=64)
    # 48 kHz reads of 1536 samples resample to 512 samples (1024 bytes) at 16 kHz.
    source = _source([b"\x05\x05" * 1536 for _ in range(4)])

    clip = capture_until_endpoint(
        source, endpointer=endpointer, sample_rate=48_000, max_seconds=10.0
    )

    assert seen == [VAD_FRAME_BYTES] * 3  # endpoint fires on the 3rd VAD frame
    assert clip.sample_rate == 48_000  # raw audio retained at capture rate


def test_capture_resets_source_and_detector_between_turns() -> None:
    """Each capture flushes the mic and clears Silero's recurrent state."""
    detector = ResettableDetector([0.9, 0.0, 0.0])
    endpointer = Endpointer(detect=detector, threshold=0.5, silence_ms=64)
    flushes = 0

    def reset_source() -> None:
        nonlocal flushes
        flushes += 1

    capture_until_endpoint(
        _source([b"\x07\x07" * 512 for _ in range(3)]),
        endpointer=endpointer,
        sample_rate=16_000,
        max_seconds=10.0,
        reset_source=reset_source,
    )

    assert flushes == 1
    assert detector.resets == 1


def test_capture_rejects_nonpositive_max_seconds() -> None:
    endpointer, _ = _endpointer([0.0], silence_ms=64)
    with pytest.raises(ValueError):
        capture_until_endpoint(
            _repeating_source(b"\x00\x00" * 512),
            endpointer=endpointer,
            sample_rate=16_000,
            max_seconds=0.0,
        )
