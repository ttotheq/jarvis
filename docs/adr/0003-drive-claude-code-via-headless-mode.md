# ADR-0003: Drive Claude Code via headless mode

- **Status:** Accepted, superseded by
  [ADR-0007](0007-rebuild-brain-on-claude-agent-sdk.md) (2026-05-27) —
  implementation deferred to after 2026-06-15. Until ADR-0007 lands, the
  headless CLI brain described here remains the shipped v1.0.0 backend.
- **Date:** 2026-05-24

## Context

Jarvis needs Claude Code as its brain without reimplementing it. Options:

1. **Headless CLI** — `claude -p --output-format stream-json --resume <id>`.
   Well-documented, stable, available today.
2. **Managed Agents API / Agent SDK** — server-side persistent sessions. Cleaner
   in principle, but its exact shape could not be verified during research and
   it is beta.
3. Rebuild the agent loop against the raw Messages API — rejected outright; it
   throws away the entire point of using Claude Code.

## Decision

Build on the **headless CLI** (`claude -p`):

- `--output-format stream-json --include-partial-messages` for token streaming.
- `--resume <session_id>` for multi-turn context (first call returns the id via
  `--output-format json`).
- `--permission-mode acceptEdits` as the default for unattended voice operation.
- `--append-system-prompt` to inject the voice persona.

Speakable text is extracted in `jarvis.brain`, dropping tool-use/tool-result
blocks and fenced code.

## Consequences

- Uses the real Claude Code with all its tools, hooks, and config — no
  reimplementation.
- Subprocess management and stream parsing are our responsibility.
- The Agent SDK / Managed Agents API is revisited as a later optimization; the
  `jarvis.brain` interface is kept narrow so the backend can be swapped without
  disturbing the rest of the cascade. Would supersede this ADR if adopted.
