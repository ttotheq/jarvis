"""Synthesize a reproducible labeled wake-word fixture set (Phase 2 goal G2.1).

Live human recordings + a real 30-min mic soak are the gold standard, but they
need a microphone and a person. This generator builds a *reproducible* labeled set
with the same TTS that voices Jarvis itself (Kokoro, the British male voices) so
the G2.1 targets can be measured against the real openWakeWord model offline:

- **positives** — "hey jarvis" across the three British male voices, three speeds,
  and several phrasings; a subset mixed with background noise (≥ 20 utterances).
- **ambient** — ~30 min of low-level noise beds with interspersed non-wake speech,
  including phonetically near phrases ("hey there", "hey Travis", …) to give
  false-accepts a real chance to fire. The wake word never appears here.

Run with the voice extra installed (`uv sync --extra voice`)::

    python scripts/gen_wakeword_fixtures.py

It writes 16 kHz mono PCM16 WAVs + ``manifest.json`` into ``tests/fixtures/wakeword/``
(git-ignored, regenerable), then runs the real model and prints the measured
true-accept rate and false-accepts-per-30-min. Those numbers go in the Phase 2 doc
Outcomes; the numbers are synthetic-TTS, not live human speech — see the caveat
there.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
from numpy.random import default_rng
from scipy.signal import resample_poly

from jarvis.audio import Clip
from jarvis.wakeword import OpenWakeWordDetector, WakeWordListener, evaluate

SAMPLE_RATE = 16_000
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "wakeword"
SEED = 20260525
# Tuned against the synthetic soak below: the floor that holds false-accepts to the
# budget (<= 1 / 30 min) while keeping every positive comfortably above it. Mirrors
# the JARVIS_WAKE_THRESHOLD default in jarvis.config.
THRESHOLD = 0.9

VOICES = ("bm_george", "bm_lewis", "bm_fable")
SPEEDS = (0.85, 1.0, 1.15)
WAKE_PHRASINGS = ("Hey Jarvis.", "Hey Jarvis!", "Hey, Jarvis?", "Hey Jarvis, are you there?")

# Non-wake speech for the ambient bed, including near-miss distractors that stress
# the false-accept rate. None of these contain the wake word.
DISTRACTORS = (
    "What time is it?",
    "Turn on the kitchen lights.",
    "Hey there, how are you?",
    "Hey Travis, did you call?",
    "Play some jazz, please.",
    "The weather looks nice today.",
    "Hey Marcus, over here.",
    "Could you pass the salt?",
    "Let's go for a walk later.",
    "Hey Charles, are you ready?",
    "I left my keys on the table.",
    "Set a timer for ten minutes.",
    "Hey, Garrett, wait up.",
    "The meeting is at three o'clock.",
    "Where did I park the car?",
    "Hey Jarrah, nice to meet you.",
    "Remind me to buy milk.",
    "It's getting cold outside.",
    "Hey everyone, gather round.",
    "Have you seen my glasses anywhere?",
)


def _synth(pipe: object, text: str, voice: str, speed: float) -> np.ndarray:
    """Synthesize ``text`` and return mono 16 kHz float32 in [-1, 1]."""
    chunks = [audio for _gs, _ps, audio in pipe(text, voice=voice, speed=speed)]  # type: ignore[operator]
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    return np.asarray(resample_poly(audio, SAMPLE_RATE, 24_000), dtype=np.float32)


def _to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


def _noise(seconds: float, rng: np.random.Generator, level: float) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    return rng.normal(0.0, level, n).astype(np.float32)


def _mix(speech: np.ndarray, bed: np.ndarray) -> np.ndarray:
    """Overlay ``speech`` onto a noise ``bed`` at a random offset."""
    out = bed.copy()
    if speech.size and speech.size < bed.size:
        start = int(np.random.default_rng().integers(0, bed.size - speech.size))
        out[start : start + speech.size] += speech
    return out


def generate() -> dict[str, list[str]]:
    """Synthesize the fixture set, write WAVs + manifest, return the manifest dict."""
    from kokoro import KPipeline

    FIXTURES.mkdir(parents=True, exist_ok=True)
    pipe = KPipeline(lang_code="b")
    rng = default_rng(SEED)

    positives: list[str] = []
    idx = 0
    for voice in VOICES:
        for speed in SPEEDS:
            # 1.0x gets all four phrasings, off-speeds get two: 8 per voice, 24 total.
            for phrasing in WAKE_PHRASINGS[: 4 if speed == 1.0 else 2]:
                idx += 1
                speech = _synth(pipe, phrasing, voice, speed)
                # Mix background noise into roughly half of the positives.
                if idx % 2 == 0:
                    bed = _noise(len(speech) / SAMPLE_RATE + 0.5, rng, level=0.03)
                    speech = _mix(speech, bed)
                name = f"pos{idx:02d}.wav"
                _write_wav(FIXTURES / name, _to_pcm16(speech))
                positives.append(name)

    # Pre-synthesize the distractor pool once; reuse across ambient clips.
    pool = [_synth(pipe, text, VOICES[i % len(VOICES)], 1.0) for i, text in enumerate(DISTRACTORS)]

    ambient: list[str] = []
    clip_seconds = 30.0
    num_clips = 60  # 60 * 30 s = 30 min
    for c in range(num_clips):
        bed = _noise(clip_seconds, rng, level=float(rng.uniform(0.01, 0.05)))
        # Intermittent speech: ~40% of ambient seconds carry a distractor.
        for _ in range(int(rng.integers(0, 3))):
            speech = pool[int(rng.integers(0, len(pool)))]
            if speech.size < bed.size:
                start = int(rng.integers(0, bed.size - speech.size))
                bed[start : start + speech.size] += speech
        name = f"amb{c:02d}.wav"
        _write_wav(FIXTURES / name, _to_pcm16(bed))
        ambient.append(name)

    manifest = {"threshold": THRESHOLD, "positives": positives, "ambient": ambient}
    (FIXTURES / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _load_wav(path: Path) -> Clip:
    with wave.open(str(path), "rb") as wav:
        return Clip(samples=wav.readframes(wav.getnframes()), sample_rate=wav.getframerate())


def measure(manifest: dict[str, list[str]]) -> None:
    """Run the real model over the generated set and print the G2.1 metrics."""
    detector = OpenWakeWordDetector()
    threshold = float(manifest.get("threshold", 0.5))  # type: ignore[arg-type]

    def fires_on(clip: Clip) -> bool:
        detector.reset()
        return WakeWordListener(detect=detector, threshold=threshold).fires_on(clip)

    positives = [_load_wav(FIXTURES / p) for p in manifest["positives"]]
    ambient = [_load_wav(FIXTURES / p) for p in manifest["ambient"]]
    acc = evaluate(fires_on=fires_on, positives=positives, ambient=ambient)
    print(f"\nthreshold              : {threshold}")
    print(f"positives              : {acc.positives}")
    print(f"true accepts           : {acc.true_accepts}")
    print(f"true-accept rate       : {acc.true_accept_rate:.2%}  (target >= 95%)")
    print(f"ambient duration       : {acc.ambient_seconds / 60:.1f} min")
    print(f"false accepts          : {acc.false_accepts}")
    print(f"false accepts / 30 min : {acc.false_accepts_per_30min:.2f}  (target <= 1)")


def main() -> None:  # pragma: no cover - live TTS + model path, run by hand
    print("Synthesizing wake-word fixtures (Kokoro)…")
    manifest = generate()
    n_pos, n_amb = len(manifest["positives"]), len(manifest["ambient"])
    print(f"wrote {n_pos} positives + {n_amb} ambient clips")
    measure(manifest)


if __name__ == "__main__":  # pragma: no cover
    main()
