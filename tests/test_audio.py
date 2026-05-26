"""Tests for mic capture and playback (Phase 1, jarvis.audio).

The device-agnostic record loop is exercised against a fake frame source so the
record -> buffer -> playback path is verified without PortAudio or a real
microphone. The native sounddevice backend is a thin, hardware-only shim.
"""

from __future__ import annotations

from jarvis.audio import Clip, record, resample_mono_pcm16


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


def test_resample_mono_pcm16_noop_when_rates_match() -> None:
    samples = b"\x01\x00\x02\x00\x03\x00"
    assert resample_mono_pcm16(samples, input_rate=16_000, output_rate=16_000) == samples


def test_resample_mono_pcm16_downsamples_to_expected_length() -> None:
    samples = b"".join(i.to_bytes(2, "little", signed=True) for i in range(12))
    resampled = resample_mono_pcm16(samples, input_rate=48_000, output_rate=16_000)
    assert len(resampled) == len(samples) // 3
