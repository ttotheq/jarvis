# Phase 4 — Daemon polish

- **Status:** In progress — **G4.0** (wake-phrase barge-in), **G4.1** (launchd
  service lifecycle, ADR-0006), the **always-on wake-word runtime** (the entry
  point the service runs, verified live), and **G4.6** (smooth streaming
  playback) are done as of 2026-05-26. `jarvis run` now defaults to a headless
  wake-word cascade that plays multi-sentence replies gaplessly. The remaining
  daemon/release goals (G4.2 cold start, G4.3 soak, G4.4 config-driven, G4.5
  release) are still ahead; G4.2/G4.3 now have the always-on loop to measure.
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
| G4.6 | Smooth streaming playback | Multi-sentence replies play gaplessly — no inter-sentence gaps, boundary clicks, or clipped sentence-starts | `tests/test_playback_pipeline.py` + live multi-sentence check |

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

### G4.1 — service lifecycle · _Done 2026-05-26_

Jarvis now installs as a macOS **launchd LaunchAgent** (ADR-0006). The new
`jarvis.service` module owns plist generation and lifecycle, and `jarvis.cli`
exposes `jarvis service install | uninstall | status`.

- **Config-driven, no baked-in paths.** `build_plist_spec` resolves the entry
  point at install time to `[sys.executable, "-m", "jarvis", "run"]`, the working
  directory to the resolved project root, and `EnvironmentVariables.PATH` to the
  install-time `PATH` so launchd's minimal environment still finds `claude` and
  the native binaries. The plist label and log directory are config keys
  (`JARVIS_SERVICE_LABEL` default `com.jarvis.voice`; `JARVIS_SERVICE_LOG_DIR`
  default `~/Library/Logs/jarvis`).
- **Auto-start + crash-restart.** The plist sets `RunAtLoad: true` and
  `KeepAlive: {Crashed: true}` — start at login, restart on crash, but **not** on
  a clean exit (so the harness exiting for lack of a TTY does not thrash-restart
  while the always-on entry point is still being wired).
- **Modern launchctl verbs.** `install` writes the plist, creates the log dir,
  best-effort boots out any stale registration, then
  `launchctl bootstrap gui/<uid> <plist>`. `uninstall` runs
  `bootout gui/<uid>/<label>` and removes the plist (idempotent). `status` runs
  `print gui/<uid>/<label>` and reports installed/loaded state.

**Verification:**

- Write-first unit tests landed in `tests/test_service_unit.py`, including the
  named acceptance test `test_service_plist_is_valid` (the rendered plist
  round-trips through `plistlib` and `ProgramArguments` points at the resolved
  interpreter + `-m jarvis run`). Plist generation and install/uninstall/status
  orchestration are exercised with an injected fake `Runner` and a `tmp_path`
  HOME; `jarvis.service` is at 100% coverage with only the `launchctl`-spawning
  `default_runner` excluded. Full suite **219 passed, 96% coverage**.
- **Live install → status → uninstall round-trip (macOS Tahoe 26.5, 2026-05-26):**
  `jarvis service install` bootstrapped the agent and `plutil -lint` reported the
  generated plist `OK`. `launchctl print gui/501/com.jarvis.voice` showed
  `state = running`, `properties = runatload`, `program =
  /Users/ttotheq/projects/jarvis/.venv/bin/python3`, `arguments = … -m jarvis
  run`, and the configured stdout/stderr log paths — i.e. the venv interpreter
  was resolved at install time, not hard-coded. `jarvis service status` returned
  `loaded (running)` (exit 0). `jarvis service uninstall` booted it out and
  removed the plist; a follow-up `status` returned `not loaded` (exit 1), the
  plist was gone, and `launchctl print` no longer knew the service. A second
  `uninstall` was a clean no-op (idempotent).
- **Known gap (expected):** `jarvis run` is still the Enter-gated developer
  harness, so under launchd (no TTY) the launched process loaded the voice stack
  and then exited (`Aborted.` in the err log) rather than holding a session. With
  `KeepAlive {Crashed: true}` that clean exit correctly did **not** trigger a
  relaunch. Wiring the always-on wake-word loop as the launchd entry point is the
  remaining Phase 4 work; G4.1 delivers the service mechanism it plugs into.
- **Manual leg for Ty:** the full **logout/login survival** check cannot be
  performed autonomously. The mechanism is in place and proven loaded with
  `RunAtLoad: true`; confirm survival by `jarvis service install`, log out and
  back in, then `jarvis service status` (expect `loaded`).

### Always-on wake-word runtime · _Done 2026-05-26_

The entry point the launchd service runs. Previously `jarvis run` was the
Enter-gated developer harness and exited under launchd for lack of a TTY (the
G4.1 known gap). `jarvis run` now defaults to a **headless wake-word cascade**:
it parks at `IDLE` until "hey jarvis", endpoints the utterance with Silero VAD,
streams Claude's reply through TTS, and returns to `IDLE` — no keyboard, so it
self-sustains under the service. This unblocks **G4.2** (cold start) and **G4.3**
(soak), which need a loop that stays up.

- **Two pure primitives in `jarvis.loop`** (injected, fully unit-tested without a
  mic or native models): `wait_for_wake_phrase` (the IDLE gate — resamples and
  coerces mic frames to openWakeWord's 80 ms geometry, blocks until the score
  crosses `wake_threshold`, reusing the G4.0 frame path via a shared
  `_to_wakeword_frame` helper) and `capture_until_endpoint` (the LISTENING
  capture — accumulates the `Clip` at the capture rate while re-chunking a 16 kHz
  copy into Silero's exact 512-sample frames, **carrying the remainder across mic
  reads** since the 1280-sample mic block is not a multiple of the 512-sample VAD
  frame, and stopping on the VAD endpoint or a `listen_max_seconds` safety cap).
- **`VoiceLoop` gained one additive seam** `wait_for_wake`: when set, a turn opens
  `IDLE → LISTENING` (matching the architecture state machine); when `None`, the
  developer-harness behaviour is byte-for-byte unchanged (same optional-seam
  pattern as `watch_barge_in`). `_think_and_speak`, brain, TTS, and barge-in are
  untouched.
- **Config-driven:** new `JARVIS_RUN_MODE` (`wake_word` default | `push_to_talk` |
  `timed`) and `JARVIS_LISTEN_MAX_SECONDS`. The persistent shared mic feeds the
  IDLE wake-wait, the LISTENING capture, and the SPEAKING barge-in watcher alike.

**Verification:**

- Write-first tests landed in `tests/test_always_on.py` (wake gate fires/ignores,
  the cross-read VAD re-chunk carry — 5 frames vs the 4 a naive split would drop,
  the duration cap, resampling, and reset-between-turns) and `tests/test_loop.py`
  (the `IDLE → LISTENING` contract with the seam, and back-compat without it),
  plus `run_mode` parsing in `tests/test_config.py`. Full suite **235 passed, 97%
  coverage**; `jarvis.loop` at 95% (only the native builders + pre-existing
  barge-in live branches excluded).
- **Live mic check — PASSED (2026-05-26).** Across four real-mic runs the loop
  woke on "hey jarvis" (scores 0.95–0.99), VAD-endpointed the utterance,
  transcribed it accurately ("What is two plus two?", "What is 4 plus 4?", the
  Bluetooth-profiles prompt), replied in-character via real `claude` + Kokoro
  ("Four, sir.", "8.", a 3-sentence answer), and exited cleanly — confirming
  `IDLE → LISTENING → THINKING → SPEAKING → IDLE` end to end. The native
  `build_wait_for_wake` / `build_vad_record_turn` shims stay `# pragma: no cover`.
- **Audio-routing finding (AirPods):** because the loop holds the mic open
  continuously, selecting AirPods as input forces them from A2DP to the HFP
  headset profile, muffling TTS output. Mitigation is a **device split** — input
  on the built-in mic (`JARVIS_INPUT_DEVICE="MacBook Air Microphone"`), output on
  the AirPods (stays A2DP). This also removes the mic-hears-itself self-trigger
  risk, since playback is then in-ear. (Led directly to G4.6 below.)

**Deferred to a focused follow-up:** status chimes (ready/listening/thinking) — a
Phase 4 in-scope item — ride cleanly on the new `on_state` `IDLE`/`LISTENING`
transitions and are kept out of this change to keep it tight.

### G4.6 — smooth streaming playback · _Done 2026-05-26_

The live always-on check surfaced choppy multi-sentence playback: replies clicked
at sentence boundaries, swallowed sentence-starts, and stalled with gaps. Root
cause was two stacked problems — playback was a fresh `sd.play` per sentence (so
Bluetooth A2DP renegotiated at every boundary → clicks + clipped starts), and
synthesis was serialized with playback in one thread (so each new sentence's
render time was dead air). One architectural change fixes both.

- **Persistent output stream.** New `jarvis.audio.SoundDeviceStreamingSpeaker`
  holds a single `RawOutputStream` open across the session and writes each clip
  into it, so back-to-back sentences are one continuous stream (no per-sentence
  renegotiation, no clipped starts). `wait()` drains buffered audio at end of turn
  (PortAudio `stop`); `stop()` aborts immediately for barge-in (PortAudio
  `abort`); `close()` tears it down. `build_default_speaker` now returns it, and
  `jarvis.cli.run` closes it on exit alongside the persistent mic. The dead
  per-clip `SoundDeviceSpeaker` was removed.
- **Synthesis pipelined ahead of playback.** `jarvis.loop._think_and_speak` is now
  a three-stage pipeline — `tokens → sentences → audio → speaker` — with a new
  synth thread rendering sentence N+1 while N plays, so there is no inter-sentence
  gap. On a clean finish the consumer drains the speaker (optional `wait`); on
  barge-in the cancel flag stops every stage and the speaker is aborted.

**Verification:**

- Write-first tests in `tests/test_playback_pipeline.py`: synthesis runs ahead of
  playback, the speaker is drained on a clean finish (and not when nothing is
  spoken), it is aborted — not drained — on barge-in, and a synth-stage error
  propagates instead of hanging. Existing `test_loop_streaming.py` /
  `test_barge_in.py` / `test_loop.py` stayed green (the G2.4 overlap and G3.1
  barge-in/latency contracts are unchanged). Full suite **240 passed, 97%
  coverage**; the real streaming speaker is the only `# pragma: no cover` piece.
- **Live (2026-05-26):** the same three-sentence Bluetooth-profiles prompt that
  previously clicked and stalled now plays **gaplessly** on the built-in speakers
  (input on the built-in mic, wake score 0.974). The persistent single stream also
  removes the per-sentence A2DP renegotiation that caused the AirPods clicks.

**Note (separate, persona tuning):** the 3-sentence replies ran ~90–95 words, over
the persona's ≤ 50-word target — a G3.2 prompt-tuning item, independent of playback.
