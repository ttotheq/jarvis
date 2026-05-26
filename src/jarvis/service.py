"""macOS launchd service lifecycle for Jarvis (``jarvis service ...``).

Phase 4 (G4.1) makes Jarvis an always-on background service. On macOS the
mechanism is a per-user **LaunchAgent**: a plist in ``~/Library/LaunchAgents``
that launchd starts at login (``RunAtLoad``) and restarts on crash
(``KeepAlive`` ``{Crashed: True}``). See ADR-0006.

The design mirrors :mod:`jarvis.doctor`: the plist generation and lifecycle
orchestration are pure, testable functions, and the only thing that actually
spawns ``launchctl`` is the injected :data:`Runner` seam (default
:func:`default_runner`, excluded from coverage). This keeps every path but the
real subprocess unit-tested with a fake runner and a tmp HOME.

**No user paths are hard-coded in source.** The entry point resolves at install
time to the running interpreter (``sys.executable``) plus ``-m jarvis run``, and
the working directory to the resolved project root — so the generated plist is
correct for whatever venv installed it.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.config import Settings


@dataclass(frozen=True)
class RunResult:
    """Outcome of one ``launchctl`` invocation."""

    returncode: int
    stdout: str
    stderr: str


#: A seam that runs one command and reports its result. Injected so the
#: install/uninstall/status logic is tested without spawning ``launchctl``.
Runner = Callable[[list[str]], RunResult]


@dataclass(frozen=True)
class ServiceStatus:
    """A snapshot of the service's install/load state."""

    label: str
    installed: bool  # the plist file exists in ~/Library/LaunchAgents
    loaded: bool  # launchctl knows about the service in the gui domain
    detail: str


def default_runner(argv: list[str]) -> RunResult:  # pragma: no cover - spawns launchctl
    """Run a command via ``subprocess`` and capture its result."""
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def _project_root() -> Path:
    """Resolve the repository/package root (src/jarvis/service.py -> repo)."""
    return Path(__file__).resolve().parents[2]


def launch_agents_dir() -> Path:
    """The per-user LaunchAgents directory (a fixed macOS location)."""
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label: str) -> Path:
    """Path to the LaunchAgent plist for ``label``."""
    return launch_agents_dir() / f"{label}.plist"


def _domain_target() -> str:
    """The launchd gui domain for the current user (gui/<uid>)."""
    return f"gui/{os.getuid()}"


def _service_target(label: str) -> str:
    """The service-specific launchd target (gui/<uid>/<label>)."""
    return f"{_domain_target()}/{label}"


def build_plist_spec(
    settings: Settings,
    *,
    python: str | None = None,
    project_root: Path | None = None,
    env_path: str | None = None,
) -> dict[str, Any]:
    """Build the launchd plist as a dict, resolving paths at install time.

    ``python`` defaults to the running interpreter, ``project_root`` to the
    resolved package root, and ``env_path`` to the current ``PATH`` — so the
    LaunchAgent can find ``claude`` and the native voice binaries under
    launchd's otherwise-minimal environment. All three are injectable for tests.
    """
    python = python or sys.executable
    project_root = project_root or _project_root()
    env_path = env_path if env_path is not None else os.environ.get("PATH", "")
    log_dir = settings.service_log_dir
    return {
        "Label": settings.service_label,
        "ProgramArguments": [python, "-m", "jarvis", "run"],
        # Auto-start at login, restart on crash (but not on a clean exit).
        "RunAtLoad": True,
        "KeepAlive": {"Crashed": True},
        "WorkingDirectory": str(project_root),
        "EnvironmentVariables": {"PATH": env_path},
        "StandardOutPath": str(log_dir / "jarvis.out.log"),
        "StandardErrorPath": str(log_dir / "jarvis.err.log"),
        # Audio workload: ask launchd for interactive QoS.
        "ProcessType": "Interactive",
    }


def render_plist(spec: dict[str, Any]) -> bytes:
    """Serialize a plist spec to XML bytes."""
    return plistlib.dumps(spec)


def install(
    settings: Settings,
    *,
    runner: Runner = default_runner,
    python: str | None = None,
    project_root: Path | None = None,
    env_path: str | None = None,
) -> Path:
    """Write the LaunchAgent plist and load it into launchd.

    Creates the log directory, writes ``~/Library/LaunchAgents/<label>.plist``,
    boots out any stale registration (best-effort), then bootstraps the service
    into the gui domain. With ``RunAtLoad`` the service also starts immediately.
    Returns the plist path.
    """
    spec = build_plist_spec(settings, python=python, project_root=project_root, env_path=env_path)
    path = plist_path(settings.service_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings.service_log_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_plist(spec))

    # Clear any prior registration so a re-install picks up the new plist; this
    # fails harmlessly when nothing is loaded.
    runner(["launchctl", "bootout", _service_target(settings.service_label)])
    runner(["launchctl", "bootstrap", _domain_target(), str(path)])
    return path


def uninstall(settings: Settings, *, runner: Runner = default_runner) -> bool:
    """Boot the service out of launchd and remove its plist.

    Returns ``True`` if a plist was removed, ``False`` if there was nothing to
    remove (so the operation is idempotent).
    """
    runner(["launchctl", "bootout", _service_target(settings.service_label)])
    path = plist_path(settings.service_label)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def status(settings: Settings, *, runner: Runner = default_runner) -> ServiceStatus:
    """Report whether the service is installed (plist present) and loaded."""
    result = runner(["launchctl", "print", _service_target(settings.service_label)])
    detail = result.stdout.strip() or result.stderr.strip()
    return ServiceStatus(
        label=settings.service_label,
        installed=plist_path(settings.service_label).exists(),
        loaded=result.returncode == 0,
        detail=detail,
    )


def format_status(st: ServiceStatus) -> str:
    """Render a status snapshot as a human-readable report."""
    state = "loaded (running)" if st.loaded else "not loaded"
    installed = "yes" if st.installed else "no"
    lines = [
        f"{st.label}: {state}",
        f"  plist installed: {installed} ({plist_path(st.label)})",
    ]
    if st.detail:
        lines.append(f"  detail: {st.detail.splitlines()[0]}")
    return "\n".join(lines)
