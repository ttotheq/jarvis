"""Tests for the G1.2 dev-set recorder helper (scripts/record_devset.py).

The mic capture and whisper.cpp transcription are hardware shims; the testable
core is :func:`record_devset`, which walks the manifest, fills missing
hypotheses from an injected capture+transcribe pair, and leaves existing ones
untouched. Fakes stand in for the hardware here.
"""

from __future__ import annotations

from record_devset import record_devset

from jarvis.audio import Clip


def _clip() -> Clip:
    return Clip(samples=b"\x00\x00", sample_rate=16_000)


def test_fills_missing_hypotheses_in_order() -> None:
    entries = [
        {"audio": "utt01.wav", "reference": "turn on the lights", "hypothesis": None},
        {"audio": "utt02.wav", "reference": "what time is it", "hypothesis": None},
    ]
    scripted = iter(["turn on the lights", "what time is it"])
    updated = record_devset(entries, capture=_clip, transcribe=lambda _c: next(scripted))
    assert [e["hypothesis"] for e in updated] == ["turn on the lights", "what time is it"]


def test_leaves_existing_hypotheses_untouched() -> None:
    entries = [
        {"audio": "utt01.wav", "reference": "alpha", "hypothesis": "already done"},
        {"audio": "utt02.wav", "reference": "beta", "hypothesis": None},
    ]
    calls: list[Clip] = []

    def _transcribe(c: Clip) -> str:
        calls.append(c)
        return "beta"

    updated = record_devset(entries, capture=_clip, transcribe=_transcribe)
    assert updated[0]["hypothesis"] == "already done"
    assert updated[1]["hypothesis"] == "beta"
    assert len(calls) == 1  # only the missing one is captured


def test_does_not_mutate_input_entries() -> None:
    entries = [{"audio": "utt01.wav", "reference": "alpha", "hypothesis": None}]
    record_devset(entries, capture=_clip, transcribe=lambda _c: "alpha")
    assert entries[0]["hypothesis"] is None
