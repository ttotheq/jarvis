# ADR-0002: Local-first voice stack

- **Status:** Accepted
- **Date:** 2026-05-24

## Context

The voice cascade (wake word, VAD, STT, TTS) can run locally on Apple Silicon or
via cloud APIs. Cloud options (Deepgram, ElevenLabs) are marginally lower-latency
and higher-fidelity; local options (whisper.cpp, Kokoro, Silero, openWakeWord)
are free, fully private, and have no per-minute cost. The brain (Claude Code) is
inherently a network call regardless.

## Decision

Run the **entire voice stack on-device**:

- Wake word: **openWakeWord** (ships a pretrained `hey_jarvis` model; fully open,
  no API key, no training required).
- Endpointing: **Silero VAD**.
- STT: **whisper.cpp** (`large-v3-turbo`, Core ML/Metal accelerated).
- TTS: **Kokoro** with a British male voice.

The only data leaving the machine is the final text prompt to Anthropic's API,
which is exactly what using Claude Code already entails — no new privacy surface.

## Consequences

- Zero marginal cost and maximal privacy; no API keys for the voice path.
- Slightly higher latency and a notch lower voice realism than top cloud
  services — accepted, mitigated by streaming every stage.
- First-run setup includes downloading models and a whisper.cpp Core ML
  conversion step (a known Phase 0 risk).
- The cascade is modular, so any single stage can later be swapped for a cloud
  provider behind the same interface without touching the rest.
