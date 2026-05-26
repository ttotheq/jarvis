# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Jarvis is a **local-first voice cascade for Claude Code** — it is *not* a reimplementation of Claude Code. It shells out to the real `claude` CLI in headless mode and treats it as the reasoning engine (ADR-0003). The only thing that leaves the machine is the final text prompt; all audio, wake-word, STT, and TTS run on-device (Apple Silicon).

```
mic → wake word → VAD/endpoint → STT → claude -p (stream-json) → TTS → speakers
      openWakeWord  Silero VAD    whisper.cpp                      Kokoro
                                            ↑ wake-phrase barge-in cancels in-flight speech + the claude child
```

It is deliberately a **turn-based cascade**, not a speech-to-speech model — speech-to-speech APIs bundle their own LLM and would compete with Claude Code rather than serve it (ADR-0002).

## Commands

```bash
uv sync                  # core deps + dev tools (uv fetches Python 3.12)
uv sync --extra voice    # add the native voice stack (Kokoro, sounddevice, openWakeWord, silero-vad)

make check               # THE gate: ruff lint + ruff format --check + mypy strict + pytest. Mirrors CI exactly.
make test                # pytest with the coverage gate only
make lint / make format / make type

uv run pytest tests/test_loop.py                 # one file
uv run pytest tests/test_loop.py::test_name      # one test
uv run pytest -k "barge_in"                       # by keyword

uv run jarvis doctor     # verify the native stack is installed (exit 0 when ready)
uv run jarvis config     # print resolved settings (defaults + env + .env)
uv run jarvis run        # the dev harness voice loop (needs --extra voice + a mic)

uv run jarvis service install     # register the launchd LaunchAgent (auto-start at login)
uv run jarvis service status      # installed/loaded? (exit 0 when loaded)
uv run jarvis service uninstall   # unload + remove the plist
```

Never push without a green `make check`. CI (`.github/workflows/ci.yml`) runs on **macos-latest** and does **not** install the `voice` extra.

## Architecture

The orchestrator (`jarvis.loop.VoiceLoop`) is a state machine: `IDLE → LISTENING → THINKING → SPEAKING`. Streaming overlaps THINKING and SPEAKING as a three-stage pipeline (`tokens → sentences → audio → speaker`): a producer segments the `claude` token stream into sentences, a synth thread renders sentence N+1 while N plays (no inter-sentence gap, G4.6), and the consumer writes clips to one persistent output stream (`SoundDeviceStreamingSpeaker` — gapless, drained on clean finish, aborted on barge-in). TTS starts on the *first complete sentence* rather than the full reply.

`jarvis run` now defaults to the **always-on wake-word runtime** (`run_mode=wake_word`): a turn opens at IDLE and blocks in `wait_for_wake_phrase` until "hey jarvis", then `capture_until_endpoint` records LISTENING until Silero VAD detects end-of-speech (or `listen_max_seconds`). It needs no keyboard, so it runs headless under `jarvis service`. `VoiceLoop.wait_for_wake` is the optional seam (None → the old developer harness, opening at LISTENING). `JARVIS_RUN_MODE=push_to_talk|timed` selects the Enter-gated / fixed-window harness modes.

Module map — **Conventional Commit scopes match these module names** (`feat(stt):`, `fix(vad):`):

| Module | Responsibility |
|--------|----------------|
| `jarvis.config` | Twelve-factor `Settings` (pydantic-settings); the single home for every tunable |
| `jarvis.cli` | Typer CLI (`version`, `config`, `doctor`, `run`, `service`) |
| `jarvis.audio` | Mic capture + playback; persistent shared mic + PCM16 resampling for barge-in; persistent output stream (`SoundDeviceStreamingSpeaker`) for gapless playback |
| `jarvis.stt` | whisper.cpp transcription |
| `jarvis.brain` | `claude -p` subprocess, session resume, speakable-text extraction |
| `jarvis.tts` | Kokoro synthesis (generic British male voice) |
| `jarvis.wakeword` | openWakeWord "hey_jarvis" detector; reused for wake-phrase barge-in |
| `jarvis.vad` | Silero VAD endpointing (`Endpointer`) + raw-speech onset primitive |
| `jarvis.persona` | Voice-mode system prompt + the pure conciseness/no-code metric |
| `jarvis.permissions` | `PreToolUse` hook that verbally gates destructive Bash calls |
| `jarvis.loop` | Turn orchestrator + always-on entry point (wake-gated IDLE → VAD-endpointed LISTENING) + barge-in watcher wiring |
| `jarvis.service` | macOS launchd LaunchAgent lifecycle (config-driven plist + `install`/`uninstall`/`status`) |

### The brain (`jarvis.brain`)

Drives `claude -p` and keeps one session across turns. First call captures `session_id`; every later call passes `--resume <session_id>`. `--append-system-prompt` injects the voice persona on both call shapes. `Brain.ask` is blocking (`--output-format json`); `Brain.stream` yields text deltas (`--output-format stream-json --include-partial-messages --verbose` — **stream-json with `-p` requires `--verbose`**).

Only natural-language prose reaches TTS — fenced code, tool-use/tool-result blocks, and inline-code backticks are stripped. `extract_speakable()` is the whole-string form; `SentenceStreamer` is the stateful streaming filter that withholds content inside an unclosed fence/tool block so a half-streamed code block is never spoken.

### The permission gate (`jarvis.permissions`) — important gotcha

This is a Claude Code `PreToolUse` hook that runs as a **separate process** the `claude` child spawns (not inside the jarvis loop). It exists because the headless `claude -p` child runs under `--permission-mode acceptEdits` with no human at the keyboard, so an unattended `rm -rf` or `git push` would otherwise run.

**Verified live (Claude Code 2.1.150):** a `PreToolUse` deny emitted as stdout JSON (`permissionDecision: "deny"`) does **not** block the tool — it still runs. The block Claude Code honors is the **exit-code protocol: exit 2 with the reason on stderr.** So `main()` routes denials through exit-2/stderr while allows ride the documented stdout JSON at exit 0. `decide()` stays pure (returns the documented decision dict, fully unit-tested); `main()` is the thin translation to the channel that actually works. Don't "fix" this back to stdout JSON.

## Conventions (non-negotiable — the whole project is organized around them)

1. **TDD.** Failing test first, then code. Each phase doc (`docs/phases/`) lists its "write-first" tests that define done. Bug fixes start with a reproducing test.
2. **Coverage gate is 85%** (`--cov-fail-under=85` in `pyproject.toml`; started at 80%, rose in Phases 3–4). Test behaviour, not lines.
3. **Docs ship in the same PR as the code.** A new tunable goes in `src/jarvis/config.py` **and** `.env.example` together. Architectural choices get an ADR in `docs/adr/`. Every PR adds a `## [Unreleased]` entry to `CHANGELOG.md`. When a phase/goal closes, update **every** status surface that mentions it (`README.md`, `docs/README.md`, `docs/phases/README.md`, the phase doc, relevant architecture notes).
4. **Trunk-based.** `main` is protected and always releasable. Short-lived `feat/` `fix/` `docs/` `chore/` branches. [Conventional Commits](https://www.conventionalcommits.org/) with scopes from the module map. Releases are cut by tagging `vX.Y.Z` (triggers `.github/workflows/release.yml`).

## Testing patterns

Hardware and native-library edges are isolated behind injected seams (`FrameSource`, `Runner`/`StreamRunner`, `Speaker`/`Microphone` protocols, injected `confirm`/`clock`) so pure logic is tested without a mic, speaker, or spawning `claude`. The real hardware shims carry `# pragma: no cover` and are exercised manually (recorded in each phase doc's Outcomes).

- mypy is **strict** over `src/` only. Native modules with no stubs (`sounddevice`, `numpy`, `kokoro`, `openwakeword`, `silero_vad`, `torch`) are `ignore_missing_imports` via overrides — don't relax strictness elsewhere.
- `conftest.py` clears the settings cache around every test (`get_settings` is `@lru_cache`d). If a test mutates `JARVIS_*` env vars, the autouse fixture already handles isolation; call `get_settings.cache_clear()` if you mutate mid-test.
- `scripts/` (bench, soak, eval) are on `pythonpath` so they can be unit-tested without being packaged.

## Config

All behaviour-changing constants live in `jarvis.config.Settings` — nowhere else. Every field has a safe default, a `JARVIS_`-prefixed env override, and a line in `.env.example` (the canonical tunable list). The brain defaults to `--permission-mode acceptEdits`.
