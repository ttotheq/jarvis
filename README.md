# Jarvis

A local-first, voice-controlled interface for [Claude Code](https://claude.com/claude-code) — speak to it, it speaks back, in a refined British register reminiscent of Tony Stark's assistant.

Jarvis is **not** a reimplementation of Claude Code. It is a thin, local voice cascade that uses the real Claude Code CLI as its brain:

```
"Hey Jarvis" → mic → VAD → STT → claude (headless) → TTS → speakers
                                        ↑ barge-in cancels in-flight speech + task
```

Everything in the voice path runs on-device (Apple Silicon). The only thing that leaves the machine is the final text prompt to Claude — exactly as Claude Code already works.

## Status

Pre-Phase-0. This repository currently contains the project skeleton, quality gates, and phase documentation. The voice runtime is built phase-by-phase under [`docs/phases/`](docs/phases/).

| Phase | Goal | Status |
|------|------|--------|
| [0 — Spike & de-risk](docs/phases/phase-0-spike.md) | Prove the local stack installs and Claude round-trips on this Mac | Not started |
| [1 — Walking skeleton](docs/phases/phase-1-skeleton.md) | Push-to-talk → STT → Claude → TTS, end to end | Not started |
| [2 — Wake word + streaming](docs/phases/phase-2-wakeword-streaming.md) | "Hey Jarvis" activation, sub-1.5 s response | Not started |
| [3 — Jarvis feel](docs/phases/phase-3-jarvis-feel.md) | Barge-in, persona, spoken permission gating | Not started |
| [4 — Daemon polish](docs/phases/phase-4-daemon.md) | Always-on launchd service, v1.0.0 release | Not started |

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

Copy `.env.example` to `.env` to override any setting locally; nothing in `.env` is committed.

## Architecture & decisions

- [Architecture](docs/architecture.md) — the voice cascade and runtime state machine.
- [Voice persona](docs/voice-persona.md) — how Claude is steered to sound like Jarvis and stay speakable.
- [Architecture Decision Records](docs/adr/) — why the project is built the way it is.

## Contributing

This is a personal project, but it follows real conventions: trunk-based development with short-lived branches, [Conventional Commits](https://www.conventionalcommits.org/), test-driven development, and a green CI gate before merge. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Legal

"Jarvis" and the Iron Man character are trademarks of Marvel/Disney; this is an unaffiliated personal project. The voice persona deliberately uses a *generic* refined British male voice — **not** a clone of any identifiable person — to stay clear of voice right-of-publicity law (e.g. the ELVIS Act). See [`docs/voice-persona.md`](docs/voice-persona.md).

All rights reserved — see [LICENSE](LICENSE).
