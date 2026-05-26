# Phase 3 — Jarvis feel

- **Status:** Goals met — G3.1 (barge-in), G3.2 (persona), G3.3 (permission
  gating) done; coverage 99% (G3.4). Remaining: the live end-to-end demo recording
  (Definition of Done) — the unit contracts are proven; the audio path is wired
  manually.
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

### G3.3 — Spoken permission gating · _Done (metric); live wiring manual_

New `jarvis.permissions` is a Claude Code `PreToolUse` hook: before a tool call
runs, it classifies the call and — for destructive ones — routes a spoken yes/no
confirmation, emitting the Claude Code `permissionDecision` (`allow`/`deny`). This
makes the persona line *"confirm destructive or irreversible actions verbally
before executing them"* mechanical rather than merely willed.

Why the hook is necessary at all: the brain runs `--permission-mode acceptEdits`
(ADR-0003), and the headless `claude -p` child has no human at a keyboard to
approve a Bash call. Without the hook a destructive `rm -rf` or `git push` would
run unattended. The hook is a **separate process** the `claude` child spawns — it
cannot call the running loop's Speaker/STT in memory, which is the central design
constraint (same cross-process split that shaped G3.2's metric-vs-live-eval).

The split between what CI proves and what only a live run can:

- **CI proves the pure classifier and the decision emission.** `is_destructive`
  inspects a Bash command — splitting compound chains on `&&`/`||`/`;`/`|`, tokenising
  each sub-command, stripping `sudo`/env/wrappers to the real program — and flags an
  explicit set of irreversible verbs: `rm`/`rmdir`/`shred`/`dd`/`mkfs`/`truncate`,
  process/power control (`kill`/`shutdown`/…), `git push`/`git clean`/`git reset
  --hard`/`git branch -D`/`git checkout --force`, and any privilege escalation.
  `decide(payload, confirm)` calls the injected `confirm` seam **before** forming a
  verdict (affirmative → `allow`, anything else → `deny`); non-destructive calls are
  `allow`ed without ever consulting `confirm`. `main` parses the `PreToolUse` JSON
  from stdin and writes the decision JSON to stdout. All of this is 100%-covered with
  a fake `confirm` — nothing in CI speaks, records, or spawns `claude`.
- **The live audio wiring is the integration step.** `build_live_confirm` reuses the
  cascade's own components — Kokoro speaks the question, the sounddevice mic +
  whisper.cpp capture the answer, `interpret_confirmation` maps it to a go-ahead. It
  is hardware-bound (excluded from coverage, ADR-0005) and exercised manually.

**Three deliberate safety biases:**

1. *Deny on doubt.* `interpret_confirmation` defaults to **No**: a negative word
   anywhere wins ("yes — actually no, stop" → deny), an affirmative otherwise
   allows, and silence/ambiguity/empty deny. The gate never auto-runs on
   uncertainty.
2. *Speak intent, not the command.* The spoken question is `"You're about to
   {delete files / push to the remote / discard uncommitted changes}, sir — shall
   I?"` — no command, flags, or paths read aloud (the persona's no-code-aloud rule
   applies to the confirmation too).
3. *Never gate read-only.* `ls`, `cat`, `git status`, `git log`, a non-`--hard`
   `git reset`, and all non-Bash tools pass through silently — a confirmation
   prompt on every benign command would wreck the voice UX.

**The metric (G3.3): 100% of the destructive set gates before running, 0 auto-run**
— proven in `tests/test_permission_gate.py` over the `rm -rf` / `git push` / `git
reset --hard` cases (plus `git clean`, `git branch -D`, `sudo`, `dd`, compound
chains), with the four required write-first tests green: classifier flags
destructive vs. read-only; hook blocks until confirmed (`confirm`→False emits deny,
→True allow, `confirm` called first); hook passes safe through without asking; hook
reads stdin and emits the `permissionDecision` JSON.

**Integration (manual).** Register the hook in the `settings.json` the spawned
`claude` reads (project `.claude/settings.json` or user settings), scoped to the
Bash matcher so read-only tools never reach it:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "uv run python -m jarvis.permissions" }
        ]
      }
    ]
  }
}
```

With `confirm` unset, `main` builds the live audio `confirm`, so the spawned hook
speaks the question and hears the answer. This registration plus the end-to-end
demo recording (Definition of Done) is the remaining manual step.

**Caveats / scope.** Classification is an explicit destructive-verb allow-list, not
a sandbox: a novel destructive command outside the set (e.g. an obscure CLI, an
overwriting `>` redirection, `mv` over an existing file) is **not** caught — it runs
exactly as it would without the gate. The set covers the irreversible verbs that
matter in practice and errs toward asking (any `sudo`/`doas` gates), but it is a
guardrail against the common cases, not a security boundary. A `git push` is gated
as "destructive" though it is recoverable; that conservative call is intentional.
The yes/no interpreter is keyword-based, so a creatively phrased answer may be read
as ambiguous and (safely) denied — the user simply repeats.
