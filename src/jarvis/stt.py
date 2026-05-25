"""Speech-to-text via whisper.cpp.

The transcriber is a :data:`Transcriber` callable so the loop depends on the
interface, not the binary. :func:`word_error_rate` — the metric for Phase 1 goal
G1.2 — is pure and tested directly. The whisper.cpp backend writes the clip to a
temporary WAV and shells out to the CLI; it is a hardware/binary shim excluded
from coverage (ADR-0005).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from jarvis.audio import Clip
from jarvis.config import Settings, get_settings

#: A transcriber: turns a recorded clip into text.
Transcriber = Callable[[Clip], str]

_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> list[str]:
    """Lower-case word tokens, punctuation stripped — for fair WER scoring."""
    return _WORD.findall(text.lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word error rate: word-level edit distance over reference word count.

    Case- and punctuation-insensitive. An empty reference scores 0.0 against an
    empty hypothesis and 1.0 against any words (everything inserted).
    """
    ref = _tokens(reference)
    hyp = _tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0

    # Levenshtein distance over word tokens (substitution/insertion/deletion).
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        curr = [i]
        for j, h in enumerate(hyp, start=1):
            cost = 0 if r == h else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1] / len(ref)


class WhisperCppTranscriber:  # pragma: no cover - requires the whisper.cpp binary
    """Transcribe a clip by shelling out to the whisper.cpp CLI."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else get_settings()

    def __call__(self, clip: Clip) -> str:
        import shutil
        import subprocess
        import tempfile
        import wave
        from pathlib import Path

        binary = next(
            (b for b in ("whisper-cli", "whisper-cpp", "whisper") if shutil.which(b)),
            None,
        )
        if binary is None:
            raise RuntimeError("whisper.cpp CLI not found on PATH (run `jarvis doctor`)")

        # whisper-cli's -m takes a GGML model file, not a model name: resolve
        # <stt_model_dir>/ggml-<stt_model>.bin.
        model_path = self._settings.stt_model_dir / f"ggml-{self._settings.stt_model}.bin"
        if not model_path.exists():
            raise RuntimeError(
                f"whisper model not found at {model_path} — download "
                f"ggml-{self._settings.stt_model}.bin into {self._settings.stt_model_dir}"
            )

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "clip.wav"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(clip.sample_rate)
                wav.writeframes(clip.samples)
            completed = subprocess.run(
                [binary, "-m", str(model_path), "-f", str(wav_path), "-nt"],
                capture_output=True,
                text=True,
                check=True,
            )
        return completed.stdout.strip()
