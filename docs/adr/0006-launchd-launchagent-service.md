# ADR-0006: Run as a macOS launchd LaunchAgent

- **Status:** Accepted
- **Date:** 2026-05-26

## Context

Phase 4 turns Jarvis from a foreground developer harness into a dependable
always-on background service (G4.1): it should start at login, restart if it
crashes, and be installable/uninstallable with one command. Jarvis is a
**single-user, on-device** voice assistant (ADR-0002) that needs the logged-in
user's audio session (microphone + speakers) and that user's environment
(`claude` on PATH, the venv interpreter, the native voice binaries).

Options considered:

- **launchd LaunchAgent** (per-user, `~/Library/LaunchAgents`) — runs in the
  user's GUI session, has access to audio and the user keychain, starts at
  login. The native macOS mechanism for exactly this shape of service.
- **launchd LaunchDaemon** (system-wide, `/Library/LaunchDaemons`) — runs as
  root before login, in no user audio session. Wrong layer for a microphone +
  speaker assistant, and needs sudo to install.
- **Login Item / `brew services`** — login items can't express crash-restart
  policy cleanly; `brew services` is a thin launchd wrapper that assumes a
  Homebrew-formula install, which Jarvis is not.

A second decision is which `launchctl` verbs to use. The legacy
`launchctl load -w` / `unload -w` interface is deprecated and increasingly
unreliable on recent macOS (the service runs on **macOS Tahoe 26.5**). The
modern domain-target interface (`bootstrap` / `bootout` / `print` against
`gui/<uid>`) is the supported path.

## Decision

- Run Jarvis as a **per-user launchd LaunchAgent**. `jarvis service install`
  generates `~/Library/LaunchAgents/<service_label>.plist` and bootstraps it;
  `uninstall` boots it out and removes the plist; `status` prints its load
  state.
- The plist sets `RunAtLoad: true` (start at login) and
  `KeepAlive: {Crashed: true}` (restart on crash, but **not** on a clean exit —
  so the loop can exit cleanly without thrash-restarting while the always-on
  wake-word entry point is still being wired in later Phase 4 work).
- Use the **modern** `launchctl bootstrap gui/<uid> <plist>` /
  `bootout gui/<uid>/<label>` / `print gui/<uid>/<label>` verbs.
- **Resolve all paths at install time, never in source.** `ProgramArguments` is
  `[sys.executable, "-m", "jarvis", "run"]`, `WorkingDirectory` is the resolved
  project root, and `EnvironmentVariables.PATH` is snapshotted from the
  install-time environment so launchd's minimal env still finds `claude` and the
  native binaries. The generated plist therefore matches whatever venv installed
  it; the repository contains no hard-coded user paths.
- The plist label and log directory are config-driven
  (`JARVIS_SERVICE_LABEL`, `JARVIS_SERVICE_LOG_DIR`).

Plist generation (`build_plist_spec`/`render_plist`) and lifecycle orchestration
(`install`/`uninstall`/`status`) are pure and unit-tested; only the
`launchctl`-spawning `default_runner` is an injected, coverage-excluded shim
(ADR-0005).

## Consequences

- One command installs an always-on agent that survives logout/login and
  restarts on crash, with a clean uninstall — no leftover registration.
- The generated plist is portable across machines/venvs because every path is
  resolved at install time.
- A LaunchAgent only runs while a user is logged in; that is correct for an
  assistant that needs the user's audio session, and rules out pre-login or
  headless operation by design (out of scope for v1).
- `jarvis run` is still the Enter-gated developer harness, so under launchd
  (no TTY) it currently exits rather than holding a session; `KeepAlive`
  `{Crashed: true}` deliberately does not relaunch a clean exit. Wiring the
  always-on wake-word loop as the launchd entry point is the remaining Phase 4
  step; this ADR fixes the service mechanism it will plug into.
