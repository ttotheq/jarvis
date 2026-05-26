"""Tests for VAD endpointing (Phase 2 goal G2.2).

Written before ``jarvis.vad`` exists (TDD, per ADR-0005). The native Silero VAD
model is injected as a :data:`Detector` callable (one PCM16 frame in, one speech
probability out), so the endpointing logic — a rolling trailing-silence
accumulator that fires once speech has stopped — is exercised with fakes: no
model, no microphone. The endpointer owns only the threshold comparison and the
silence bookkeeping; the Silero backend is a native shim covered by the live
benchmark, not the unit suite.
"""

from __future__ import annotations

from collections.abc import Iterator

from jarvis.audio import Clip
from jarvis.vad import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    Endpointer,
    OnsetDetector,
    iter_frames,
)

SILENT = b"\x00" * FRAME_BYTES


def _scripted(scores: list[float]) -> Iterator[float]:
    return iter(scores)


def _endpointer(scores: list[float], *, silence_ms: int, frame_ms: float) -> Endpointer:
    """An endpointer whose per-frame probability is read off ``scores`` in order."""
    it = _scripted(scores)
    return Endpointer(
        detect=lambda _frame: next(it),
        threshold=0.5,
        silence_ms=silence_ms,
        frame_ms=frame_ms,
    )


# --- the write-first endpoint test ----------------------------------------


def test_vad_endpoints_after_silence() -> None:
    """Speech then trailing silence >= vad_silence_ms fires the endpoint once.

    Two speech frames then four silent ones at 100 ms/frame, 300 ms window: the
    accumulator reaches 300 ms on the third silent frame and fires there — exactly
    once, not once per subsequent silent frame.
    """
    ep = _endpointer([0.9, 0.9, 0.1, 0.1, 0.1, 0.1], silence_ms=300, frame_ms=100.0)
    fires = [ep.feed(SILENT) for _ in range(6)]
    assert fires == [False, False, False, False, True, False]
    assert sum(fires) == 1  # exactly one endpoint, not one per silent frame


# --- endpointer mechanics -------------------------------------------------


def test_leading_silence_does_not_endpoint() -> None:
    """Silence before any speech never fires — there is no utterance to end."""
    ep = _endpointer([0.1] * 5, silence_ms=100, frame_ms=50.0)
    assert all(ep.feed(SILENT) is False for _ in range(5))


def test_speech_resets_the_silence_accumulator() -> None:
    """A speech frame mid-pause restarts the trailing-silence count.

    Without the reset the window (150 ms / 50 ms = 3 frames) would close on the
    third frame; the speech frame at index 3 pushes the firing frame out to 6.
    """
    ep = _endpointer([0.9, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1], silence_ms=150, frame_ms=50.0)
    assert ep.endpoint([SILENT] * 7) == 6


def test_endpoint_returns_firing_index() -> None:
    ep = _endpointer([0.9, 0.1, 0.1], silence_ms=100, frame_ms=50.0)
    assert ep.endpoint([SILENT] * 3) == 2  # speech, +50 ms, +50 ms == 100 ms -> fire


def test_endpoint_returns_none_when_speech_never_stops() -> None:
    ep = Endpointer(detect=lambda _f: 0.9, threshold=0.5, silence_ms=100, frame_ms=50.0)
    assert ep.endpoint([SILENT] * 10) is None


def test_endpoint_returns_none_when_frames_exhausted_mid_pause() -> None:
    """A pause shorter than the window leaves no endpoint."""
    ep = _endpointer([0.9, 0.1], silence_ms=100, frame_ms=50.0)
    assert ep.endpoint([SILENT] * 2) is None  # only 50 ms of trailing silence


def test_threshold_is_inclusive_so_boundary_frame_is_speech() -> None:
    """A probability exactly at the threshold is speech, so it never endpoints."""
    ep = Endpointer(detect=lambda _f: 0.5, threshold=0.5, silence_ms=50, frame_ms=50.0)
    assert ep.endpoint([SILENT] * 10) is None


def test_feed_is_idempotent_after_firing() -> None:
    """Once fired the endpointer stays fired — later frames never re-fire."""
    ep = _endpointer([0.9, 0.1, 0.1, 0.1, 0.1], silence_ms=100, frame_ms=50.0)
    fired_at = [i for i in range(5) if ep.feed(SILENT)]
    assert fired_at == [2]


def test_reset_clears_state_for_reuse() -> None:
    scores = _scripted([0.9, 0.1, 0.1, 0.9, 0.1, 0.1])
    ep = Endpointer(detect=lambda _f: next(scores), threshold=0.5, silence_ms=100, frame_ms=50.0)
    assert ep.endpoint([SILENT] * 3) == 2
    ep.reset()
    assert ep.endpoint([SILENT] * 3) == 2  # fresh state, fires again


# --- speech onset (the barge-in rising edge, G3.1) ------------------------


def _onset(scores: list[float]) -> OnsetDetector:
    """An onset detector whose per-frame probability is read off ``scores``."""
    it = _scripted(scores)
    return OnsetDetector(detect=lambda _frame: next(it), threshold=0.5)


def test_onset_fires_on_first_speech_frame() -> None:
    """Silence then speech: the onset fires on the first frame at/above threshold."""
    det = _onset([0.1, 0.2, 0.9, 0.1])
    fires = [det.feed(SILENT) for _ in range(4)]
    assert fires == [False, False, True, False]


def test_onset_never_fires_on_pure_silence() -> None:
    det = _onset([0.0, 0.1, 0.2, 0.3])
    assert all(det.feed(SILENT) is False for _ in range(4))


def test_onset_latches_so_continuous_speech_fires_once() -> None:
    """A run of speech frames yields one onset, not one per voiced frame."""
    det = _onset([0.9, 0.9, 0.9, 0.9])
    fires = [det.feed(SILENT) for _ in range(4)]
    assert fires == [True, False, False, False]


def test_onset_threshold_is_inclusive() -> None:
    """A probability exactly at the threshold counts as speech and fires."""
    det = OnsetDetector(detect=lambda _f: 0.5, threshold=0.5)
    assert det.feed(SILENT) is True


def test_onset_returns_firing_index() -> None:
    det = _onset([0.1, 0.1, 0.9])
    assert det.onset([SILENT] * 3) == 2


def test_onset_returns_none_when_no_speech() -> None:
    det = _onset([0.1, 0.2, 0.3])
    assert det.onset([SILENT] * 3) is None


def test_onset_reset_clears_the_latch() -> None:
    scores = _scripted([0.9, 0.1, 0.9])
    det = OnsetDetector(detect=lambda _f: next(scores), threshold=0.5)
    assert det.onset([SILENT]) == 0
    det.reset()
    assert det.onset([SILENT] * 2) == 1  # fresh latch, fires again


# --- frame geometry (mirrors jarvis.wakeword) -----------------------------


def test_frame_geometry_matches_silero_16khz() -> None:
    """Silero VAD at 16 kHz consumes exactly 512-sample (1024-byte) frames."""
    assert FRAME_SAMPLES == 512
    assert FRAME_BYTES == 1024


def test_iter_frames_splits_and_drops_short_tail() -> None:
    clip = Clip(samples=b"\x01" * (FRAME_BYTES * 3 + 17), sample_rate=16_000)
    frames = list(iter_frames(clip))
    assert len(frames) == 3  # the trailing 17-byte partial frame is dropped
    assert all(len(f) == FRAME_BYTES for f in frames)


def test_iter_frames_empty_clip_yields_nothing() -> None:
    assert list(iter_frames(Clip(samples=b"", sample_rate=16_000))) == []
