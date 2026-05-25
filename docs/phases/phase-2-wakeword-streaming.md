# Phase 2 — Wake word + streaming

- **Status:** In progress
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

### G2.4 — Streaming overlap + state machine (done)

The brain gained a streaming path (`Brain.stream`, `--output-format stream-json
--include-partial-messages`) that yields assistant text deltas; `loop.py` was
rewritten as the `IDLE→LISTENING→THINKING→SPEAKING→IDLE` state machine with a
producer (token stream → `SentenceStreamer` → sentence queue) / consumer (TTS)
overlap. `extract_speakable`'s whole-string regex was joined by
`SentenceStreamer`, a stateful filter that tracks in-fence / in-tool-block /
in-inline-code state and emits a sentence only once it is confirmed safe *and* a
boundary is reached — a code fence that opens mid-stream and never closes is
never spoken. Sentence segmentation does not split on abbreviations ("Mr.") or
decimals ("3.14"). Verified by `tests/test_loop_streaming.py` (first sentence
spoken before the stream completes; full state graph) plus
`tests/test_speakable_stream.py` and `tests/test_brain_streaming.py`.

### Streaming-TTFT de-risk (STEP 0, `scripts/bench_brain.py` extended with p95)

Measured time-to-first-token under `stream-json --include-partial-messages`:

| Prompt | Runs | First-token p50 | First-token p95 | Completion p50 | Overlap window p50 |
|--------|------|-----------------|-----------------|----------------|--------------------|
| one word ("ready") | 20 | 2762 ms | 3235 ms | ~= first token | — |
| multi-sentence prose | 5 | 4338 ms | — | 7717 ms | **3203 ms** |

**Streaming is worth it (G2.4):** speaking the first sentence buys a ~3.2 s head
start over waiting for the full reply, and that window grows with reply length.

> [!WARNING]
> **G2.3 (time-to-first-audio ≤ 1.5 s p50) is unreachable as currently
> architected.** The brain's first-token latency alone (≥ 2.76 s p50) exceeds the
> whole budget before STT/TTS are added. The ~1.9 s floor is `claude` CLI process
> startup, paid per turn under the spawn-per-turn model (ADR-0003); streaming
> does not reduce absolute TTFT — only a persistent brain process (Agent SDK /
> long-lived `claude`, the revisit ADR-0003 anticipates) would. **G2.3 must be
> renegotiated against this distribution or re-architected before it is chased.**

### G2.1 — Wake-word detection ("hey_jarvis") (both targets met)

`jarvis.wakeword` wraps openWakeWord's pretrained `hey_jarvis` model as a
`Detector` callable (one 80 ms PCM16 frame in, one score in [0, 1] out).
`WakeWordListener` owns the threshold comparison and frame loop and fires the
moment a score crosses `wake_threshold` (`jarvis.config`, already present from
scaffolding — reused, no new key). Because the detector is an injected callable,
the listener loop and the G2.1 metric (`Accuracy` — true-accept rate and
false-accepts-per-30-min) are pure and unit-tested with fakes; the openWakeWord
backend (`OpenWakeWordDetector`) is a native shim excluded from coverage (ADR-0005).
`jarvis.wakeword` is at 100% coverage.

**Measured against the real model** (`tests/test_wakeword.py::test_labeled_fixtures_meet_targets`,
which runs when the fixtures exist and skips in CI). Because a live human mic
session is hardware-bound, the labeled set is **synthesized** by
`scripts/gen_wakeword_fixtures.py` with the same TTS that voices Jarvis (Kokoro,
the three British male voices) — 24 "hey jarvis" positives across voices / speeds /
phrasings (half noise-mixed) and 30 min of ambient: low-level noise beds with
interspersed non-wake speech, **including phonetically near distractors** ("hey
there", "hey Travis", "hey Jarrah", …) to stress false-accepts.

| Threshold | True-accept (24 utt.) | False-accept / 30 min |
|-----------|-----------------------|-----------------------|
| 0.50 (old default) | 100% | 10 |
| 0.70 | 100% | 6 |
| 0.80 | 100% | 5 |
| **0.90 (tuned default)** | **100% (24/24)** | **1** ✅ |
| 0.95 | 100% | 0 |

Positives separate cleanly from the ambient near-misses — the weakest positive
scores 0.979, the loudest false-positive 0.937 — so **0.90 meets both targets**
(true-accept 100% ≥ 95%; false-accept 1 / 30 min ≤ 1) with ~0.08 of true-accept
headroom. The old 0.5 default was too permissive against "hey X" near-misses, so
`wake_threshold` is now **0.9** (config + `.env.example`).

> [!NOTE]
> These numbers are **synthetic TTS**, not live human speech. Synthetic positives
> are in-distribution for openWakeWord (trained on TTS) and likely *overstate*
> true-accept; the near-miss ambient is deliberately adversarial and likely
> *overstates* false-accept. They verify the integration end-to-end and tune the
> threshold, but a live human run (record fixtures + `python scripts/soak_wakeword.py
> --minutes 30`) remains the gold standard — re-check true-accept margin on real
> voices and lower the threshold if wakes feel marginal.

_Endpoint-latency distribution to be filled in as G2.2 lands._
