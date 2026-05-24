# Phase 1 — Walking skeleton (push-to-talk)

- **Status:** Not started
- **Milestone:** Phase 1
- **Objective:** A clunky-but-complete spoken conversation with Claude Code:
  push a key, speak, hear a spoken reply. Synchronous; no wake word, no
  streaming yet.

## In scope

- `jarvis.audio` — record from mic on a hotkey, play back audio.
- `jarvis.stt` — transcribe the recorded clip with whisper.cpp.
- `jarvis.brain` — run `claude -p --output-format json`, parse `.result` and
  `.session_id`, resume across turns, and **extract speakable text** (strip
  tool-use/tool-result blocks and fenced code).
- `jarvis.tts` — synthesize the reply with Kokoro and play it.
- A `jarvis run` command wiring these into a push-to-talk loop.

## Out of scope

- Wake word, VAD endpointing, token streaming, barge-in (Phases 2–3).

## Deliverables

- The four modules above + `jarvis run` (push-to-talk).
- Test fixtures: sample audio clips + canned `claude` JSON responses.

## Measurable goals

| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G1.1 | End-to-end reliability | ≥ 5 consecutive exchanges, no crash | manual session recorded in Outcomes |
| G1.2 | STT accuracy | Word error rate ≤ 10% on a 20-utterance dev set | `tests/test_stt_accuracy.py` |
| G1.3 | Speakable-text extraction | 100% of code/tool blocks stripped on fixtures | `tests/test_brain_extraction.py` |
| G1.4 | Session continuity | Turn 3 references turn 1 via `--resume` | `tests/test_brain_session.py` |
| G1.5 | Coverage | ≥ 80% | CI |

## Test plan (write first)

- `test_brain_extraction_strips_code_and_tools` — given a `claude` JSON payload
  containing fenced code and tool blocks, only prose is returned.
- `test_brain_resume_passes_session_id` — second call includes
  `--resume <id>` from the first call's response (subprocess mocked).
- `test_stt_accuracy_under_threshold` — WER over the dev-set fixtures ≤ 10%.
- `test_audio_roundtrip` — record→buffer→playback path works against a fake
  audio device.

## Definition of Done

All goals met; `jarvis run` holds a real spoken conversation; tests green;
coverage ≥ 80%; docs + CHANGELOG updated.

## Outcomes

_To be filled in as the phase completes._
