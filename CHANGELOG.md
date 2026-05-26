# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 3 voice persona (G3.2): new `jarvis.persona` module owns the voice-mode
  system prompt (`VOICE_SYSTEM_PROMPT`, the speakable-output contract from
  `docs/voice-persona.md`: concise ≤ 50 words, never read code/paths/output aloud,
  lead with the decision-relevant point, confirm destructive actions first, in an
  advisor — not butler — register). It is injected into `Brain._base_argv` as
  `--append-system-prompt`, so it rides on **both** `Brain.ask` and `Brain.stream`
  (the path the loop uses). The module also owns the **pure** G3.2 metric
  (`evaluate_persona` → `PersonaReport`): over a set of replies it reports the
  fraction within the 50-word cap and the count still leaking a code fence, both
  measured on the *speakable* text (`extract_speakable` first) — a surviving
  (unclosed) fence is flagged as code reaching TTS. The metric is 100%-covered and
  CI-tested over committed exemplars; it cannot prove Claude *obeys* the prompt, so
  `scripts/eval_persona.py` runs 20 **neutral** factual prompts through the real
  `claude` (injected runner; fresh context-free session each) and records the
  distribution. **Live result (20 prompts, 2026-05-25): 100% (20/20) ≤ 50 words,
  0 code leaked — G3.2 PASS** (full attribution in the Phase 3 doc Outcomes). The
  recorded set is gitignored, so `test_persona_eval.py`'s live assertion skips in
  CI while the metric and flag-wiring tests stay green.
- Phase 3 barge-in (G3.1): the `SPEAKING` state is now cancellable. The `Speaker`
  protocol gains `stop()` (real impl `sd.stop()`) so the consumer can abort a clip
  *mid-utterance*, and `jarvis.vad` gains `OnsetDetector` — the rising-edge
  counterpart to G2.2's trailing-silence `Endpointer`, reusing the same injected
  Silero `Detector` seam but firing on the first frame at/above `vad_threshold`
  (latched, one onset per utterance). `jarvis.loop` runs an injected onset watcher
  on the hot mic during `SPEAKING`; on speech onset it sets a cancel flag and
  aborts playback, the consumer stops, and the producer breaks its token loop and
  **closes the generator** — in `Brain.stream` that `GeneratorExit` terminates the
  `claude` child, so no further sentences are spoken — then the machine returns to
  `LISTENING` (not `IDLE`). Barge-in latency is bounded by `stop()` rather than the
  sentence length; measured onset → playback-halted ≤ 300 ms off an injected clock
  (`tests/test_barge_in.py`), with the live watcher (`build_default_barge_in_watcher`)
  wired into `jarvis run`. The mic-hears-itself echo case (no AEC yet) is noted as
  a known limitation in the Phase 3 doc Outcomes.
- Phase 2 time-to-first-audio measurement (G2.3): `scripts/bench_latency.py`
  gains a `--mode ttfa` path that composes the real first-audio cascade behind
  injected per-stage timers (the `vad_silence_ms` hangover → whisper.cpp STT →
  `claude -p` first-token → Kokoro's first audio chunk) and sums them per run —
  the cascade to first audio is strictly sequential, so TTFA is the sum of the
  stages, not their max (the G2.4 overlap shortens *total* turn time, not
  time-to-*first*-audio). `claude` is spawned per run, matching real per-turn
  behaviour (ADR-0003). Measured live over 20 runs: **p50 6.07 s, p95 7.75 s** —
  full per-stage attribution in the Phase 2 doc Outcomes. The stage timers are
  injected, so `tests/test_bench_ttfa.py` exercises the timing/aggregation with
  fakes (no whisper, claude, or Kokoro in CI). `--no-hangover` reframes the
  metric as endpoint-fire → first audio.
- Phase 2 G2.3 target **renegotiated** against the measured distribution
  (recorded in the Phase 2 doc): the original ≤ 1.5 s p50 / ≤ 2.5 s p95 is
  unreachable under the spawn-per-turn model and is replaced by a measured
  spawn-per-turn baseline (≤ 6.5 s p50 / ≤ 8 s p95) plus a forward target
  (≤ 2.0 s p50) gated on a persistent-brain re-architecture (the ADR-0003
  revisit). See Outcomes for the three options and trade-offs.
- Phase 2 VAD endpointing (G2.2): new `jarvis.vad` module decides end-of-speech
  in the LISTENING state. It mirrors `jarvis.wakeword` — the native Silero VAD
  model is wrapped as an injected `Detector` callable (one 512-sample / 32 ms
  PCM16 frame → speech probability in [0, 1]) and `Endpointer` owns a pure rolling
  trailing-silence accumulator that fires once sub-threshold frames sum to
  `vad_silence_ms` *after* speech is heard (leading silence never fires; the
  endpoint latches, so a long pause yields one endpoint, not one per silent
  frame). Reuses the existing `vad_threshold` (0.5) and `vad_silence_ms` (700)
  config keys. The Silero backend (`SileroDetector`, loaded from the bundled
  `silero-vad` wheel — no `torch.hub` download) is a native shim excluded from
  coverage; `silero-vad` is added to the `voice` extra. `jarvis.vad` is at 100%
  coverage.
- Phase 2 G2.2 verification: new `scripts/bench_latency.py` feeds a synthetic
  frame stream (speech frames then trailing silence) through the endpointer and
  times the decision compute from the last speech frame to the endpoint firing.
  Measured live against the real Silero model: **p50 1.4 ms, p95 1.5 ms over 30
  runs (target ≤ 300 ms p50)** — full distribution in the Phase 2 doc Outcomes.
  The detector and clock are injected, so `tests/test_bench_latency.py` exercises
  the timing/aggregation with fakes (no torch in CI).
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

- The `jarvis.audio.Speaker` protocol now requires a `stop()` method (alongside
  `play`) so playback can be aborted mid-clip for barge-in; `jarvis.loop.VoiceLoop`
  gains optional `watch_barge_in` and `clock` fields and `Turn` gains `barged_in` /
  `barge_in_latency_s`. All additions are backward-compatible defaults — a loop
  built without a watcher behaves exactly as in Phase 2.
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
