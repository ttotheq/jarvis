"""Guided recorder for the G1.2 STT dev set.

Walks `tests/fixtures/stt/devset.json`, and for each utterance still missing a
`hypothesis` it prompts you to read the reference line aloud (push-to-talk),
records it, transcribes it with whisper.cpp, and writes the transcription back
into the manifest. The manifest is saved after *every* utterance, so the run is
crash-safe and resumable — re-running skips entries already filled. When every
entry has a hypothesis, `tests/test_stt_accuracy.py::test_devset_wer_under_threshold`
stops skipping and asserts mean WER <= 10% (G1.2).

Run it once the voice stack is installed (`jarvis doctor` green):

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


def main() -> None:  # pragma: no cover - drives a real microphone + whisper.cpp
    from jarvis.cli import _push_to_talk_record_turn  # local import: hardware glue
    from jarvis.config import get_settings
    from jarvis.stt import WhisperCppTranscriber, word_error_rate

    settings = get_settings()
    capture = _push_to_talk_record_turn(settings.sample_rate)
    transcribe = WhisperCppTranscriber(settings)

    entries: Entries = json.loads(DEVSET.read_text())
    total = len(entries)
    remaining = sum(1 for e in entries if not e.get("hypothesis"))
    print(f"{total} utterances; {remaining} left to record. Read each line as shown.")

    def _prompt(reference: str) -> None:
        idx = next(i for i, e in enumerate(entries, 1) if e["reference"] == reference)
        print(f"\n[{idx}/{total}] Read aloud:  {reference!r}")

    def _persist(result: Entries) -> None:
        DEVSET.write_text(json.dumps(result, indent=2) + "\n")

    updated = record_devset(
        entries, capture=capture, transcribe=transcribe, prompt=_prompt, persist=_persist
    )

    mean = sum(word_error_rate(str(e["reference"]), str(e["hypothesis"])) for e in updated) / total
    print(f"\nSaved {total} hypotheses to {DEVSET}.")
    print(f"Mean WER: {mean:.3f}  ({'PASS' if mean <= 0.10 else 'FAIL'} vs 10% target)")


if __name__ == "__main__":  # pragma: no cover
    main()
