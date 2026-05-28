# Phase 4 — Daemon polish

- **Status:** Done (2026-05-27). **G4.0** (wake-phrase barge-in), **G4.1** (launchd
  service lifecycle, ADR-0006), the **always-on wake-word runtime** (the entry
  point the service runs, verified live), **G4.6** (smooth streaming playback),
  **G4.2** (cold start), **G4.3** (stability soak), **G4.4** (config-driven
  runtime), and **G4.5** (the v1.0.0 release) are all complete. `jarvis run`
  defaults to a headless wake-word cascade that plays multi-sentence replies
  gaplessly, is ready for "hey jarvis" in ~1 s (the heavy loads warm in the
  background), held flat memory with zero crashes over a 1-hour idle soak, and is
  fully retargetable via `.env` (voice, Claude model, STT model, permission mode)
  with no code edit. **`v1.0.0` is released** — this closes Phase 4 and the
  five-phase plan.
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
| G4.2 | Cold start | Boot → ready-for-wake-word ≤ 10 s | `scripts/bench_latency.py --mode cold_start` |
| G4.3 | Stability soak | 1-hour idle: 0 crashes, memory growth ≤ 50 MB | `scripts/soak_idle.py --minutes 60` + `tests/test_soak_idle.py` |
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
- **Known gap at G4.1 — since closed.** At the time of this G4.1 test `jarvis run`
  was still the Enter-gated developer harness, so under launchd (no TTY) the
  launched process loaded the voice stack and then exited (`Aborted.` in the err
  log) rather than holding a session. With `KeepAlive {Crashed: true}` that clean
  exit correctly did **not** trigger a relaunch. G4.1 delivered the service
  mechanism; the **always-on wake-word runtime** (next section) then became the
  headless entry point it runs, closing this gap.
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
- **Audio-routing note (AirPods) — initial finding NOT reproduced.** One early
  single-sentence run muffled on AirPods, first attributed to the always-open mic
  forcing the A2DP→HFP headset profile. A later test with AirPods on **both** input
  and output (no device overrides, after G4.6) came back **crystal clear and
  gapless**, so the muffling did not reproduce — most likely transient Bluetooth
  settling, or the old per-clip `sd.play` churn that G4.6's persistent stream
  resolved. A **device split** (`JARVIS_INPUT_DEVICE="MacBook Air Microphone"`,
  output on the AirPods) remains available as a fallback if muffling ever recurs,
  and also avoids the mic-hears-itself self-trigger, but it is **not required**.
  (Unverified: which mic macOS used on the no-override run.)

**Status chimes — landed in a focused post-1.0.0 follow-up** (see the *Status
chimes* Outcomes section below). They ride on the `on_state` seam as planned,
though the final mapping settled on `LISTENING` + `THINKING` (with a one-time
`READY` cue at startup) rather than the literal IDLE/LISTENING the deferral note
suggested — IDLE re-enters twice between turns and would double-fire.

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
  (input on the built-in mic, wake score 0.974). A later multi-sentence run on
  **AirPods** (both input and output, no overrides) was likewise **crystal clear
  and gapless** — confirming the persistent single stream removes the per-sentence
  A2DP renegotiation that caused the earlier Bluetooth clicks.

**Note (separate, persona tuning):** the 3-sentence replies ran ~90–95 words, over
the persona's ≤ 50-word target — a G3.2 prompt-tuning item, independent of playback.

### G4.2 — cold start · _Done 2026-05-26_

Boot → ready-for-wake-word, measured by `scripts/bench_latency.py --mode
cold_start`. "Ready" is defined precisely as the moment the always-on loop can
block in `wait_for_wake` on a working mic and hear "hey jarvis" — **not**
full-cascade warm.

**Measured first (the goal said measure before fixing).** The new cold-start mode
times each component's real construction. In the `wake_word` runtime only the
persistent mic and the openWakeWord listener gate readiness; the Silero VAD
endpointer, Kokoro synthesizer, and the barge-in watcher's second openWakeWord
model are needed only *after* a wake. Live (warm cache, AirPods absent):

- `ready_s` (mic + openWakeWord wake gate) = **0.88 s**
- deferred: Silero VAD 0.59 s, **Kokoro 3.38 s**, barge-in openWakeWord 0.12 s
- full warm cost if loaded eagerly = **4.97 s** (≈5.6 s including interpreter +
  import boot)

So warm steady-state was already under the 10 s target even fully eager; the
earlier "~15–20 s observed" was almost certainly first-run Hugging Face model
downloads and cold-cache reads, not steady state. But `cli.run` built everything
eagerly *before* declaring readiness, so production's true ready was ~5.6 s (and
the dominant `import torch` + Kokoro load is exactly the part that balloons on a
cold cache / fresh boot). The fix gates readiness on the wake path alone — which
carries no torch at all — and warms the rest in the background.

**Delivered.**

- `jarvis.loop.Lazy[T]` — a thread-safe build-once-on-first-use cell — and
  `warm_in_background(*lazies)`, which builds them on a daemon thread.
  `jarvis.cli.run` now builds only the persistent mic and the openWakeWord wake
  gate eagerly, prints "Jarvis is ready", then warms the deferred components
  (Kokoro, the Silero VAD record-turn, the barge-in watcher) in the background.
  The `synthesize` / `record_turn` / `watch_barge_in` seams pull their backing
  object through `Lazy.get()`, so a turn that starts before warm-up finishes
  simply blocks on the lock instead of racing or double-building. `VoiceLoop` and
  the injected-seam pattern are unchanged — the deferral wraps the *builders*, not
  the loop.

**Verification:**

- Write-first tests: `tests/test_bench_cold_start.py` (readiness counts only the
  mic + wake listener; the deferred breakdown never inflates `ready_s`; each
  component built once; the runner aggregates readiness into a `LatencyResult`;
  the summary names the target, PASS/FAIL, and the deferred components) and
  `tests/test_warmup.py` (`Lazy` builds once and caches, is thread-safe under 8-way
  contention, and `warm_in_background` builds every cell on a daemon thread without
  rebuilding on later use). Full suite **254 passed, 97% coverage**; the live
  native wiring stays `# pragma: no cover`.
- **Live, real production path (2026-05-26).** `python -m jarvis run` (the launchd
  entry point) reached "Jarvis is ready" in **0.96 s** (warm cache). Only the
  onnxruntime/openWakeWord init logs before the ready line — the torch + Kokoro
  load logs now appear *after* it, on the background warm-up thread, confirming
  they are off the critical path. A separate off-thread build check warmed Kokoro
  + Silero + openWakeWord via `warm_in_background` in **4.78 s** with no error and
  produced valid 24 kHz audio from the deferred Kokoro synth — clearing the
  thread-safety risk of constructing the native models off the main thread.
- **Owed (interactive, needs Ty):** a spoken first-turn check on a true cold boot
  (reboot, cold file cache) — say "hey jarvis" within the first second and confirm
  the first capture/reply still works while warm-up is in flight. The mechanism is
  proven (ready fast, warm-up completes off-thread, first-use blocks correctly),
  but the cold-cache wall-clock and the live first turn are Ty's to confirm.

### G4.3 — stability soak · _Done 2026-05-27_

A 1-hour idle run of the always-on loop: target 0 crashes and memory growth ≤
50 MB. "Idle" is the daemon parked in `wait_for_wake_phrase`, scoring every mic
frame through openWakeWord — that per-frame hot path (resample + coerce +
`listener.score`) and openWakeWord's rolling buffer are the realistic place an
idle daemon would leak.

**Delivered.** New `scripts/soak_idle.py` follows the `soak_wakeword.py` pattern:
a pure `soak()` core drives an injected frame source + per-frame score function +
RSS sampler + clock, samples resident memory at intervals, tallies a scoring
exception as a crash (rather than propagating it, so the run completes and is
recorded), and reports growth from a **post-settle steady-state baseline** — so
G4.2's background warm-up (torch + Kokoro loading in the first seconds) is
excluded and only true steady-state drift counts. The live `main()`
(`# pragma: no cover`) wires the real openWakeWord listener and a `ps`-based RSS
sampler; `--source silence` paces synthetic silent frames at the real ~80 ms mic
cadence (deterministic, no false wakes), `--source mic` soaks real ambient.

**Verification:**

- Write-first tests in `tests/test_soak_idle.py`: growth is measured from the
  post-settle baseline (a launch RSS spike is excluded); flat memory → 0 growth;
  a rising series is detected; a scoring exception is counted as a crash and not
  raised; the injected clock bounds frames and elapsed; the `passed()` verdict
  fails on either over-budget growth or any crash. Full suite **261 passed, 97%
  coverage**; the live mic/model + `ps` sampler stay `# pragma: no cover`.
- **Live 1-hour idle soak (2026-05-27, `--source silence`):** **39,183 frames
  scored over 60.0 min, 0 crashes.** RSS baseline 218.6 MB → peak 218.7 MB → end
  106.2 MB, i.e. **growth −112.4 MB** (memory *fell* over the hour as the OS
  reclaimed openWakeWord/onnxruntime init buffers) — comfortably inside the ≤ 50 MB
  budget. **PASS.** `caffeinate -i` held the machine awake for the window;
  `pmset -g log` confirmed no system sleep during the run, so the 60 min is real
  awake runtime. The in-process soak exercises the idle scoring hot path + the
  openWakeWord buffer but does not hold Kokoro/Silero resident (idle never
  synthesizes) — valid for a leak signal since resident-unused memory is flat.
- **Optional (interactive, Ty):** a faithful live-mic full-`jarvis run` hour
  (`--source mic`, or just leaving the service running) would also exercise the
  warmed Kokoro/Silero resident footprint; not required for the goal, offered as a
  fidelity confirmation.

### G4.4 — config-driven runtime · _Done 2026-05-27_

The runtime is retargetable via `.env` alone — voice, Claude model, STT model, and
permission mode change with **no code edit**. This was mostly a verification goal:
three of the four knobs were already wired, and the work closed the one real gap
(the Claude model) and proved the whole set with a write-first acceptance test.

- **Already wired (proven, not changed):** `JARVIS_TTS_VOICE` / `JARVIS_TTS_SPEED`
  reach `KokoroSynthesizer.__call__` (passed straight to the Kokoro pipeline),
  `JARVIS_STT_MODEL` resolves the GGML path in `WhisperCppTranscriber`, and
  `JARVIS_PERMISSION_MODE` reaches `--permission-mode` on both `Brain` call shapes
  (`ask()` and `stream()`).
- **Gap closed — the Claude model.** There was no `claude_model` setting and the
  brain never passed `--model`, so the model rode the `claude` CLI default and was
  not `.env`-changeable. Added `claude_model: str | None = None` to
  `jarvis.config.Settings` (with a `.env.example` line) and wired it into
  `Brain._base_argv`: when set, it appends `--model <value>`; when `None` it is
  omitted so the CLI default holds. The default is **unset, not pinned** — pinning
  a model name that can later be retired would be a maintenance hazard, and the CLI
  default is the safe baseline. The flag is *appended* (not inserted at a fixed
  index) so it cannot disturb the `argv[3:3]` `--output-format` insert or the
  trailing `--resume`; every existing flag-relative argv test stayed green.
- **Scope (decided with Ty):** "model" means the **Claude reasoning model**. The
  whisper STT model is a separate, already-wired knob (`JARVIS_STT_MODEL`); G4.4
  did not rename or rescope it.

**Verification:**

- Write-first `tests/test_config_drives_runtime.py` (failed first on the missing
  `--model`/`claude_model`, then passed): a `JARVIS_`-prefixed env override flows to
  the seam the live component reads — `--permission-mode` and the new `--model` on
  both brain call shapes (with `--model` *absent* when unset, and the
  `--output-format`/`--resume` invariants preserved when present), `tts_voice` /
  `tts_speed` reaching the settings the Kokoro backend consumes, and `stt_model`
  reaching the constructed `WhisperCppTranscriber`. Per ADR-0005 the native
  backends are not spawned — the test asserts at the settings→argv / settings→object
  seam, so it runs in CI without the `voice` extra. The full suite stayed green at
  **85%+ coverage** with no new `# pragma: no cover`.
- No live leg: the change is pure settings→argv wiring, fully covered by unit tests.

### G4.5 — release · _Done 2026-05-27_

`v1.0.0` cut — the last Phase 4 goal, closing the phase and the five-phase plan.

- **CHANGELOG finalized.** The accreted `[Unreleased]` section (which spanned all
  of Phases 0–4 and had accumulated duplicate `### Added`/`### Changed`/`### Fixed`
  headers from per-PR appends) was consolidated into a single
  `## [1.0.0] - 2026-05-27` section with one of each subsection, ordered
  Added / Changed / Fixed / Documentation. Scaffolding and Phase 0–1 entries that
  had been mislabeled under `### Fixed` were moved to `### Added`; the repo-status
  and onboarding entries were grouped under `### Documentation`. A fresh empty
  `## [Unreleased]` skeleton sits above it, and the bottom compare-links were
  repaired to the Keep-a-Changelog convention
  (`[Unreleased]: …/compare/v1.0.0...HEAD`, `[1.0.0]: …/releases/tag/v1.0.0`).
- **Version bumped.** `pyproject.toml` `version` moved `0.0.0` → `1.0.0`. This is
  the source `uv build` stamps onto the sdist and wheel, so the published artifacts
  carry the correct version.
- **Coverage.** `make check` green at the release commit — **268 passed, 97.17%
  coverage** (gate 85%), with `ruff check`, `ruff format --check`, and strict
  `mypy` all clean.
- **Release mechanism.** The changelog + version bump + Phase 4 status-surface
  updates landed via a `chore/` PR, squash-merged on green CI. `v1.0.0` was then
  tagged on the merge commit and pushed, triggering
  `.github/workflows/release.yml` (`tags: ["v*"]` → `uv build` →
  `softprops/action-gh-release` with auto-generated notes and `dist/*` attached).
  The workflow run completed green and published the GitHub Release with the built
  sdist + wheel.

**Two optional interactive legs remain owed (non-blocking, do not gate the
release):** a spoken first-turn check on a true cold boot (G4.2) and a faithful
live-mic full-`jarvis run` hour (G4.3). Both mechanisms are proven; only the
in-person confirmations are outstanding.

### Status chimes · _Done (post-1.0.0 follow-up)_

The eyes-free state-transition cues that were deferred out of the always-on
runtime change. The headless service runs with no terminal in view, so the
runtime now voices three short tones over the existing audio output: `READY`
once at startup, `LISTENING` when the wake phrase is acknowledged (you can
start talking), and `THINKING` once capture ends and Claude is reasoning.
IDLE and SPEAKING never chime — IDLE re-enters twice between turns and would
double-fire; SPEAKING is Jarvis's own voice.

- **No copyrighted audio.** The tones are generated on the fly
  (`jarvis.chimes.make_tone`), deliberately matching the voice persona's
  posture (`docs/voice-persona.md`): evoke the refined holographic-interface
  *feel* without sampling any film audio. Each tone carries a short linear
  attack/release envelope so it never clicks even on the persistent Bluetooth
  stream.
- **Same persistent output stream as TTS.** Chimes are voiced at Kokoro's
  24 kHz so the `SoundDeviceStreamingSpeaker` (G4.6) does not have to renegotiate
  on the first speech clip after a cue. The chime observer calls `speaker.wait()`
  after each play so the cue drains before the loop's next step — critically, the
  `LISTENING` chime finishes before capture begins so it cannot bleed into the
  microphone.
- **Wired via the existing seam.** `build_chime_observer(speaker, *, enabled)`
  returns a pure `on_state` observer (consecutive identical states are deduped);
  `jarvis.cli.run` passes it as `VoiceLoop.on_state` and plays the `READY` cue
  once before warming the deferred loads. New config knob `JARVIS_CHIMES_ENABLED`
  (default `true`) mutes everything.

**Verification:**

- Write-first tests in `tests/test_chimes.py` (12 cases): tone sample
  rate matches Kokoro, requested duration is honored, attack/release fades
  attenuate the boundaries (not just the natural sine zero-crossing), named
  chimes are distinct/audible, the mapping covers only LISTENING + THINKING,
  the observer plays through the speaker + drains when enabled, is silent
  when disabled or on unmapped states, dedupes immediate repeats, and works
  cleanly against a minimal `Speaker` without `wait()`. Plus a
  `JARVIS_CHIMES_ENABLED` default/override case in `tests/test_config.py`.
- Full suite green at the change: **282 passed, 97.29% coverage**;
  `jarvis.chimes` 100% covered; the live wiring in `cli.run` stays
  `# pragma: no cover`.
- **Live audible verification owed to Ty:** `jarvis run` and confirm
  the READY/LISTENING/THINKING tones sound as intended through the persistent
  output stream (especially on AirPods, where the no-renegotiation property
  matters).
