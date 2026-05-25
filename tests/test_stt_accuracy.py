"""Tests for STT accuracy measurement (Phase 1 goal G1.2).

Word error rate (WER) is the metric for G1.2 (target <= 10%). Its computation is
pure and fully tested here. The *live* measurement over a 20-utterance dev set
requires whisper.cpp transcribing real recordings; that manifest is read here
and asserted when present, and skipped with an explicit message until the
recordings exist (after the Phase 1 setup step installs the voice stack).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.stt import word_error_rate

DEVSET = Path(__file__).parent / "fixtures" / "stt" / "devset.json"


def test_wer_identical_is_zero() -> None:
    assert word_error_rate("open the pod bay doors", "open the pod bay doors") == 0.0


def test_wer_is_case_and_punctuation_insensitive() -> None:
    assert word_error_rate("Open the pod bay doors.", "open the pod bay doors") == 0.0


def test_wer_single_substitution() -> None:
    # one of four words wrong -> 0.25
    assert word_error_rate("turn on the lights", "turn on the light") == pytest.approx(0.25)


def test_wer_insertion_and_deletion() -> None:
    assert word_error_rate("hello there", "hello") == pytest.approx(0.5)  # deletion
    assert word_error_rate("hello", "hello there") == pytest.approx(1.0)  # insertion


def test_wer_empty_reference() -> None:
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "noise") == 1.0


def test_devset_wer_under_threshold() -> None:
    if not DEVSET.exists():
        pytest.skip("STT dev set not yet recorded (Phase 1 setup step)")
    entries = json.loads(DEVSET.read_text())
    pending = [e for e in entries if not e.get("hypothesis")]
    if pending:
        pytest.skip(
            f"{len(pending)}/{len(entries)} dev-set utterances lack a whisper.cpp "
            "transcription; record them after installing the voice stack"
        )
    assert len(entries) >= 20, "G1.2 requires a 20-utterance dev set"
    mean_wer = sum(word_error_rate(e["reference"], e["hypothesis"]) for e in entries) / len(entries)
    assert mean_wer <= 0.10, f"mean WER {mean_wer:.3f} exceeds 10% target"
