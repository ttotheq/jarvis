# Phase 3 — Jarvis feel

- **Status:** In progress — G3.1 (barge-in) done; G3.2/G3.3 remain
- **Milestone:** Phase 3
- **Objective:** Turn a working voice loop into something that feels like Jarvis:
  you can interrupt him, he's concise and in-character, and he asks before doing
  anything dangerous.

## In scope

- **Barge-in** — the mic stays hot during `SPEAKING`; user speech (or "Jarvis,
  stop") cancels TTS playback and any in-flight `claude` task, returning to
  `LISTENING`.
- `jarvis.persona` — the voice-mode system prompt injected via
  `--append-system-prompt` (see `docs/voice-persona.md`): concise, in an advisor
  register (Tony Stark's J.A.R.V.I.S., not a butler), proactive with the
  decision-relevant point, never reads code aloud.
- **Spoken permission gating** — a Claude Code `PreToolUse` hook routes risky
  tool calls (e.g. `rm`, `git push`, destructive bash) to a spoken confirmation
  before they execute.

## Out of scope

- Background daemon, release (Phase 4).

## Measurable goals

| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G3.1 | Barge-in latency | Playback stops ≤ 300 ms after speech onset; in-flight task cancelled | `tests/test_barge_in.py` |
| G3.2 | Spoken conciseness | ≥ 90% of replies ≤ 50 words on a 20-prompt eval; 0 code read aloud | `tests/test_persona_eval.py` |
| G3.3 | Permission gating | 100% of destructive tool calls trigger spoken confirmation before running | `tests/test_permission_gate.py` |
| G3.4 | Coverage | ≥ 85% | CI |

## Test plan (write first)

- `test_barge_in_cancels_playback` — a speech-onset event during SPEAKING sets
  the cancel flag and halts the (fake) player within budget.
- `test_persona_responses_are_concise` — over canned prompt/response fixtures,
  ≥ 90% are ≤ 50 words and none contain fenced code.
- `test_permission_hook_blocks_destructive` — a `PreToolUse` payload for `rm -rf`
  is gated (confirmation requested) rather than auto-approved.

## Definition of Done

All goals met; coverage ≥ 85%; a recorded session demonstrates interrupt +
in-character replies + a spoken confirmation; docs + CHANGELOG updated.

## Outcomes

### G3.1 — Barge-in (cancellable SPEAKING) · _Done_

The `SPEAKING` state is now cancellable. Three concurrent edges cooperate, all
injected so the contract is unit-tested without a mic or a live `claude`
(`tests/test_barge_in.py`, `tests/test_vad.py`):

- **`Speaker` grows `stop()`** (real impl: `sd.stop()`), so the consumer can abort
  a clip *mid-utterance* instead of waiting out the sentence. This is the seam
  that makes the 300 ms budget reachable — barge-in latency is bounded by `stop()`,
  not by how long the current sentence happens to be.
- **`jarvis.vad.OnsetDetector`** is the rising-edge counterpart to the G2.2
  `Endpointer`: it reuses the same injected Silero `Detector` seam but fires on the
  *first* frame at/above `vad_threshold` (and latches, so one utterance = one
  onset). The live watcher (`loop.build_default_barge_in_watcher`) reads 512-sample
  frames off the hot mic and is wired into `jarvis run`.
- **A cancel `threading.Event`** set by the onset watcher. On onset the watcher
  marks cancel, then aborts the in-flight clip; the consumer stops, the producer
  breaks its token loop and **closes the generator** — in `Brain.stream` that
  `GeneratorExit` terminates the `claude` child, so no further sentences are
  spoken. The machine then transitions to `LISTENING` (the user is talking), not
  `IDLE`.

The latency is read off an injected clock (onset → playback-halted), proven
≤ 300 ms in `test_barge_in_latency_within_budget`; the cancellation contract
(no later sentence spoken, stream torn down, returns to LISTENING) is proven in
the other three write-first tests. Coverage floor raised to **85%** (G3.4); the
suite sits at **99%** with `jarvis.loop` and `jarvis.vad` at 100%.

**Known limitation (out of scope for G3.1):** the mic is hot while Kokoro plays,
so Jarvis can in principle hear *himself* and self-trigger barge-in. There is no
acoustic echo cancellation yet; onset reuses `vad_threshold` (0.5). On real
hardware the practical mitigations are a higher onset threshold, output ducking,
or AEC — to be evaluated when the live loop is exercised end-to-end. The unit
budget (onset → `stop()`) is pure compute and trivially within 300 ms; the live
floor is one Silero frame (~32 ms) plus `sd.stop()`, measured manually.
