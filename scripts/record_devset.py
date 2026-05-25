"""Guided recorder for the G1.2 STT dev set.

Walks `tests/fixtures/stt/devset.json`, and for each utterance still missing a
`hypothesis` it speaks the reference line aloud (via macOS `say`), beeps, then
records a fixed window (no keyboard input — works when launched non-interactively
through `! uv run …`), transcribes it with whisper.cpp, and writes the
transcription back into the manifest. The manifest is saved after *every*
utterance, so the run is crash-safe and resumable — re-running skips entries
already filled. When every entry has a hypothesis,
`tests/test_stt_accuracy.py::test_devset_wer_under_threshold` stops skipping and
asserts mean WER <= 10% (G1.2).

Run it once the voice stack is installed (`jarvis doctor` green). Each window is
JARVIS_RECORD_SECONDS (default 5s):

    uv run python scripts/record_devset.py
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from jarvis.audio import Clip

#: Inject-ables so the walk logic is testable without a mic or whisper.cpp.
Capture = Callable[[], Clip]
Transcribe = Callable[[Clip], str]
Entries = list[dict[str, object]]

DEVSET = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "stt" / "devset.json"


def record_devset(
    entries: Entries,
    capture: Capture,
    transcribe: Transcribe,
    prompt: Callable[[str], None] = lambda _ref: None,
    persist: Callable[[Entries], None] | None = None,
) -> Entries:
    """Return a copy of ``entries`` with missing ``hypothesis`` fields filled.

    Entries that already have a truthy hypothesis are passed through untouched
    (and their audio is not captured). Input entries are not mutated. If
    ``persist`` is given it is called with the full manifest after each newly
    recorded utterance, so a partial run is saved and can be resumed.
    """
    result: Entries = [dict(entry) for entry in entries]
    for entry in result:
        if entry.get("hypothesis"):
            continue
        prompt(str(entry["reference"]))
        entry["hypothesis"] = transcribe(capture())
        if persist is not None:
            persist(result)
    return result


#: How long to record each utterance (seconds). Override with JARVIS_RECORD_SECONDS.
RECORD_SECONDS = 5.0


def main() -> None:  # pragma: no cover - drives a real microphone + whisper.cpp
    import os
    import shutil
    import subprocess
    import time

    from jarvis.audio import make_sounddevice_source, record
    from jarvis.config import get_settings
    from jarvis.stt import WhisperCppTranscriber, word_error_rate

    settings = get_settings()
    seconds = float(os.environ.get("JARVIS_RECORD_SECONDS", RECORD_SECONDS))
    transcribe = WhisperCppTranscriber(settings)
    say = shutil.which("say")

    entries: Entries = json.loads(DEVSET.read_text())
    total = len(entries)
    remaining = sum(1 for e in entries if not e.get("hypothesis"))
    print(
        f"{total} utterances; {remaining} left to record. Each line is spoken aloud — "
        f"after the beep, repeat it (you have {seconds:.0f}s).",
        flush=True,
    )

    def _announce(reference: str) -> None:
        idx = next(i for i, e in enumerate(entries, 1) if e["reference"] == reference)
        print(f"\n[{idx}/{total}] Say:  {reference!r}", flush=True)
        if say is not None:
            # Speak the line so you know what to read even if stdout is buffered.
            subprocess.run([say, f"Number {idx}. Repeat after the beep: {reference}"], check=False)

    def _capture() -> Clip:
        if say is not None:
            subprocess.run([say, "[[volm 0.4]] beep"], check=False)
        source = make_sounddevice_source(settings.sample_rate)
        deadline = time.monotonic() + seconds
        clip = record(
            source, stop=lambda: time.monotonic() >= deadline, sample_rate=settings.sample_rate
        )
        print("  recorded.", flush=True)
        return clip

    def _persist(result: Entries) -> None:
        DEVSET.write_text(json.dumps(result, indent=2) + "\n")

    updated = record_devset(
        entries, capture=_capture, transcribe=transcribe, prompt=_announce, persist=_persist
    )

    mean = sum(word_error_rate(str(e["reference"]), str(e["hypothesis"])) for e in updated) / total
    print(f"\nSaved {total} hypotheses to {DEVSET}.", flush=True)
    print(f"Mean WER: {mean:.3f}  ({'PASS' if mean <= 0.10 else 'FAIL'} vs 10% target)", flush=True)


if __name__ == "__main__":  # pragma: no cover
    main()
