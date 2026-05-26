# Delivery plan: five phases

Jarvis is built in five phases. Each phase has **measurable acceptance goals**
with a stable ID, a metric, a target, and a verification method. This structure
is deliberate: every goal is written so it can be lifted directly into a tracked
Claude Code goal in a later iteration, and so "done" is never a matter of
opinion.

## How a phase runs (the iterative loop)

1. Open a GitHub issue per goal using the **Phase task** template; group them
   under the phase's milestone.
2. **Write the failing test(s) first** (TDD) — listed in each phase's
   "Test plan".
3. Implement until the test passes and the goal's target metric is met.
4. Update the phase doc's **Status** and **Outcomes**, every repo status surface
   that mentions the phase (`README.md`, this overview, architecture notes when
   they changed), the `CHANGELOG.md`, and any ADR/`.env.example` in the same PR.
5. Merge only on green CI.

## Status legend

`Not started` · `In progress` · `Done`

## Measurable goals at a glance

Each goal: **ID — metric — target — verification**. IDs are stable; treat them
as the unit of tracking.

### Phase 0 — Spike & de-risk · _Done_
| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G0.1 | Local components available | `jarvis doctor` reports openWakeWord, whisper.cpp, Kokoro, PortAudio all OK → exit 0 | `jarvis doctor` + test with fakes |
| G0.2 | Claude headless round-trip | Median time-to-first-token recorded over ≥ 10 `claude -p` calls (baseline captured) | `scripts/bench_brain.py` output committed to Outcomes |
| G0.3 | Voice chosen | One British male Kokoro voice selected, sample recorded | ADR/Outcomes entry + audio sample |
| G0.4 | CI green on `main` | Pipeline passes; coverage ≥ 80% | GitHub Actions run |

### Phase 1 — Walking skeleton (push-to-talk) · _Done_
| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G1.1 | End-to-end reliability | ≥ 5 consecutive push-to-talk exchanges, no crash | manual script + recorded session |
| G1.2 | STT accuracy | Word error rate ≤ 10% on a 20-utterance dev set | `tests/test_stt_accuracy.py` over fixtures |
| G1.3 | Speakable-text extraction | 100% of code/tool blocks stripped on fixtures | `tests/test_brain_extraction.py` |
| G1.4 | Session continuity | Turn 3 correctly references turn 1 via `--resume` | `tests/test_brain_session.py` |
| G1.5 | Coverage | ≥ 80% | CI |

### Phase 2 — Wake word + streaming · _Done_
| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G2.1 | Wake-word detection | True-accept ≥ 95% / 20 utterances; false-accept ≤ 1 per 30 min ambient | `tests/test_wakeword.py` on audio fixtures + soak |
| G2.2 | Endpoint latency | End-of-speech → STT start ≤ 300 ms p50 | `scripts/bench_latency.py` |
| G2.3 | Time-to-first-audio | **Renegotiated** (orig. ≤ 1.5 s p50): spawn-per-turn ≤ 6.5 s p50 / ≤ 8 s p95 (measured 6.07/7.75); ≤ 2.0 s p50 forward target on a persistent brain | `scripts/bench_latency.py --mode ttfa` over 20 runs |
| G2.4 | Streaming overlap | First sentence spoken before full Claude response completes | `tests/test_loop_streaming.py` |
| G2.5 | Coverage | ≥ 80% | CI |

### Phase 3 — Jarvis feel · _Done_
| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G3.1 | Barge-in latency | Playback stops ≤ 300 ms after user speech onset; in-flight task cancelled | `tests/test_barge_in.py` |
| G3.2 | Spoken conciseness | ≥ 90% of replies ≤ 50 words on a 20-prompt eval; 0 code read aloud | `tests/test_persona_eval.py` |
| G3.3 | Permission gating | 100% of destructive tool calls trigger spoken confirmation before running | `tests/test_permission_gate.py` |
| G3.4 | Coverage | ≥ 85% | CI |

### Phase 4 — Daemon polish · _In progress (G4.0 done)_
| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G4.0 | Pre-Phase 4: wake-phrase-gated barge-in (carryover) | Only "hey jarvis" interrupts; ambient/other-voice/self does not; no CoreAudio `-50` during SPEAKING | `tests/test_barge_in.py` + live shared-stream probe |
| G4.1 | Service lifecycle | launchd service installs, auto-starts, survives logout/login; clean uninstall | manual + `tests/test_service_unit.py` |
| G4.2 | Cold start | Boot → ready-for-wake-word ≤ 10 s | `scripts/bench_latency.py` |
| G4.3 | Stability soak | 1-hour idle: 0 crashes, memory growth ≤ 50 MB | soak run, recorded |
| G4.4 | Config-driven | Voice/model/permission mode changeable via `.env` only, no code edits | `tests/test_config_drives_runtime.py` |
| G4.5 | Release | `v1.0.0` tagged; CHANGELOG finalized; coverage ≥ 85% | release workflow run |

See each phase file for scope, deliverables, the write-first test plan, and the
Outcomes section (filled in as work lands).
