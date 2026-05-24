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

## Runtime state machine

The orchestrator (`jarvis.loop`) is a small state machine:

| State | Active components | Transition |
|-------|-------------------|------------|
| `IDLE` | wake word | wake word detected → `LISTENING` |
| `LISTENING` | mic capture + VAD | end-of-speech (silence ≥ `vad_silence_ms`) → `THINKING` |
| `THINKING` | STT then `claude -p` (streaming) | first speakable sentence ready → `SPEAKING` |
| `SPEAKING` | TTS playback (mic stays hot) | playback done → `IDLE`; user speaks → barge-in → `LISTENING` |

Streaming overlaps `THINKING` and `SPEAKING`: TTS begins on the first complete
sentence from Claude rather than waiting for the full response.

## Module map

These modules are introduced phase-by-phase (see [phases/](phases/)). Conventional
Commit scopes match these names.

| Module | Responsibility | Introduced |
|--------|----------------|-----------|
| `jarvis.config` | Twelve-factor settings (present today) | scaffolding |
| `jarvis.cli` | Command-line surface (present today) | scaffolding |
| `jarvis.audio` | Mic capture + playback ring buffers (`sounddevice`) | Phase 1 |
| `jarvis.stt` | whisper.cpp transcription | Phase 1 |
| `jarvis.brain` | `claude -p` subprocess, session resume, speakable-text extraction | Phase 1 |
| `jarvis.tts` | Kokoro synthesis (British male voice) | Phase 1 |
| `jarvis.wakeword` | openWakeWord "hey_jarvis" | Phase 2 |
| `jarvis.vad` | Silero VAD endpointing | Phase 2 |
| `jarvis.persona` | Voice-mode system prompt | Phase 3 |
| `jarvis.loop` | State-machine orchestrator + barge-in | Phase 2–3 |

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

Target **time-to-first-audio ≤ 1.5 s p50** from end-of-speech. The dominant
sinks are endpointing and Claude's time-to-first-token; STT and TTS are cheap if
streamed.

| Stage | Budget (p50) |
|-------|-------------|
| VAD endpoint decision | 150–300 ms |
| STT (whisper.cpp turbo, short utterance) | 200–500 ms |
| Claude time-to-first-token | 300–800 ms |
| TTS first audio chunk (Kokoro) | 100–300 ms |

The single highest-impact rule: **stream at every stage** so total latency
trends toward `max(stages)` rather than `sum(stages)`.
