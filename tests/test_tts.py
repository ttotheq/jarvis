"""Tests for speech synthesis dispatch (Phase 1, jarvis.tts).

Kokoro synthesis itself is a native shim; the testable logic is :func:`speak`,
which guards against voicing empty replies and forwards real prose to the
synthesizer and speaker. Both are injected as fakes here.
"""

from __future__ import annotations

from jarvis.audio import Clip
from jarvis.tts import speak


class FakeSynth:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, text: str) -> Clip:
        self.texts.append(text)
        return Clip(samples=b"\x01\x02" * len(text), sample_rate=24_000)


class FakeSpeaker:
    def __init__(self) -> None:
        self.played: list[Clip] = []

    def play(self, clip: Clip) -> None:
        self.played.append(clip)


def test_speak_synthesizes_and_plays_prose() -> None:
    synth, speaker = FakeSynth(), FakeSpeaker()
    spoke = speak("All set, sir.", synth, speaker)
    assert spoke is True
    assert synth.texts == ["All set, sir."]
    assert len(speaker.played) == 1


def test_speak_skips_empty_text() -> None:
    synth, speaker = FakeSynth(), FakeSpeaker()
    assert speak("", synth, speaker) is False
    assert speak("   \n\t ", synth, speaker) is False
    assert synth.texts == []
    assert speaker.played == []
