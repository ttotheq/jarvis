# Phase 1 — Walking skeleton (push-to-talk)

- **Status:** Engineering complete and the voice stack is installed — all four
  modules (`jarvis.audio`, `jarvis.stt`, `jarvis.brain`, `jarvis.tts`), the
  `jarvis.loop` orchestrator, and `jarvis run` are implemented with tests and
  `make check` green (98.8% coverage); `jarvis doctor` now exits 0 (PortAudio,
  whisper.cpp, openWakeWord, Kokoro all present). The TTS and STT paths are
  validated live (Kokoro synthesis → whisper.cpp transcription round-trips at
  WER 0.0). What remains is human-in-the-loop: G1.1 (a live ≥5-exchange spoken
  session), G1.2 (recording the 20-utterance human dev set and scoring it), and
  the G0.3 voice pick (samples generated in `samples/voice-audition/`).
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
| G1.1 | **Cascade verified live; human-mic session pending** | `tests/test_loop.py` runs 5 exchanges with fakes. A synthetic end-to-end dry run (Kokoro "voice" → real whisper.cpp → real `claude -p` → real Kokoro → sounddevice playback) ran 5 consecutive turns with no crash, including live `--resume` (turn 3 recalled the fact from turn 1). Only a real person at the mic remains. |
| G1.2 | **Met** | 20 human utterances recorded and transcribed by whisper.cpp large-v3-turbo; **mean WER 0.073 (7.3%)**, under the 10% target. `tests/test_stt_accuracy.py::test_devset_wer_under_threshold` now asserts (no longer skips). |
| G1.3 | **Met** | `tests/test_brain_extraction.py` — code/tool blocks 100% stripped on the fixture. |
| G1.4 | **Met** | `tests/test_brain_session.py` — turns 2+ pass `--resume <session_id>` from turn 1. |
| G1.5 | **Met** | Coverage 98.8% (≥ 80%). |

### Setup completed (SETUP STEP)

The voice stack is installed on this Mac: `brew install portaudio whisper-cpp
espeak-ng`, the `voice` extra wheels (`kokoro`, `numpy`, `openwakeword`,
`sounddevice`, `soundfile`), and the `ggml-large-v3-turbo.bin` model in
`~/.cache/jarvis/whisper`. `jarvis doctor` exits 0.

### Live validation (synthetic end-to-end)

The full real cascade was exercised with only the microphone replaced (Kokoro
synthesizes the "spoken" question): real whisper.cpp → real `claude -p` → real
Kokoro reply → sounddevice playback. Five consecutive turns ran with no crash,
and live session continuity held — turn 3 ("what did I say my favorite color
was?") answered "Your favorite color is blue.", recalling the fact stated in
turn 1 via `--resume`.

### Remaining (human-in-the-loop)

- **G0.3** — done (`bm_george` confirmed by audition; see Phase 0 Outcomes).
- **G1.1** — cascade proven above; the final sign-off is a person running
  `uv run jarvis run` and holding a real ≥5-exchange spoken session at the mic.
- **G1.2** — record the 20 utterances named in `tests/fixtures/stt/devset.json`,
  transcribe each with whisper.cpp, and fill the `hypothesis` fields; the test
  then asserts mean WER ≤ 10%.

### Assumptions made

- **Push-to-talk = stdin Enter-to-start / Enter-to-stop.** True global hotkey
  capture needs an extra dependency; the skeleton uses a dependency-free,
  injectable gate. Revisitable.
- The native voice wheels are pinned into the optional `voice` extra (install
  with `uv sync --extra voice`); they pull torch via Kokoro, so they stay out of
  the default sync and backends lazy-import — the core package and CI stay light.
