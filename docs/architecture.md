# Architecture

## The core idea

Jarvis does **not** reimplement Claude Code. It is a local voice cascade that
uses the real `claude` CLI as its reasoning engine. This is deliberately a
**turn-based cascade**, not a speech-to-speech model — speech-to-speech APIs
(OpenAI Realtime, Gemini Live) bundle their own LLM brain and would compete with
Claude Code rather than serve it.

```
                                   barge-in (interrupt) ─────────────┐
                                                                     │
 mic ─▶ Wake word ─▶ VAD/endpoint ─▶ STT ─▶ Claude (headless) ─▶ TTS ─▶ speakers
        openWakeWord  Silero VAD     whisper  claude -p           Kokoro
        "hey_jarvis"                 .cpp     stream-json
```

Only the final **text prompt** leaves the machine (to Anthropic's API, via the
`claude` CLI). All audio, wake-word detection, transcription, and synthesis are
on-device. See [ADR-0002](adr/0002-local-first-voice-stack.md).

The always-on path above is now the **default runtime**. As of Phase 4,
`jarvis run` defaults to `run_mode=wake_word`: it parks at `IDLE` until
"hey jarvis", endpoints the utterance with Silero VAD, replies, and returns to
`IDLE` — no keyboard, so it runs headless. `push_to_talk` (Enter-gated) and
`timed` (fixed window) remain as developer-harness modes via `JARVIS_RUN_MODE`.
Phase 4's G4.1 added the service this plugs into: `jarvis service install`
registers a macOS launchd LaunchAgent that starts `jarvis run` at login and
restarts it on crash (see [ADR-0006](adr/0006-launchd-launchagent-service.md)).

## Runtime state machine

The always-on orchestrator (`jarvis.loop` plus wake-word / VAD wiring) is a small
state machine:

| State | Active components | Transition |
|-------|-------------------|------------|
| `IDLE` | wake word | wake word detected → `LISTENING` |
| `LISTENING` | mic capture + VAD | end-of-speech (silence ≥ `vad_silence_ms`) → `THINKING` |
| `THINKING` | STT then `claude -p` (streaming) | first speakable sentence ready → `SPEAKING` |
| `SPEAKING` | TTS playback (mic stays hot) | playback done → `IDLE`; wake phrase detected → barge-in → `LISTENING` |

Streaming overlaps `THINKING` and `SPEAKING`: TTS begins on the first complete
sentence from Claude rather than waiting for the full response.

During `SPEAKING` the mic stays hot. The live watcher now reuses
`jarvis.wakeword`: a persistent input stream stays open across LISTENING capture
and SPEAKING, frames are resampled to openWakeWord's 16 kHz geometry when the
configured input rate differs, and only `"hey jarvis"` aborts playback
(`Speaker.stop()`), cancels the in-flight `claude` stream (closing the token
generator terminates the child), and returns to `LISTENING`. Setting
`JARVIS_LOG_LEVEL=DEBUG` logs per-frame RMS + wake score + whether a read failed
during `SPEAKING`, which is the live proof surface for the G4.0 stream fix.

## Module map

These modules are introduced phase-by-phase (see [phases/](phases/)). Conventional
Commit scopes match these names.

| Module | Responsibility | Introduced |
|--------|----------------|-----------|
| `jarvis.config` | Twelve-factor settings (present today) | scaffolding |
| `jarvis.cli` | Command-line surface (present today) | scaffolding |
| `jarvis.audio` | Mic capture + playback (`sounddevice`); persistent shared mic + PCM16 resampling for G4.0 live barge-in | Phase 1 |
| `jarvis.stt` | whisper.cpp transcription | Phase 1 |
| `jarvis.brain` | `claude -p` subprocess, session resume, speakable-text extraction | Phase 1 |
| `jarvis.tts` | Kokoro synthesis (British male voice) | Phase 1 |
| `jarvis.wakeword` | openWakeWord "hey_jarvis" detector primitive; reused for G4.0 wake-phrase barge-in | Phase 2 |
| `jarvis.vad` | Silero VAD endpointing (`Endpointer`) + the retained raw-speech onset primitive (`OnsetDetector`) | Phase 2 |
| `jarvis.persona` | Voice-mode system prompt (`--append-system-prompt`) + the pure G3.2 conciseness/no-code metric | Phase 3 |
| `jarvis.loop` | Turn orchestrator + the always-on entry point: wake-gated `IDLE` (`wait_for_wake_phrase`) → VAD-endpointed `LISTENING` capture (`capture_until_endpoint`) → streaming `THINKING`/`SPEAKING` with wake-phrase barge-in | Phase 1 |
| `jarvis.service` | macOS launchd LaunchAgent lifecycle — config-driven plist generation + `install`/`uninstall`/`status` (`jarvis service …`) | Phase 4 |

## The brain: driving Claude Code

The brain shells out to the headless CLI and keeps one session across turns:

```bash
claude -p "<transcript>" \
  --output-format stream-json --include-partial-messages \
  --resume <session_id> \
  --permission-mode acceptEdits \
  --append-system-prompt "<voice persona>"
```

- `stream-json` yields token deltas so TTS can start early.
- `--resume <session_id>` carries conversation context turn to turn; the first
  call returns the id (`--output-format json` → `.session_id`).
- Only natural-language assistant text is sent to TTS — tool calls, tool
  results, and fenced code blocks are filtered out (`jarvis.brain`).

See [ADR-0003](adr/0003-drive-claude-code-via-headless-mode.md).

## Latency budget (fully local, Apple Silicon)

Phase 2 measured the current spawn-per-turn `claude -p` path and renegotiated the
time-to-first-audio target accordingly. The current acceptance bar is
**≤ 6.5 s p50 / ≤ 8.0 s p95** from end-of-speech to first audio, matching the
measured **6.07 s p50 / 7.75 s p95** distribution on Apple Silicon. The standing
forward target is **≤ 2.0 s p50**, but that requires a persistent-brain
re-architecture (ADR-0003 revisit); the headless CLI startup cost dominates the
current runtime.

| Stage | Current measured / attributed p50 |
|-------|----------------------------------|
| VAD silence hangover | 700 ms |
| STT (whisper.cpp `large-v3-turbo`) | ~1.1 s |
| Claude time-to-first-token (`claude -p`) | 2.76 s isolated; dominant and variable |
| TTS first audio chunk (Kokoro) | ~180 ms |

Streaming still matters: it shortens **total turn time** by letting sentence one
play while later text generates. It does **not** reduce time-to-first-audio on the
current architecture, because STT → first Claude token → first TTS chunk is still a
strictly sequential path.
