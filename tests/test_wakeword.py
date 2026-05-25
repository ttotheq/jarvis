"""Tests for wake-word detection (Phase 2 goal G2.1).

Written before ``jarvis.wakeword`` exists (TDD, per ADR-0005). The native
openWakeWord model is injected as a :data:`Detector` callable (one PCM16 frame in,
one score out), so the listening logic and the true-accept / false-accept metric
are exercised with fakes — no microphone, no model download in CI. The *live*
measurement over labeled "hey jarvis" recordings needs the real model and audio
fixtures; that path is asserted when the fixtures exist and skipped with an
explicit message until they are recorded (the live step, alongside the ambient
soak in ``scripts/soak_wakeword.py``).
"""

from __future__ import annotations

import json
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.audio import Clip
from jarvis.wakeword import (
    FRAME_BYTES,
    Accuracy,
    WakeWordListener,
    evaluate,
    iter_frames,
)

FIXTURES = Path(__file__).parent / "fixtures" / "wakeword"
MANIFEST = FIXTURES / "manifest.json"


def _clip(num_frames: int) -> Clip:
    """A silent clip exactly ``num_frames`` wake-word frames long."""
    return Clip(samples=b"\x00" * (FRAME_BYTES * num_frames), sample_rate=16_000)


def _scripted(scores: list[float]) -> Iterator[float]:
    return iter(scores)


# --- the two write-first detection tests ----------------------------------


def test_wakeword_fires_on_positive_clip() -> None:
    """On a "hey jarvis" clip the score crosses the threshold and the listener fires."""
    scores = _scripted([0.01, 0.04, 0.72, 0.91])  # rises past 0.5 partway through
    listener = WakeWordListener(detect=lambda _frame: next(scores), threshold=0.5)
    assert listener.fires_on(_clip(4)) is True


def test_wakeword_silent_on_negative() -> None:
    """On non-wake speech / ambient noise the score never crosses; no fire."""
    scores = _scripted([0.02, 0.18, 0.31, 0.07, 0.12])  # noisy but always < 0.5
    listener = WakeWordListener(detect=lambda _frame: next(scores), threshold=0.5)
    assert listener.fires_on(_clip(5)) is False


# --- listener mechanics ---------------------------------------------------


def test_wait_for_wake_short_circuits_on_first_crossing() -> None:
    """Detection stops at the first crossing — an unbounded mic stream is fine."""
    calls = {"n": 0}

    def detect(_frame: bytes) -> float:
        calls["n"] += 1
        return 0.9 if calls["n"] == 2 else 0.1

    listener = WakeWordListener(detect=detect, threshold=0.5)
    frames = iter([b"\x00" * FRAME_BYTES] * 1000)
    assert listener.wait_for_wake(frames) is True
    assert calls["n"] == 2  # scored only until the crossing, not all 1000 frames


def test_wait_for_wake_false_when_frames_exhausted() -> None:
    listener = WakeWordListener(detect=lambda _f: 0.0, threshold=0.5)
    assert listener.wait_for_wake([b"\x00" * FRAME_BYTES] * 3) is False


def test_threshold_is_inclusive() -> None:
    """A score exactly at the threshold counts as a wake."""
    listener = WakeWordListener(detect=lambda _f: 0.5, threshold=0.5)
    assert listener.scored(b"\x00" * FRAME_BYTES) is True


def test_iter_frames_splits_and_drops_short_tail() -> None:
    clip = Clip(samples=b"\x01" * (FRAME_BYTES * 3 + 17), sample_rate=16_000)
    frames = list(iter_frames(clip))
    assert len(frames) == 3  # the trailing 17-byte partial frame is dropped
    assert all(len(f) == FRAME_BYTES for f in frames)


def test_iter_frames_empty_clip_yields_nothing() -> None:
    assert list(iter_frames(Clip(samples=b"", sample_rate=16_000))) == []


# --- the G2.1 accuracy metric ---------------------------------------------


def test_accuracy_rates() -> None:
    acc = Accuracy(positives=20, true_accepts=19, false_accepts=1, ambient_seconds=1800.0)
    assert acc.true_accept_rate == pytest.approx(0.95)
    assert acc.false_accepts_per_30min == pytest.approx(1.0)


def test_accuracy_scales_false_accepts_to_30_min() -> None:
    # 2 false accepts over 15 minutes projects to 4 per 30 minutes.
    acc = Accuracy(positives=0, true_accepts=0, false_accepts=2, ambient_seconds=900.0)
    assert acc.false_accepts_per_30min == pytest.approx(4.0)


def test_accuracy_empty_is_well_defined() -> None:
    acc = Accuracy(positives=0, true_accepts=0, false_accepts=0, ambient_seconds=0.0)
    assert acc.true_accept_rate == 1.0  # vacuously perfect, never NaN
    assert acc.false_accepts_per_30min == 0.0


def test_evaluate_tallies_true_and_false_accepts() -> None:
    positives = [_clip(2), _clip(2), _clip(2)]
    ambient = [_clip(2), _clip(2)]
    # Fire on the first two positives and on the second ambient clip.
    will_fire = {id(positives[0]), id(positives[1]), id(ambient[1])}
    acc = evaluate(
        fires_on=lambda clip: id(clip) in will_fire,
        positives=positives,
        ambient=ambient,
    )
    assert acc.positives == 3
    assert acc.true_accepts == 2
    assert acc.false_accepts == 1
    assert acc.ambient_seconds == pytest.approx(sum(c.duration_s for c in ambient))
    assert acc.true_accept_rate == pytest.approx(2 / 3)


# --- live verification over labeled fixtures (skipped until recorded) ------


def _load_wav(path: Path) -> Clip:
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        samples = wav.readframes(wav.getnframes())
    return Clip(samples=samples, sample_rate=sample_rate)


def test_labeled_fixtures_meet_targets() -> None:
    if not MANIFEST.exists():
        pytest.skip("wake-word audio fixtures not yet recorded (live G2.1 step)")
    from jarvis.wakeword import OpenWakeWordDetector  # native, voice extra only

    manifest = json.loads(MANIFEST.read_text())
    positives = [_load_wav(FIXTURES / p) for p in manifest["positives"]]
    ambient = [_load_wav(FIXTURES / p) for p in manifest["ambient"]]
    assert len(positives) >= 20, "G2.1 requires a 20-utterance positive set"

    detector = OpenWakeWordDetector()
    threshold = manifest.get("threshold", 0.5)

    def fires_on(clip: Clip) -> bool:
        detector.reset()  # independent clips: clear the rolling buffer between them
        return WakeWordListener(detect=detector, threshold=threshold).fires_on(clip)

    acc = evaluate(fires_on=fires_on, positives=positives, ambient=ambient)
    assert acc.true_accept_rate >= 0.95, f"true-accept {acc.true_accept_rate:.2%} < 95%"
    assert acc.false_accepts_per_30min <= 1.0, (
        f"false-accept {acc.false_accepts_per_30min:.2f}/30min > 1"
    )
