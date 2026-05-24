# Phase 4 — Daemon polish

- **Status:** Not started
- **Milestone:** Phase 4
- **Objective:** Make Jarvis a dependable always-on background service and cut
  the first release.

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

_To be filled in as the phase completes._
