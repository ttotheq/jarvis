"""Text-to-speech via Kokoro (British male voice).

The synthesizer is a :data:`Synthesizer` callable so the loop depends on the
interface, not Kokoro. :func:`speak` is the dispatch logic — it refuses to voice
an empty reply and otherwise synthesizes and plays — and is fully tested with
fakes. The Kokoro backend is a native shim excluded from coverage (ADR-0005);
the voice is configurable via ``JARVIS_TTS_VOICE`` (see docs/voice-persona.md).
"""

from __future__ import annotations

from collections.abc import Callable

from jarvis.audio import Clip, SoundDeviceStreamingSpeaker, Speaker
from jarvis.config import Settings, get_settings

#: A synthesizer: turns reply text into a playable clip.
Synthesizer = Callable[[str], Clip]


def speak(text: str, synthesize: Synthesizer, speaker: Speaker) -> bool:
    """Synthesize ``text`` and play it; return whether anything was spoken.

    A blank or whitespace-only reply is silently skipped (there is nothing worth
    voicing), so the loop never plays an empty clip.
    """
    if not text.strip():
        return False
    speaker.play(synthesize(text))
    return True


class KokoroSynthesizer:  # pragma: no cover - requires the Kokoro model + espeak-ng
    """Synthesize speech with Kokoro at the configured voice and speed."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else get_settings()
        from kokoro import KPipeline

        # 'b' = British English; the specific voice is chosen per utterance.
        self._pipeline = KPipeline(lang_code="b")

    def __call__(self, text: str) -> Clip:
        import numpy as np

        chunks = [
            audio
            for _gs, _ps, audio in self._pipeline(
                text, voice=self._settings.tts_voice, speed=self._settings.tts_speed
            )
        ]
        samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        return Clip(samples=pcm16, sample_rate=24_000)


def build_default_synthesizer() -> Synthesizer:  # pragma: no cover - native
    return KokoroSynthesizer()


def build_default_speaker() -> SoundDeviceStreamingSpeaker:  # pragma: no cover - native
    """The persistent-stream speaker: gapless multi-sentence playback (G4.6)."""
    return SoundDeviceStreamingSpeaker(device=get_settings().output_device)
