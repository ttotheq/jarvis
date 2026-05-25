# Phase 0 — Spike & de-risk

- **Status:** Done — `jarvis doctor` and `scripts/bench_brain.py` landed with
  tests and CI green (G0.1, G0.2, G0.4). The voice stack was installed during the
  Phase 1 setup step (`jarvis doctor` now exits 0) and the G0.3 audition picked
  `bm_george`; see the updated Outcomes below.
- **Milestone:** Phase 0
- **Objective:** Prove the local voice stack installs and runs on this Mac, and
  that the Claude headless brain round-trips, before committing to the build.

## In scope

- A `jarvis doctor` command that checks the environment: PortAudio, whisper.cpp,
  Kokoro, openWakeWord importable/available; microphone permission granted;
  `claude` on PATH.
- A throwaway benchmark of `claude -p` round-trip latency.
- Listening to candidate British male Kokoro voices and choosing one.

## Out of scope

- Any part of the live voice loop (Phase 1+). Spike code is deliberately small
  and may be discarded once its question is answered.

## Deliverables

- `jarvis doctor` (in `jarvis.cli`) with unit tests using fakes.
- `scripts/bench_brain.py` — times `claude -p` time-to-first-token.
- Outcomes recorded below; chosen voice noted in `.env.example` default.

## Measurable goals

| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G0.1 | Local components available | `jarvis doctor` reports all four components OK → exit 0 | `jarvis doctor` + `tests/test_doctor.py` (fakes) |
| G0.2 | Claude round-trip | Median time-to-first-token over ≥ 10 `claude -p` calls recorded | `scripts/bench_brain.py` output in Outcomes |
| G0.3 | Voice chosen | One British male Kokoro voice selected + sample recorded | Outcomes entry |
| G0.4 | CI green on `main` | Pipeline passes; coverage ≥ 80% | GitHub Actions |

## Test plan (write first)

- `test_doctor_reports_missing_component` — doctor exits non-zero and names a
  missing dependency (injected fake reports absent).
- `test_doctor_all_present_exits_zero` — all fakes present → exit 0.

## Definition of Done

All four goals met; go/no-go decision and benchmark numbers recorded in
Outcomes; `make check` and CI green.

## Outcomes

### G0.1 — Local components available

`jarvis doctor` (in `jarvis.cli`, logic in `jarvis.doctor`) probes the four
voice-stack dependencies and exits non-zero naming any that are missing, exit 0
when all present. The stack was installed during the Phase 1 setup step
(`brew install portaudio whisper-cpp espeak-ng` + the `voice` extra wheels), so
it now reports:

```
Jarvis environment check:
  [OK ] PortAudio     audio backend available
  [OK ] whisper.cpp   found (whisper-cli)
  [OK ] openWakeWord  importable
  [OK ] Kokoro        importable

All voice-stack dependencies present.
```

The all-present (exit 0) and any-missing (exit 1) paths are also covered by
`tests/test_doctor.py` with injected fake probes, so the gate stays verified in
CI without the native libraries.

### G0.2 — Claude round-trip (time-to-first-token)

`scripts/bench_brain.py` spawns `claude -p ... --output-format stream-json
--include-partial-messages` and times the interval from launch to the first
line of **model** output (the `system`/`init` event is skipped — timing to it
would measure process startup, not the brain responding).

Live result, 10 runs, prompt "Reply with exactly one word: ready":

| Metric | Value |
|--------|-------|
| Median | **2796 ms** |
| Mean   | 3017 ms |
| Min    | 1895 ms |
| Max    | 4418 ms |

Interpretation: ~2.8 s to first token is on the slow side of conversational but
acceptable as a worst case, because TTS streams the reply as it arrives — the
user hears speech shortly after the first token, not after the full response.
The subprocess is fully injected, so `tests/test_bench_brain.py` exercises the
timing and aggregation logic with a fake and never makes a network call.

### G0.3 — Voice chosen

**Chosen: `bm_george`** (British male). With Kokoro installed, audition samples
for `bm_george` / `bm_lewis` / `bm_fable` were synthesized through `jarvis.tts`
to `samples/voice-audition/*.wav` (the same line in each voice) and auditioned
on this Mac; `bm_george` was confirmed as the default. It remains the default in
`.env.example` / `jarvis.config` and is revisitable via `JARVIS_TTS_VOICE` with
no code change. **G0.3 met.**

### G0.4 — CI green

`make check` (ruff lint + format, mypy strict, pytest) passes locally at 100%
coverage; CI on the PR is green (see the PR's checks).

### Go/no-go

**Go.** The two questions the spike existed to answer are settled: the
environment self-check works and is test-covered, and `claude -p`
time-to-first-token (~2.8 s median) is within the budget that streaming TTS can
mask. Native voice-stack install and the voice audition move into Phase 1.

### Surprises

- Timing to the first *stdout line* (≈420 ms) measures only `claude` startup
  plus the session-init event — not the model. Skipping the `system` event was
  necessary to get an honest time-to-first-token (≈2.8 s).
