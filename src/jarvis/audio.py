"""Microphone capture and speaker playback for the push-to-talk skeleton.

Audio is carried as raw little-endian PCM16 :class:`Clip` bytes so the core
package needs neither numpy nor sounddevice to import — those native wheels are
the optional ``voice`` extra. The capture loop (:func:`record`) is fully
device-agnostic: it pulls frames from an injected :data:`FrameSource` until a
``stop`` predicate fires, so it is tested with a fake source. The real
sounddevice-backed source, shared microphone, and speaker are thin hardware
shims, isolated and excluded from coverage per ADR-0005.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from queue import Empty, Full, Queue
from typing import Protocol

#: A frame source: returns the next chunk of PCM16 bytes from the input device.
FrameSource = Callable[[], bytes]

_BYTES_PER_SAMPLE = 2  # PCM16


@dataclass(frozen=True)
class Clip:
    """A mono PCM16 audio buffer with its sample rate."""

    samples: bytes
    sample_rate: int

    @property
    def num_samples(self) -> int:
        return len(self.samples) // _BYTES_PER_SAMPLE

    @property
    def duration_s(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.num_samples / self.sample_rate


class Speaker(Protocol):
    """Anything that can play a clip through an output device.

    ``stop`` aborts playback in progress; it is what makes the SPEAKING state
    cancellable for barge-in (G3.1) — the consumer can halt a clip mid-utterance
    rather than waiting for the sentence to finish.
    """

    def play(self, clip: Clip) -> None: ...
    def stop(self) -> None: ...


class Microphone(Protocol):
    """A long-lived mono PCM16 microphone that can be shared across phases."""

    def read(self) -> bytes: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


def record(source: FrameSource, stop: Callable[[], bool], sample_rate: int) -> Clip:
    """Pull frames from ``source`` until ``stop()`` is true; return one clip.

    ``stop`` is checked before each read, so a predicate that is already true
    yields an empty clip. This is the push-to-talk capture loop: hold the key
    (``stop`` stays false), release it (``stop`` flips true).
    """
    chunks: list[bytes] = []
    while not stop():
        chunks.append(source())
    return Clip(samples=b"".join(chunks), sample_rate=sample_rate)


def resample_mono_pcm16(samples: bytes, input_rate: int, output_rate: int) -> bytes:
    """Linearly resample mono PCM16 between sample rates.

    The shared live mic can run at any configured input rate, while wake-word
    detectors require 16 kHz frames. This keeps the detector fed with valid
    samples without opening a second input stream.
    """
    if input_rate <= 0 or output_rate <= 0:
        raise ValueError("sample rates must be positive")
    if input_rate == output_rate or not samples:
        return samples

    source = memoryview(samples).cast("h")
    out_len = max(1, round(len(source) * output_rate / input_rate))
    if len(source) == 1:
        repeated = array("h", [int(source[0])] * out_len)
        if repeated.itemsize != _BYTES_PER_SAMPLE:
            raise ValueError("expected PCM16 samples")
        return repeated.tobytes()

    step = input_rate / output_rate
    resampled = array("h")
    for i in range(out_len):
        position = i * step
        left = min(int(position), len(source) - 1)
        right = min(left + 1, len(source) - 1)
        blend = position - left
        sample = round((1.0 - blend) * int(source[left]) + blend * int(source[right]))
        resampled.append(max(-32_768, min(32_767, sample)))
    if resampled.itemsize != _BYTES_PER_SAMPLE:
        raise ValueError("expected PCM16 samples")
    return resampled.tobytes()


class SoundDeviceSpeaker:  # pragma: no cover - requires PortAudio + a real device
    """Plays clips through the default output device via sounddevice."""

    def play(self, clip: Clip) -> None:
        import numpy as np
        import sounddevice as sd

        audio = np.frombuffer(clip.samples, dtype=np.int16)
        sd.play(audio, samplerate=clip.sample_rate)
        sd.wait()

    def stop(self) -> None:
        import sounddevice as sd

        sd.stop()  # aborts the in-flight sd.play/sd.wait so barge-in can interrupt


class SoundDeviceMicrophone:  # pragma: no cover - requires PortAudio + a real device
    """A persistent input stream buffered in user space for shared consumption."""

    def __init__(
        self,
        sample_rate: int,
        block_frames: int,
        device: str | int | None = None,
        max_queue_blocks: int = 64,
    ) -> None:
        import sounddevice as sd

        self._frames: Queue[bytes] = Queue(maxsize=max_queue_blocks)

        def _callback(indata: bytes, _frames: int, _time: object, _status: object) -> None:
            block = bytes(indata)
            if self._frames.full():
                with suppress(Empty):
                    self._frames.get_nowait()
            with suppress(Full):
                self._frames.put_nowait(block)

        self._stream = sd.RawInputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=block_frames,
            device=device,
            callback=_callback,
        )
        self._stream.start()

    def read(self) -> bytes:
        return self._frames.get()

    def flush(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except Empty:
                return

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


def make_sounddevice_source(  # pragma: no cover - requires PortAudio + a real device
    sample_rate: int,
    block_frames: int = 1_600,
    device: str | int | None = None,
) -> FrameSource:
    """Build a :data:`FrameSource` that reads PCM16 blocks from the mic."""
    import sounddevice as sd

    stream = sd.RawInputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=block_frames,
        device=device,
    )
    stream.start()

    def _read() -> bytes:
        data, _overflowed = stream.read(block_frames)
        return bytes(data)

    return _read
