# Phase 0 — Spike & de-risk

- **Status:** Not started
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

_To be filled in as the phase completes (benchmark numbers, chosen voice,
go/no-go decision, surprises)._
