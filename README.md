# Jarvis

A local-first, voice-controlled interface for [Claude Code](https://claude.com/claude-code) — speak to it, it speaks back, in a refined British register reminiscent of Tony Stark's assistant.

Jarvis is **not** a reimplementation of Claude Code. It is a thin, local voice cascade that uses the real Claude Code CLI as its brain:

```
push-to-talk / timed turn today → mic → STT → claude (headless) → TTS → speakers
                                                      ↑ barge-in cancels in-flight speech + task
```

Everything in the voice path runs on-device (Apple Silicon). The only thing that leaves the machine is the final text prompt to Claude — exactly as Claude Code already works.

## Status

**Phase 3 shipped; G4.0 and G4.1 are in** — Jarvis streams Claude replies, speaks
in-character, verbally gates destructive Bash actions before they run, only lets
`"hey jarvis"` interrupt during `SPEAKING` on the live path, and now installs as a
macOS launchd background service (`jarvis service install`) that auto-starts at
login and restarts on crash. The default `jarvis run` command is still a
development harness (Enter-gated push-to-talk or timed turns in non-interactive
shells); wiring that harness into an always-on wake-word loop is the remaining
Phase 4 step.

| Phase | Goal | Status |
|------|------|--------|
| [0 — Spike & de-risk](docs/phases/phase-0-spike.md) | Prove the local stack installs and Claude round-trips on this Mac | ✅ Done |
| [1 — Walking skeleton](docs/phases/phase-1-skeleton.md) | Push-to-talk → STT → Claude → TTS, end to end | ✅ Done |
| [2 — Wake word + streaming](docs/phases/phase-2-wakeword-streaming.md) | Wake-word/VAD primitives, streaming, measured latency | ✅ Done |
| [3 — Jarvis feel](docs/phases/phase-3-jarvis-feel.md) | Barge-in, persona, spoken permission gating | ✅ Done |
| [4 — Daemon polish](docs/phases/phase-4-daemon.md) | G4.0 wake-phrase barge-in ✅, launchd service ✅, v1.0.0 release | In progress |

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
uv run jarvis run                                # push-to-talk: Enter to talk, Ctrl-C to quit
```

`jarvis run` is still the development harness: it defaults to Enter-gated
push-to-talk, and in a non-interactive shell you can set `JARVIS_PTT_SECONDS`
(timed turns) plus `JARVIS_MAX_TURNS` to run hands-free. Its live barge-in path
now shares one persistent mic between capture and `SPEAKING`, resamples to
openWakeWord's 16 kHz frame geometry when needed, and only interrupts on
`"hey jarvis"`. The always-on wake-word daemon is the remaining Phase 4 step.
Copy `.env.example` to `.env` to override any setting locally; nothing in
`.env` is committed.

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
`~/Library/Logs/jarvis/`. (`jarvis run` is still the Enter-gated dev harness, so
under the service it currently exits for lack of a TTY; the always-on wake-word
entry point is the remaining Phase 4 step.)

## Architecture & decisions

- [Architecture](docs/architecture.md) — the voice cascade and runtime state machine.
- [Voice persona](docs/voice-persona.md) — how Claude is steered to sound like Jarvis and stay speakable.
- [Architecture Decision Records](docs/adr/) — why the project is built the way it is.

## Contributing

This is a personal project, but it follows real conventions: trunk-based development with short-lived branches, [Conventional Commits](https://www.conventionalcommits.org/), test-driven development, and a green CI gate before merge. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Legal

"Jarvis" and the Iron Man character are trademarks of Marvel/Disney; this is an unaffiliated personal project. The voice persona deliberately uses a *generic* refined British male voice — **not** a clone of any identifiable person — to stay clear of voice right-of-publicity law (e.g. the ELVIS Act). See [`docs/voice-persona.md`](docs/voice-persona.md).

All rights reserved — see [LICENSE](LICENSE).
