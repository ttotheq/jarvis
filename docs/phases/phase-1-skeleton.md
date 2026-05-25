# Phase 1 — Walking skeleton (push-to-talk)

- **Status:** Engineering complete — all four modules (`jarvis.audio`,
  `jarvis.stt`, `jarvis.brain`, `jarvis.tts`), the `jarvis.loop` orchestrator,
  and `jarvis run` are implemented with tests and `make check` green (98.8%
  coverage). The hardware-dependent goals — G1.1 (live ≥5-exchange session),
  G1.2 (WER over recorded utterances), and the deferred G0.3 voice audition —
  remain pending the native voice-stack install on this machine (`jarvis doctor`
  still reports all four components missing).
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

### Modules (all behind injected interfaces, tested with fakes per ADR-0005)

- **`jarvis.brain`** — `Brain.ask()` runs `claude -p <prompt> --output-format
  json --permission-mode <mode>`, parses `.result`/`.session_id`, and appends
  `--resume <session_id>` on every turn after the first. The subprocess is an
  injected `Runner`, so argv assembly and session handling are tested without
  spawning `claude`. `extract_speakable()` strips fenced code, tool-use, and
  tool-result blocks and unwraps inline code spans.
- **`jarvis.audio`** — `Clip` (PCM16 bytes + rate) and a device-agnostic
  `record()` capture loop over an injected `FrameSource`; `sounddevice` mic
  source and `Speaker` are native shims excluded from coverage.
- **`jarvis.stt`** — `word_error_rate()` (case/punctuation-insensitive,
  word-level Levenshtein) plus a whisper.cpp CLI transcriber shim.
- **`jarvis.tts`** — `speak()` dispatch (skips blank replies) plus a Kokoro
  synthesizer shim.
- **`jarvis.loop` + `jarvis run`** — `VoiceLoop` wires capture → STT → brain →
  TTS per turn; `converse()` drives consecutive turns. `jarvis run` is the
  push-to-talk CLI (Enter to start, Enter to stop); its hardware wiring is the
  manual end-to-end path.

### Goal status

| ID | Status | Evidence |
|----|--------|----------|
| G1.1 | **Logic verified; live pending** | `tests/test_loop.py` runs 5 consecutive exchanges with fakes, no crash. Live recorded session blocked on native install. |
| G1.2 | **Metric verified; live pending** | `word_error_rate()` covered by `tests/test_stt_accuracy.py`. The 20-utterance dev-set assertion skips until recordings exist (needs whisper.cpp). |
| G1.3 | **Met** | `tests/test_brain_extraction.py` — code/tool blocks 100% stripped on the fixture. |
| G1.4 | **Met** | `tests/test_brain_session.py` — turns 2+ pass `--resume <session_id>` from turn 1. |
| G1.5 | **Met** | Coverage 98.8% (≥ 80%). |

### Pending the voice-stack install (SETUP STEP)

`jarvis doctor` reports PortAudio, whisper.cpp, openWakeWord, and Kokoro all
missing. Once installed, the remaining work is: record the 20-utterance dev set
and run real whisper.cpp to populate `tests/fixtures/stt/devset.json` (G1.2);
hold a real ≥5-exchange spoken session and record it here (G1.1); audition
`bm_george`/`bm_lewis`/`bm_fable` and confirm the default (G0.3).

### Assumptions made

- **Push-to-talk = stdin Enter-to-start / Enter-to-stop.** True global hotkey
  capture needs an extra dependency; the skeleton uses a dependency-free,
  injectable gate. Revisitable.
- The native voice wheels are **not** yet pinned into the `voice` extra (Kokoro
  pulls torch — an install-size decision left to the operator); backends
  lazy-import so the core package and CI stay light.
