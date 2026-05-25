"""Tests for mic capture and playback (Phase 1, jarvis.audio).

The device-agnostic record loop is exercised against a fake frame source so the
record -> buffer -> playback path is verified without PortAudio or a real
microphone. The native sounddevice backend is a thin, hardware-only shim.
"""

from __future__ import annotations

from jarvis.audio import Clip, record


class FakeSource:
    """Yields a fixed list of PCM frames, one per call."""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.reads = 0

    def __call__(self) -> bytes:
        self.reads += 1
        return self._frames.pop(0)


class FakeSpeaker:
    """Captures clips handed to it instead of playing them."""

    def __init__(self) -> None:
        self.played: list[Clip] = []

    def play(self, clip: Clip) -> None:
        self.played.append(clip)


def test_clip_reports_samples_and_duration() -> None:
    clip = Clip(samples=b"\x00\x00" * 16_000, sample_rate=16_000)
    assert clip.num_samples == 16_000
    assert clip.duration_s == 1.0


def test_clip_empty_duration_is_zero() -> None:
    assert Clip(samples=b"", sample_rate=16_000).duration_s == 0.0


def test_record_accumulates_until_stop() -> None:
    source = FakeSource([b"\x01\x02", b"\x03\x04", b"\x05\x06"])
    # Stop after three frames have been pulled.
    clip = record(source, stop=lambda: source.reads >= 3, sample_rate=16_000)
    assert clip.samples == b"\x01\x02\x03\x04\x05\x06"
    assert clip.sample_rate == 16_000


def test_record_stops_immediately_yields_empty_clip() -> None:
    source = FakeSource([b"\x01\x02"])
    clip = record(source, stop=lambda: True, sample_rate=16_000)
    assert clip.samples == b""


def test_record_then_playback_roundtrip() -> None:
    source = FakeSource([b"ab", b"cd"])
    speaker = FakeSpeaker()
    clip = record(source, stop=lambda: source.reads >= 2, sample_rate=8_000)
    speaker.play(clip)
    assert speaker.played == [clip]
    assert speaker.played[0].samples == b"abcd"
