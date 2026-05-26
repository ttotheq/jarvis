# Phase 3 — live end-to-end demo record

- **Date:** 2026-05-25
- **Goal:** the Phase 3 Definition of Done — *a recorded session demonstrates
  interrupt (G3.1) + in-character replies (G3.2) + a spoken confirmation (G3.3).*
- **Machine:** MacBook Air (Apple Silicon, macOS 25.5). `jarvis doctor`: PortAudio,
  whisper.cpp (`whisper-cli` + `ggml-large-v3-turbo`), openWakeWord, Kokoro all
  present. `claude` 2.1.150. Default audio: built-in mic + speakers.

The session was driven from the assistant's shell against the real components
(`Brain.stream` → live `claude`, Kokoro TTS, whisper.cpp STT, the live barge-in
watcher). Interactive push-to-talk can't be driven headlessly, so each clip used a
fixed-window capture harness around the **real** `VoiceLoop` / hook code paths.
A throwaway target (`/tmp/jarvis-demo/old-cache.log`) stood in for anything real.

## Headline outcome

| Behaviour | Goal | Live result |
|-----------|------|-------------|
| Spoken permission gate | G3.3 | **PASS — demonstrated audibly, both directions, end-to-end.** A live blocking-protocol bug was found and fixed in the process (see below). |
| In-character replies | G3.2 | **PASS — heard live, genuinely in-register** (dry, concise, "sir"). |
| Interrupt / barge-in | G3.1 | **Mechanism proven (fires < 300 ms), but live acoustic barge-in self-triggers** (no echo cancellation + a CoreAudio stream conflict) — recorded as a confirmed limitation, fix deferred. |

## Clip 2 — spoken permission gate (G3.3)

### Wiring smoke test (no audio)

A logging hook attached via `claude -p --settings <file>` (so this session's
ambient `.claude` was untouched) confirmed `claude` 2.1.150 invokes a `PreToolUse`
Bash hook in headless mode and that the payload matches the module's assumptions —
snake_case, e.g.:

```json
{ "hook_event_name": "PreToolUse", "tool_name": "Bash",
  "tool_input": { "command": "ls -la", "description": "List files in current directory" },
  "permission_mode": "acceptEdits", "cwd": "/private/tmp/jarvis-demo", ... }
```

`is_destructive` correctly read `ls -la` as safe; the call ran with no audio.

### Spoken confirmation — isolation takes

Piping a destructive payload into `jarvis.permissions` exercised the live
speak→listen→decide path (real persona summary, Kokoro, whisper, gate logic):

```
QUESTION : You're about to delete files, sir — shall I proceed?     (no command/path read aloud)
  spoken "No."  → HEARD 'No.'  → permissionDecision "deny"
  spoken "Yes." → HEARD 'Yes.' → permissionDecision "allow"
```

### End-to-end — gating a real `claude` tool call, and the bug it exposed

Running real `claude` ("delete old-cache.log … `rm -f`") with the hook registered,
answering **"No"**, first revealed a **defect the unit tests could never catch**:

- The hook fired, spoke, heard "No", and emitted
  `{"hookSpecificOutput":{...,"permissionDecision":"deny"}}` on stdout (exit 0) —
  **but the file was deleted anyway.** `claude` 2.1.150 does **not** block a tool
  on a stdout `deny` JSON.

Isolating the mechanism, an always-deny hook using the **exit-code protocol**
(reason on stderr, `exit 2`) *did* block: the file survived and `claude` reported
*"The deletion was blocked by a guard … denied at confirmation."*

**Fix:** `main()` now routes a denial through **exit 2 + stderr** (the channel
Claude Code honors) while an allow still rides the documented stdout JSON at exit 0.
`decide()` stays pure and still returns the documented decision dict. Re-run, live:

```
say "No"  → file SURVIVES. claude: "The deletion was declined — a permission hook
            intercepted the rm call and it was turned down at the spoken confirmation
            step … I won't retry or work around it."
say "Yes" → file DELETED. claude: "Done. old-cache.log is deleted."
```

This is the G3.3 metric proven against ground truth (file state + the hook decision),
not just narration: **a destructive call is gated by a spoken confirmation before it
runs; "no" blocks it, "yes" lets it through.**

## Clip 1 — in-character replies (G3.2) + interrupt (G3.1)

Driving the real `VoiceLoop` (Brain.stream → Kokoro, live `build_default_barge_in_watcher`):

**Persona (G3.2) — heard live, in-register.** Two replies, both dry and concise:

- to a near-empty transcript: *"A solitary full stop, sir — I'll take that as a
  nudge rather than an instruction."*
- *"Phase 3's wrapped and merged, sir, but you've left uncommitted edits to the
  permission gate and its tests sitting on main."* (Correct, unprompted, and
  delivered with the advisor's standing to nag — the persona working as intended.)

This complements the recorded 20-prompt G3.2 eval (100 % ≤ 50 words, 0 code leaked).

**Barge-in (G3.1) — mechanism fires, but self-triggers acoustically.** Across two
takes the watcher fired at a near-identical **107 ms** (≤ 300 ms budget) and the
machine returned to `LISTENING` — but **sub-human-reaction-time and deterministic**,
so it was not the operator's deliberate interrupt. Root cause, confirmed live:

1. **No acoustic echo cancellation** (the limitation already flagged in the Phase 3
   Outcomes): the hot mic hears Kokoro's own playback. Raising the onset threshold
   to 0.85 did not help — the trigger isn't really the operator's voice.
2. **CoreAudio `-50` on concurrent streams:** opening the watcher's 16 kHz **input**
   stream while Kokoro's 24 kHz **output** stream plays throws
   `||PaMacCore (AUHAL)|| err='-50'`; the onset detector then trips on the bad/echoed
   first frame, aborting playback **before it is audible** (which is why the operator
   heard nothing on these takes — a symptom of the self-trigger, not an output fault;
   a plain-tone + Kokoro output test confirmed the speakers work).

These reproduce in `jarvis run` too (it uses the same `make_sounddevice_source` for
capture and for the watcher). The barge-in *logic* is correct and unit-proven
(`tests/test_barge_in.py`); the gap is the live audio I/O around it.

## Findings & follow-ups

1. **Shipped fix:** PreToolUse denial via exit-2/stderr, not stdout JSON (verified
   against `claude` 2.1.150). Covered in `tests/test_permission_gate.py`.
2. **Deferred (beyond Phase 3):** live barge-in needs **output ducking or AEC**, and
   the input watcher should not open a second device stream concurrent with Kokoro
   output (resolve the `-50`: share one stream, match sample rates, or duck/pause
   capture during playback). Headphones would side-step the echo for a clean demo.
3. **Registering the gate for the real loop:** add to the `settings.json` the brain's
   `claude` reads (Bash matcher → `… python -m jarvis.permissions`); see the Phase 3
   doc Outcomes for the snippet.
