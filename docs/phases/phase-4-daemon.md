# Phase 4 — Daemon polish

- **Status:** In progress — the **pre-Phase 4 carryover (G4.0)** is now done:
  wake-phrase-gated barge-in + the audio-stream fix the Phase 3 demo surfaced
  (`phase-3-demo.md`) landed on 2026-05-26. The remaining daemon/release goals
  are still ahead.
- **Milestone:** Phase 4
- **Objective:** Make Jarvis a dependable always-on background service and cut
  the first release.

## Pre-Phase 4 action — wake-phrase-gated barge-in (G4.0) · _Done 2026-05-26_

Carried over from the Phase 3 Definition-of-Done demo (`phase-3-demo.md`). The
shipped barge-in fires on **any speech onset** (Silero VAD via `OnsetDetector`),
which cannot tell *who* is speaking — Jarvis's own voice, a colleague, or a TV all
trip it. The live demo also hit a CoreAudio `-50` from opening a second input stream
concurrent with Kokoro's output, producing a deterministic ~107 ms self-trigger.

**Decision (Ty, 2026-05-25):** gate barge-in on the **wake phrase** instead of raw
speech. Only "hey jarvis" interrupts playback; ambient and third-party speech do
not. This reuses openWakeWord (already in the stack, `jarvis.wakeword`, tuned to
`wake_threshold` 0.9 in G2.1). Trade-off accepted: you say the phrase to interrupt
rather than just talking over him — the right call for an unattended home/office
service. ("Just talk to interrupt" *and* robustness would need speaker
verification — heavier, deferred.)

**Delivered:**

1. **Prerequisite — fix the concurrent input/output stream.** During `SPEAKING` the
   watcher opens a 16 kHz input stream while Kokoro plays 24 kHz output →
   `||PaMacCore (AUHAL)|| err='-50'` and garbage mic frames. A wake-word gate is
   useless if the mic feeds it garbage. Deliver valid frames during playback: prefer
   **one persistent input stream** (opened once, shared by `LISTENING` capture and the
   `SPEAKING` watcher) or a single duplex `sd.Stream`; handle the sample-rate mismatch
   (open at device-native rate, resample to the detector's rate). **Instrument first:**
   log per-frame RMS + score + whether `read()` errored during `SPEAKING`, run live
   once, confirm frames are valid before tuning anything.
2. **Wake-phrase gate.** Rewrite `loop.build_default_barge_in_watcher` to feed the hot
   mic to the openWakeWord detector (reuse `jarvis.wakeword`) instead of the Silero
   `OnsetDetector`; on "hey jarvis" detection, call `on_onset` (which stops playback +
   cancels the in-flight `claude` stream + returns to `LISTENING` — the
   `_think_and_speak` logic is correct and stays **unchanged**; only the watcher
   implementation changes). Reuse `wake_threshold`; if `SPEAKING` needs different
   sensitivity, add a **separate** config key (e.g. `barge_in_wake_threshold`), don't
   overload `wake_threshold`.

**Traps:**

- The `-50`/stream fix is a **prerequisite** — gating on a broken stream can't hear
  the phrase. Do it first (or together) and prove valid frames.
- The latency metric **reframes**: G3.1's "≤ 300 ms after speech onset" becomes
  "≤ 300 ms after the wake phrase is *recognized*." The phrase takes ~0.8 s to utter —
  expected cost of robustness, not a regression.
- "Jarvis, stop" is **not** a trained openWakeWord model — reuse "hey jarvis" as the
  interrupt trigger; a custom "jarvis stop" phrase model is future work.
- Keep the `BargeInWatcher` seam and `_think_and_speak` intact; the watcher stays a
  `# pragma: no cover` hardware shim, with decision logic behind injected seams so it
  is unit-tested (ADR-0005). The Silero `OnsetDetector` may become unused on the
  barge-in path — remove it there only after checking it isn't needed for endpointing.
- Residual edge: Jarvis could occasionally say "hey jarvis" mid-reply and
  self-interrupt; acceptable, note it.

**Write-first tests (landed):** `test_barge_in_watcher_fires_on_wake_phrase` (injected frames
the injected detector scores as the wake phrase → `on_onset` once; returns on stop);
`test_barge_in_watcher_ignores_non_wake_speech` (speech-like non-phrase frames do
**not** fire — the whole point). Keep `tests/test_barge_in.py` loop contracts green.

## In scope

- A macOS **launchd** service (`launchctl`) that runs Jarvis in the background,
  auto-starts at login, and restarts on crash; with documented install/uninstall.
- Startup status chimes (ready / listening / thinking) for eyes-free feedback.
- Confirm the whole runtime is **config-driven** — voice, model, and permission
  mode are changeable via `.env` with no code edits.
- Cut **v1.0.0**: finalize `CHANGELOG.md`, tag `v1.0.0`, let the release
  workflow build and publish.

## Out of scope

- Cloud-provider voice backends, multi-user, GUI — future work, not v1.

## Measurable goals

| ID | Metric | Target | Verification |
|----|--------|--------|--------------|
| G4.0 | Pre-Phase 4: wake-phrase-gated barge-in | Only "hey jarvis" interrupts playback; ambient/other-voice/self speech does not; no CoreAudio `-50` during SPEAKING | `tests/test_barge_in.py` + live shared-stream probe |
| G4.1 | Service lifecycle | Installs, auto-starts, survives logout/login; clean uninstall | manual + `tests/test_service_unit.py` |
| G4.2 | Cold start | Boot → ready-for-wake-word ≤ 10 s | `scripts/bench_latency.py` |
| G4.3 | Stability soak | 1-hour idle: 0 crashes, memory growth ≤ 50 MB | soak run recorded |
| G4.4 | Config-driven | Voice/model/permission mode changeable via `.env` only | `tests/test_config_drives_runtime.py` |
| G4.5 | Release | `v1.0.0` tagged; CHANGELOG finalized; coverage ≥ 85% | release workflow run |

## Test plan (write first)

- `test_service_plist_is_valid` — the generated launchd plist parses and points
  at the correct entry point.
- `test_config_drives_runtime` — changing a setting (e.g. `JARVIS_TTS_VOICE`)
  changes the component the loop instantiates, with no code change.

## Definition of Done

All goals met; coverage ≥ 85%; `v1.0.0` released with a complete changelog; the
service runs unattended through a soak test.

## Outcomes

### G4.0 — wake-phrase-gated barge-in · _Done_

The live barge-in path no longer opens a second input stream during
`SPEAKING`. `jarvis.audio` now has a persistent `SoundDeviceMicrophone`
backed by one long-lived `sounddevice.RawInputStream`; `jarvis.cli.run`
wires that same mic into both LISTENING capture and
`loop.build_default_barge_in_watcher`. This removes the Phase 3 topology that
triggered `||PaMacCore (AUHAL)|| err='-50'` by opening a fresh input stream
while Kokoro output was already active.

The watcher itself was rewritten around `jarvis.wakeword` instead of
`jarvis.vad.OnsetDetector`. `loop.build_wake_phrase_barge_in_watcher`
is the injected, unit-tested seam: it consumes mic frames, resamples them to
openWakeWord's required 16 kHz PCM16 geometry when the configured input rate
differs, logs per-frame RMS + score + `read_error=0/1` when
`JARVIS_LOG_LEVEL=DEBUG`, and fires `on_onset` only when the wake score crosses
`wake_threshold`. `_think_and_speak` and its cancellation semantics stayed
unchanged.

**Verification:**

- New write-first watcher tests landed in `tests/test_barge_in.py`:
  `test_barge_in_watcher_fires_on_wake_phrase` and
  `test_barge_in_watcher_ignores_non_wake_speech`.
- Existing barge-in loop contracts stayed green (`tests/test_barge_in.py`),
  and the full suite passed at **205 passed**, **96% coverage**.
- Live shared-stream probe on 2026-05-26: with one 16 kHz persistent mic open
  and a 24 kHz silent playback clip running through the speaker, the watcher
  successfully read **12/12** frames during playback, reported
  `errors=[]`, `avg_rms=3.7e-05`, `max_rms=8.4e-05`, and
  `max_score=6.1e-05` from the real openWakeWord detector. No CoreAudio `-50`
  was emitted.
- Live self-speech probe on 2026-05-26: with the same shared mic path open,
  Kokoro voiced `"Diagnostics are nominal, sir."` through the speakers while
  the real openWakeWord detector watched the hot mic. The watcher read
  **43** frames with `errors=[]`, `avg_rms=0.002111`, `max_rms=0.006024`, and
  `max_score=0.001335` — well below `wake_threshold` 0.9 and with no
  CoreAudio `-50`.

Residual edge remains the same one called out in planning: if Jarvis ever
literally says `"hey jarvis"` in a reply, he can still interrupt himself. That
is acceptable for now; a custom stop phrase model is future work.
