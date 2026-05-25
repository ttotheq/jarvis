# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 2 wake-word detection (G2.1): new `jarvis.wakeword` module wraps
  openWakeWord's pretrained `hey_jarvis` model as an injected `Detector` callable
  (one 80 ms PCM16 frame → score in [0, 1]). `WakeWordListener` owns the threshold
  comparison and frame loop and fires on the first score to cross `wake_threshold`
  (config key already present from scaffolding), short-circuiting so an unbounded
  live-mic stream is fine. The G2.1 metric (`Accuracy`: true-accept rate +
  false-accepts-per-30-min) is pure and unit-tested with fakes; `scripts/soak_wakeword.py`
  is the live 30-min ambient false-accept soak (distinct wakes counted via
  rising-edge debounce, testable core injected). The openWakeWord backend is a
  native shim excluded from coverage.
- Phase 2 G2.1 verification: `scripts/gen_wakeword_fixtures.py` synthesizes a
  reproducible labeled set with Jarvis's own TTS (24 "hey jarvis" positives + 30
  min of near-miss-laden ambient) and measures the real `hey_jarvis` model.
  `test_labeled_fixtures_meet_targets` gates on it (runs locally with the voice
  extra, skips in CI). Measured at the tuned threshold: **true-accept 100% (24/24,
  target ≥ 95%), false-accept 1 / 30 min (target ≤ 1)**. The full threshold sweep
  and the synthetic-vs-live caveat are in the Phase 2 doc Outcomes.
- Phase 2 streaming overlap + state machine (G2.4): `jarvis.brain` gains a
  streaming path (`Brain.stream`, `--output-format stream-json
  --include-partial-messages`) that yields assistant text deltas, and a stateful
  `SentenceStreamer` filter that tracks in-fence / in-tool-block / in-inline-code
  state and emits a complete sentence only once it is confirmed safe — a code
  fence that opens mid-stream and never closes is never spoken; segmentation does
  not split on abbreviations ("Mr.") or decimals ("3.14"). `jarvis.loop` is
  rewritten as the `IDLE→LISTENING→THINKING→SPEAKING→IDLE` state machine with a
  producer (token stream → sentence queue) / consumer (TTS) overlap, so the first
  sentence is spoken before Claude's full response completes. `scripts/bench_brain.py`
  reports p95. The blocking `Brain.ask` is retained for G1.4 session continuity.
  Streaming TTFT measured at 2.76 s p50 / 3.24 s p95 — recorded in the Phase 2
  doc, which flags G2.3 (time-to-first-audio ≤ 1.5 s p50) as unreachable under the
  spawn-per-turn model and pending renegotiation.

### Changed

- `wake_threshold` default raised **0.5 → 0.9** (config + `.env.example`), tuned
  against the G2.1 soak: 0.5 let "hey X" near-misses through (~10 false-accepts /
  30 min), while 0.9 holds them to 1 / 30 min at 100% true-accept.
- `jarvis.loop.VoiceLoop` now takes a `stream: Callable[[str], Iterator[str]]`
  (the brain's token stream) instead of a `Brain` instance, and exposes the
  `State` enum and an optional `on_state` transition observer.

### Fixed

- `OpenWakeWordDetector` no longer calls a non-existent `openwakeword.utils.download_models`
  (the pretrained `hey_jarvis` ONNX ships bundled); it loads that bundled model path
  directly, so the native detector works on first use.

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
