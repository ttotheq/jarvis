# Phase 3 — Jarvis feel

- **Status:** Not started
- **Milestone:** Phase 3
- **Objective:** Turn a working voice loop into something that feels like Jarvis:
  you can interrupt him, he's concise and in-character, and he asks before doing
  anything dangerous.

## In scope

- **Barge-in** — the mic stays hot during `SPEAKING`; user speech (or "Jarvis,
  stop") cancels TTS playback and any in-flight `claude` task, returning to
  `LISTENING`.
- `jarvis.persona` — the voice-mode system prompt injected via
  `--append-system-prompt` (see `docs/voice-persona.md`): concise, British
  butler register, never reads code aloud.
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

_To be filled in as the phase completes._
