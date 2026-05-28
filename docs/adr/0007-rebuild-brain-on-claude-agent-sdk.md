# ADR-0007: Rebuild the brain on the Claude Agent SDK

- **Status:** Accepted (implementation deferred to after 2026-06-15 — see
  *Consequences → Timing and billing*)
- **Date:** 2026-05-27
- **Supersedes:** [ADR-0003](0003-drive-claude-code-via-headless-mode.md) —
  which kept `jarvis.brain` narrow exactly so the backend could be swapped
  without disturbing the rest of the cascade. This is that swap.

## Context

v1.0.0 ships [ADR-0003's headless-CLI brain](0003-drive-claude-code-via-headless-mode.md):
each turn spawns a fresh `claude -p --output-format stream-json
--include-partial-messages --verbose --resume <session_id>` child. That decision
was right for getting to a working voice cascade — well-documented, stable,
shippable — but it carries two structural costs the always-on service has
surfaced:

1. **Spawn-per-turn latency.** Measured time-to-first-audio is ~6 s p50 (G2.3,
   recorded in `docs/phases/phase-2-wakeword-streaming.md`). The Phase 2 doc
   already renegotiated the original ≤ 1.5 s p50 target into a **spawn-per-turn
   baseline** plus a **forward target (≤ 2.0 s p50) gated on a persistent-brain
   re-architecture**. ADR-0007 is that re-architecture.
2. **Two-process permission gate.** `jarvis.permissions` is a `PreToolUse` hook
   the spawned `claude` child invokes as a *separate subprocess*, communicating
   verdicts via stdin/stdout + the exit-2/stderr protocol (the
   [CLAUDE.md gotcha](../../CLAUDE.md)). The spoken-confirmation primitives
   (`build_live_confirm`) live in the parent (jarvis) process; the hook
   subprocess can't reach them and bootstraps fresh Kokoro/whisper for every
   destructive call. Functional, but architecturally noisy.

A separate motivation surfaced in usage: **today's Jarvis and the user's own
interactive `claude` terminal session are separate processes with separate
conversations.** They share file state (vault, CLAUDE.md, memory files) but not
live turns. Several use cases — "drop a follow-up into my current coding
context", "extend the conversation by voice while I keep typing" — want the two
to be one session, not two.

[Research conducted 2026-05-27](https://code.claude.com/docs/en/agent-sdk/overview)
confirms the **local Claude Agent SDK** (`pip install claude-agent-sdk`, the
same engine Claude Code itself is built on, distinct from the cloud-hosted
*Managed Agents* product) supports the surface we need:

- **`ClaudeSDKClient`** — persistent in-process client; session state lives in
  memory across `.query()` calls and auto-persists to the same
  `~/.claude/projects/<encoded-cwd>/<id>.jsonl` files the official CLI uses.
- **Partial-message streaming** (`include_partial_messages=True`) emits
  `content_block_delta` events with text deltas, so the existing
  `SentenceStreamer` early-TTS pattern carries over directly.
- **In-process hooks.** `PreToolUse` (plus `PostToolUse`, `UserPromptSubmit`,
  `Stop`, etc.) take async callbacks that return a Claude Code
  `permissionDecision` dict natively. No subprocess, no exit-2 protocol.
- **CLAUDE.md auto-loading** (`setting_sources=["user", "project"]`), full MCP
  (local stdio + remote HTTP), all built-in tools (Read/Edit/Write/Bash/etc.),
  all `permission_mode` values.

The Explore audit at the same date confirms the **brain interface boundary is
clean enough to swap**: the loop, audio, STT, TTS, VAD, wake word, chimes,
persona, and config layers depend only on the
`TokenStream = Callable[[str], Iterator[str]]` callable that `Brain.stream`
satisfies (`src/jarvis/loop.py:58`, consumer
`src/jarvis/loop.py:225`). The single CLI wiring point is
`src/jarvis/cli.py:271` (`stream=Brain(settings).stream`). Replacing the brain
backend behind that seam touches one module; everything else is untouched.

## Decision

Rebuild `jarvis.brain` on the local Claude Agent SDK (Python package
`claude-agent-sdk`) as the brain backend for Jarvis 2.0. Specifically:

1. **Replace** `jarvis.brain.Brain` internals: the per-turn subprocess
   (`_default_runner`, `_default_stream_runner`, `_base_argv`, `_build_argv`,
   `_build_stream_argv`, `--resume` session-id capture) is removed in favour of
   a single long-lived `ClaudeSDKClient` constructed once at startup.
   `Brain.stream(prompt) -> Iterator[str]` keeps its public contract — it
   adapts the SDK's async `content_block_delta` events into the same text-delta
   iterator the loop already consumes. `extract_speakable` and
   `SentenceStreamer` are pure and carry over unchanged.

2. **Collapse** `jarvis.permissions` from a `PreToolUse` *subprocess* hook to
   an in-process SDK callback. The pure decision logic (`is_destructive`,
   `summarize`, `decide`, `interpret_confirmation`) is CI-proven (`tests/test_permission_gate.py`)
   and carries over byte-for-byte. The exit-2/stderr `main()` entrypoint and
   the Claude Code `settings.json` matcher registration are removed; in their
   place a `PreToolUse` hook is registered directly on
   `ClaudeAgentOptions(hooks={...})` and returns the verdict dict in-process.
   `build_live_confirm` becomes a normal in-process callable that drives the
   *same* persistent Kokoro/Whisper/microphone instances the rest of the
   cascade already holds — no per-confirmation native re-bootstrap.

3. **Reuse** every other module as-is: `jarvis.audio`, `jarvis.stt`,
   `jarvis.tts`, `jarvis.vad`, `jarvis.wakeword`, `jarvis.chimes`,
   `jarvis.persona`, `jarvis.config`, `jarvis.loop`. The injection seams on
   `VoiceLoop` (`record_turn`, `transcribe`, `stream`, `synthesize`, `speaker`,
   `wait_for_wake`, `watch_barge_in`, `on_state`, `clock`) are unchanged. The
   G4.6 three-stage `tokens → sentences → audio → speaker` pipeline is
   preserved. The persistent mic + persistent streaming speaker are unchanged.

4. **Run loop becomes async.** The SDK is async-only. `jarvis.cli.run` is
   restructured around `asyncio.run`; voice and (new) keyboard input producers
   feed an `asyncio.Queue` that the agent loop drains, so a single
   `ClaudeSDKClient` session accepts user turns from either channel into one
   conversation. The OS-thread mic capture stays as-is and bridges into the
   event loop via `asyncio.run_coroutine_threadsafe`.

5. **Migration is config-flagged.** A new `JARVIS_BRAIN={cli,sdk}` setting (in
   `jarvis.config.Settings`, default `cli` during transition) selects the
   backend. v1 and v2 brain implementations coexist on `main` behind that flag
   until the SDK path is proven on Ty's hardware end-to-end, at which point the
   default flips to `sdk`, the CLI brain is removed, and `JARVIS_CLAUDE_BINARY`
   is retired. v1 stays buildable from `main` only as long as the flag exists;
   we are not committing to perpetual dual-backend support.

The detailed work plan — measurable goals, write-first tests, sequencing — is
deferred to a separate `docs/phases/phase-5-sdk-rewrite.md` plan doc, drafted
once this ADR is approved.

## Consequences

### Positive

- **Closes the G2.3 forward target.** One persistent agent eliminates the
  spawn cost per turn; the remaining TTFA is just the cascade itself
  (VAD-hangover → STT → SDK first-token → first-sentence synth). Estimated
  ~6 s → ~2 s p50, to be measured by an updated
  `scripts/bench_latency.py --mode ttfa`.
- **Unified voice + keyboard into one conversation.** The async queue lets
  speech and typing feed the same `ClaudeSDKClient` session. The "Jarvis vs.
  my terminal Claude are separate processes" gap closes for the in-Jarvis
  channel — talking and typing become two inputs into the same agent. (A
  separate `claude` terminal session is still distinct; closing *that* gap
  needs SDK-level session-attach which doesn't exist yet.)
- **Collapses the permission gate.** No more spawned hook subprocess, no more
  exit-2 protocol, no more per-confirmation Kokoro/Whisper bootstrap. The
  spoken yes/no rides the loop's persistent speaker and the persistent mic.
- **Drops a class of fragility.** No subprocess lifecycle, no
  argv-index-sensitive `--output-format stream-json` insertion, no
  `--verbose`-required-with-stream-json gotcha (CLAUDE.md), no
  `GeneratorExit`-to-kill-the-child dance for barge-in.
- **`jarvis.permissions` becomes a `PreToolUse` callback** that returns a
  verdict directly — the CLAUDE.md gotcha about Claude 2.1.150 not honouring
  stdout-JSON deny is obsolete by construction.

### Negative — flagged honestly

- **Auth model changes.** v1 rides the user's Claude Code subscription via the
  `claude` CLI login. The Agent SDK requires `ANTHROPIC_API_KEY`. There is no
  way to share Claude Code subscription auth with the SDK today.
- **Timing and billing.** Until **2026-06-15** the Agent SDK bills per-token
  against the API key only — there is no subscription bundling. After
  2026-06-15, a separate monthly Agent SDK credit bucket is included with
  Claude subscription plans (Pro $20, Max 5x $100, Max 20x $200 per month,
  independent of interactive Claude Code usage limits). Ty's decision
  (2026-05-27): defer implementation start until after 2026-06-15 so the SDK
  spend lands in the bundled credit bucket from day one, rather than paying
  per-token during a multi-week prototyping window. v1.0.0 stays the
  user-facing release in the meantime.
- **Async refactor.** `jarvis.cli.run` and the loop wiring become async. The
  pure orchestration in `VoiceLoop._think_and_speak` already runs producer /
  synth / consumer on threads, so the change is at the I/O boundary
  (`asyncio.run`, queue bridges) rather than the core. Brain-coupled tests
  (`tests/test_brain_streaming.py`, `tests/test_brain_session.py`,
  `tests/test_bench_brain.py`) are rewritten against an SDK fake instead of a
  subprocess `Runner` fake; brain-agnostic tests stay untouched.
- **API-stability dependency.** v1 depended on a stable CLI flag surface;
  v2 depends on a stable SDK API surface. The SDK is younger than the CLI.
  Mitigation: pin `claude-agent-sdk` to a known-good minor version in
  `pyproject.toml` and treat upgrades as deliberate work, the way we treat
  whisper.cpp and Kokoro upgrades.
- **Extended thinking is mutually exclusive with partial streaming** on the
  Python SDK (the `max_thinking_tokens` parameter disables the
  `content_block_delta` events we rely on for early TTS). Practical effect:
  Jarvis 2.0 does not enable extended thinking — early-TTS wins. If
  extended-thinking voice replies ever become a requirement, it would mean
  reverting to whole-message playback for those turns.
- **Bench scripts touched.** `scripts/bench_brain.py` and the TTFA mode of
  `scripts/bench_latency.py` are rewritten to inject an SDK seam instead of a
  subprocess `StreamRunner`. The Phase 2 numbers stay as the v1 baseline; v2
  produces a new measured distribution alongside.

### Neutral — what stays

The injection seams on `VoiceLoop` are stable. `jarvis.audio`,
`jarvis.stt`, `jarvis.tts`, `jarvis.vad`, `jarvis.wakeword`, `jarvis.chimes`,
`jarvis.persona`, the always-on runtime (`wait_for_wake_phrase`,
`capture_until_endpoint`, `Lazy`/`warm_in_background`), the launchd service,
and the entire `JARVIS_*` config surface (except the retired
`JARVIS_CLAUDE_BINARY`) carry over without change.
`extract_speakable`, `SentenceStreamer`, `VOICE_SYSTEM_PROMPT`,
`is_destructive`, `decide`, `interpret_confirmation` carry over verbatim.

## Alternatives considered

1. **Stay on the headless CLI (the v1 status quo).** Keeps the existing auth
   model and avoids any rewrite. Costs: the spawn-per-turn ~6 s p50 stays;
   the permission-gate subprocess shape stays; unified voice+keyboard never
   exists. Rejected — the unified-session and latency gaps are exactly what
   v2 is for.
2. **The cloud-hosted Managed Agents product** (different from the local
   Agent SDK). Trade-offs: server-side session state, separate billing, no
   local file access without an MCP bridge. The first SDK research pass
   accidentally targeted Managed Agents and reported many "gotchas" (no
   CLAUDE.md, no in-process hooks, no local MCP, no partial streaming) — all
   of which apply to Managed Agents but **not** to the local Agent SDK. The
   correction is recorded here so future readers don't repeat the mistake.
   Rejected — Jarvis needs local file access and the on-device persona, and
   Managed Agents trades those away.
3. **Rebuild against the raw Messages API.** Throws away the entire reason
   we use Claude Code — tools, hooks, MCP, session management. Rejected
   outright, same call ADR-0003 made.
4. **Hybrid file-bridge between Jarvis v1 and the user's terminal `claude`
   session.** Keep v1 architecture, lean harder on shared vault/memory files
   to coordinate. Costs nothing to build (it's how v1 already works in
   practice). Rejected as the *only* path because it does not address the
   spawn-per-turn latency cost or the permission-gate subprocess shape — it
   only addresses cross-session context sharing, and even there only
   asynchronously. It remains a useful *complement* to v2 (memory and vault
   files are the cross-process channel between Jarvis and any other Claude
   Code session, regardless of which brain Jarvis runs).
5. **Attach Jarvis to an existing live interactive `claude` terminal
   session.** No supported SDK / CLI surface for an external process to share
   an in-flight interactive session — `--resume <id>` creates a continuation,
   not an attachment, and the terminal's own state lives in its process.
   Rejected as infeasible today; revisit if Claude Code ever ships a
   "live-attach" API.
