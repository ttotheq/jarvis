# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 1 walking skeleton (push-to-talk): `jarvis.brain` drives Claude Code
  headlessly (`claude -p --output-format json`), parsing `.result`/`.session_id`
  and resuming across turns via `--resume`; `extract_speakable()` strips fenced
  code and tool-use/tool-result blocks (G1.3, G1.4). `jarvis.audio` provides a
  device-agnostic PCM16 capture loop, `jarvis.stt` a `word_error_rate()` metric
  plus a whisper.cpp transcriber, and `jarvis.tts` a Kokoro synthesizer with an
  empty-reply guard. `jarvis.loop` orchestrates capture → STT → brain → TTS and
  `jarvis run` exposes the push-to-talk loop. Hardware/native edges are injected
  and tested with fakes; `mypy` ignores the optional voice-stack stubs.
- Voice stack wired in: the `voice` extra pins Kokoro, numpy, openWakeWord,
  sounddevice, and soundfile (`uv sync --extra voice`). `jarvis run` gains a
  hands-free timed mode (`JARVIS_PTT_SECONDS`, `JARVIS_MAX_TURNS`, guided spoken
  prompts) for non-interactive shells, and `scripts/record_devset.py` records and
  transcribes the STT dev set in one guided session.
- Phase 0 spike: `jarvis doctor` command (logic in `jarvis.doctor`) that probes
  the local voice stack — PortAudio, whisper.cpp, openWakeWord, Kokoro — and
  exits non-zero naming any missing dependency, with fake-injected tests.
- `scripts/bench_brain.py`: throwaway benchmark timing `claude -p`
  time-to-first-token over N runs, with a subprocess-mocked unit test. First
  live read: ~2.8 s median TTFT (see `docs/phases/phase-0-spike.md`).
- Project scaffolding: `uv`-managed Python 3.12 package with a `jarvis` CLI
  (`version`, `config` commands).
- Twelve-factor configuration layer (`jarvis.config`) driven by `JARVIS_*`
  environment variables and `.env`, with full test coverage.
- Quality gates: ruff (lint + format), mypy (strict), pytest with an 80%
  coverage floor; a `make check` target that mirrors CI.
- CI workflow (lint + type + test on macOS) and a tag-driven release workflow.
- GitHub repository conventions: issue/PR templates, CODEOWNERS, Dependabot.
- Documentation: architecture, voice persona, five Architecture Decision
  Records, and the five-phase plan with measurable goals.

[Unreleased]: https://github.com/ttotheq/jarvis/commits/main
