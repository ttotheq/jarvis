# Jarvis

A local-first, voice-controlled interface for [Claude Code](https://claude.com/claude-code) — speak to it, it speaks back, in a refined British register reminiscent of Tony Stark's assistant.

Jarvis is **not** a reimplementation of Claude Code. It is a thin, local voice cascade that uses the real Claude Code CLI as its brain:

```
"hey jarvis" → mic → VAD endpoint → STT → claude (headless) → TTS → speakers
   wake word                                     ↑ "hey jarvis" barge-in cancels in-flight speech + task
```

Everything in the voice path runs on-device (Apple Silicon). The only thing that leaves the machine is the final text prompt to Claude — exactly as Claude Code already works.

## Status

**Phase 3 shipped; G4.0/G4.1/G4.2/G4.3 and the always-on runtime are in** — Jarvis streams
Claude replies, speaks in-character, verbally gates destructive Bash actions before
they run, only lets `"hey jarvis"` interrupt during `SPEAKING`, and installs as a
macOS launchd background service (`jarvis service install`) that auto-starts at login
and restarts on crash. `jarvis run` now defaults to an always-on **wake-word**
cascade: it waits for `"hey jarvis"`, endpoints your speech with VAD, replies, and
returns to waiting — no keyboard, so it runs headless under the service.
(Push-to-talk and timed turns remain available via `JARVIS_RUN_MODE`.) Cold start is
fast: readiness gates on the wake detector alone while Kokoro/VAD warm in the
background, so it is ready for `"hey jarvis"` in **~1 s** (G4.2, target ≤ 10 s); a
1-hour idle soak held flat memory with zero crashes (G4.3). Remaining Phase 4 work is
config-driven verification and the v1.0.0 release.

| Phase | Goal | Status |
|------|------|--------|
| [0 — Spike & de-risk](docs/phases/phase-0-spike.md) | Prove the local stack installs and Claude round-trips on this Mac | ✅ Done |
| [1 — Walking skeleton](docs/phases/phase-1-skeleton.md) | Push-to-talk → STT → Claude → TTS, end to end | ✅ Done |
| [2 — Wake word + streaming](docs/phases/phase-2-wakeword-streaming.md) | Wake-word/VAD primitives, streaming, measured latency | ✅ Done |
| [3 — Jarvis feel](docs/phases/phase-3-jarvis-feel.md) | Barge-in, persona, spoken permission gating | ✅ Done |
| [4 — Daemon polish](docs/phases/phase-4-daemon.md) | G4.0 wake-phrase barge-in ✅, launchd service ✅, ~1 s cold start ✅, 1-hr soak ✅, v1.0.0 release | In progress |

Each phase has **measurable acceptance goals** ([overview](docs/phases/README.md)) designed to be tracked as Claude Code goals in later iterations.

## Quickstart (development)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv will fetch it).

```bash
uv sync                 # create the venv and install dev dependencies
uv run jarvis version   # smoke test the CLI
uv run jarvis config    # print the resolved configuration
make test               # run the test suite with coverage gate
make check              # lint + type-check + test (the CI gate, locally)
```

### Running the voice loop

The voice runtime needs the native stack. On Apple Silicon:

```bash
brew install portaudio whisper-cpp espeak-ng     # system libraries
uv sync --extra voice                            # Kokoro, sounddevice, openWakeWord, …
# place a whisper model at ~/.cache/jarvis/whisper/ggml-large-v3-turbo.bin
uv run jarvis doctor                             # verify the stack (exit 0 when ready)
uv run jarvis run                                # always-on: say "hey jarvis", Ctrl-C to quit
```

`jarvis run` defaults to the always-on **wake-word** cascade: say `"hey jarvis"`,
speak your request, and VAD ends the turn on your trailing silence; it then replies
and returns to waiting. The mic stays hot during playback so `"hey jarvis"` can barge
in. For the developer harness, set `JARVIS_RUN_MODE=push_to_talk` (Enter-gated) or
`JARVIS_RUN_MODE=timed` with `JARVIS_PTT_SECONDS` (plus `JARVIS_MAX_TURNS` to stop
after N turns). Copy `.env.example` to `.env` to override any setting locally;
nothing in `.env` is committed.

### Running as a background service (macOS)

Jarvis installs as a per-user **launchd LaunchAgent** that auto-starts at login
and restarts on crash ([ADR-0006](docs/adr/0006-launchd-launchagent-service.md)):

```bash
uv run jarvis service install     # write ~/Library/LaunchAgents/<label>.plist and load it
uv run jarvis service status      # installed/loaded? (exit 0 when loaded)
uv run jarvis service uninstall   # unload and remove the plist
```

The generated plist resolves the venv interpreter, project directory, and `PATH`
**at install time** — no paths are hard-coded — and is config-driven via
`JARVIS_SERVICE_LABEL` and `JARVIS_SERVICE_LOG_DIR`. Logs default to
`~/Library/Logs/jarvis/`. The service runs `jarvis run` in its default
`wake_word` mode, so it listens for "hey jarvis" headlessly with no keyboard.

## Architecture & decisions

- [Architecture](docs/architecture.md) — the voice cascade and runtime state machine.
- [Voice persona](docs/voice-persona.md) — how Claude is steered to sound like Jarvis and stay speakable.
- [Architecture Decision Records](docs/adr/) — why the project is built the way it is.

## Contributing

This is a personal project, but it follows real conventions: trunk-based development with short-lived branches, [Conventional Commits](https://www.conventionalcommits.org/), test-driven development, and a green CI gate before merge. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Legal

"Jarvis" and the Iron Man character are trademarks of Marvel/Disney; this is an unaffiliated personal project. The voice persona deliberately uses a *generic* refined British male voice — **not** a clone of any identifiable person — to stay clear of voice right-of-publicity law (e.g. the ELVIS Act). See [`docs/voice-persona.md`](docs/voice-persona.md).

All rights reserved — see [LICENSE](LICENSE).
