# Phase 3 — Jarvis feel

- **Status:** In progress — G3.1 (barge-in) and G3.2 (persona) done; G3.3 remains
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

### G3.2 — Voice persona (spoken conciseness, no code aloud) · _Done_

New `jarvis.persona` owns the voice-mode system prompt (`VOICE_SYSTEM_PROMPT`,
the speakable-output contract from `docs/voice-persona.md`) and the **pure** G3.2
metric. The prompt is injected into `Brain._base_argv` as
`--append-system-prompt`, so it rides on **both** call shapes — `ask()` and the
`stream()` path the loop actually uses (asserted flag-relative in
`tests/test_persona_eval.py`).

The split between what CI can prove and what only a live run can:

- **CI proves the metric and the wiring, not obedience.** `evaluate_persona`
  reduces each reply with `extract_speakable` first, then measures the fraction
  within the 50-word cap and the count still carrying a code fence (a *surviving*
  fence = code that would reach TTS). It is 100%-covered and exercised over a
  committed exemplar set plus crafted pass/fail cases. CI also asserts both argv
  carry the flag. CI cannot prove Claude *obeys* the prompt — the recorded live
  set is gitignored, so `test_persona_eval_skips_without_recorded_set` skips.
- **The live eval is the judge.** `scripts/eval_persona.py` runs 20 **neutral,
  factual** throwaway prompts (no vault, no tools) through
  `claude -p --append-system-prompt <persona> --output-format json`, a fresh
  session each (no `--resume`, `--permission-mode default`), and feeds the real
  replies to the same metric.

**Live measurement (20 neutral prompts, recorded 2026-05-25):**

| Metric | Target | Measured |
|--------|--------|----------|
| Replies ≤ 50 words (on speakable text) | ≥ 90% | **100% (20/20)** |
| Replies leaking code to TTS | 0 | **0** |
| G3.2 verdict | PASS | **PASS** |

Replies were concise and in-register — e.g. *"Paris, sir."*; *"Jupiter, sir — by
a comfortable margin, more massive than all the other planets combined."* The
distribution ran well inside the cap (most replies 2–15 words), so the ≥ 90%
target had ample headroom on factual prompts. Re-run with
`uv run python scripts/eval_persona.py --record`.

**Caveats / scope.** The eval prompts are factual one-liners; they exercise
conciseness and the no-code rule but not the harder advisor behaviours
(volunteering a risk, pushing back, confirming a destructive action) — those are
in the prompt but not in this metric, and genuine *anticipation* is explicitly
deferred past Phase 3 (`docs/voice-persona.md`). The no-code guard has a real
asymmetry the metric makes conservative: `extract_speakable` strips *paired*
fences but an *unclosed* fence survives whole-string extraction (the streaming
`SentenceStreamer` withholds it instead), so the metric flags a surviving fence
as leaked — the stricter, correct call for the whole-string path.
