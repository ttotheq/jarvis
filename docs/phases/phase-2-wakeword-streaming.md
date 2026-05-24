# Phase 2 — Wake word + streaming

- **Status:** Not started
- **Milestone:** Phase 2
- **Objective:** Replace push-to-talk with always-on "Hey Jarvis", and make it
  feel responsive by streaming every stage. This is where latency becomes a
  first-class, measured concern.

## In scope

- `jarvis.wakeword` — openWakeWord `hey_jarvis`, running on a rolling buffer.
- `jarvis.vad` — Silero VAD endpointing (decide when the user stopped talking).
- `jarvis.loop` — the `IDLE→LISTENING→THINKING→SPEAKING` state machine.
- Switch the brain to `--output-format stream-json --include-partial-messages`
  and begin TTS on the first complete sentence (overlap THINKING and SPEAKING).
- `scripts/bench_latency.py` — measures endpoint latency and time-to-first-audio.

## Out of scope

- Barge-in, persona, permission gating (Phase 3).

## Measurable goals

| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G2.1 | Wake-word detection | True-accept ≥ 95% / 20 utterances; false-accept ≤ 1 per 30 min ambient | `tests/test_wakeword.py` + soak |
| G2.2 | Endpoint latency | End-of-speech → STT start ≤ 300 ms p50 | `scripts/bench_latency.py` |
| G2.3 | Time-to-first-audio | ≤ 1.5 s p50, ≤ 2.5 s p95 | `scripts/bench_latency.py` over 20 runs |
| G2.4 | Streaming overlap | First sentence spoken before full response completes | `tests/test_loop_streaming.py` |
| G2.5 | Coverage | ≥ 80% | CI |

## Test plan (write first)

- `test_wakeword_fires_on_positive_clip` / `test_wakeword_silent_on_negative` —
  detection against labeled audio fixtures.
- `test_vad_endpoints_after_silence` — given an audio stream with a trailing
  silence ≥ `vad_silence_ms`, the endpoint fires once.
- `test_loop_speaks_first_sentence_before_completion` — fed a streamed token
  sequence, TTS is invoked on the first sentence boundary, not at the end.
- `test_state_transitions` — the state machine follows the documented graph.

## Definition of Done

All goals met; latency benchmark recorded in Outcomes; "Hey Jarvis" works hands
-free; tests green; coverage ≥ 80%.

## Outcomes

_To be filled in as the phase completes (latency distribution, false-accept rate)._
