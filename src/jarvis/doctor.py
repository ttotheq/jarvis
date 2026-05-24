"""Environment self-check for Jarvis (``jarvis doctor``).

Phase 0 de-risking: before any of the live voice loop is attempted, confirm the
local-first stack chosen in ADR-0002 is actually installed on this machine —
PortAudio (audio I/O), whisper.cpp (STT), openWakeWord (wake word), and Kokoro
(TTS).

Each dependency is probed by an independent :data:`Check`. Probes are values,
not hard-coded calls, so the test suite injects fakes to exercise the
all-present and missing-dependency paths without the native libraries being
present (see ``tests/test_doctor.py``).
"""

from __future__ import annotations

import ctypes.util
import importlib.util
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    """Outcome of probing a single dependency."""

    name: str
    ok: bool
    detail: str


#: A probe: takes nothing, reports whether one dependency is available.
Check = Callable[[], CheckResult]


def _check_portaudio() -> CheckResult:
    found = ctypes.util.find_library("portaudio") is not None
    detail = "audio backend available" if found else "not found — `brew install portaudio`"
    return CheckResult(name="PortAudio", ok=found, detail=detail)


def _check_whisper_cpp() -> CheckResult:
    binary = next(
        (b for b in ("whisper-cli", "whisper-cpp", "whisper") if shutil.which(b)),
        None,
    )
    found = binary is not None or ctypes.util.find_library("whisper") is not None
    detail = (
        (f"found ({binary})" if binary else "library available")
        if found
        else "not found — build whisper.cpp and put its CLI on PATH"
    )
    return CheckResult(name="whisper.cpp", ok=found, detail=detail)


def _check_openwakeword() -> CheckResult:
    found = importlib.util.find_spec("openwakeword") is not None
    detail = "importable" if found else "not found — `pip install openwakeword`"
    return CheckResult(name="openWakeWord", ok=found, detail=detail)


def _check_kokoro() -> CheckResult:
    found = importlib.util.find_spec("kokoro") is not None
    detail = "importable" if found else "not found — `pip install kokoro`"
    return CheckResult(name="Kokoro", ok=found, detail=detail)


#: The probes run when ``jarvis doctor`` is invoked with no overrides.
DEFAULT_CHECKS: tuple[Check, ...] = (
    _check_portaudio,
    _check_whisper_cpp,
    _check_openwakeword,
    _check_kokoro,
)


def run_checks(checks: Sequence[Check] | None = None) -> list[CheckResult]:
    """Run each probe and collect its result, preserving order."""
    probes = DEFAULT_CHECKS if checks is None else checks
    return [probe() for probe in probes]


def format_report(results: Sequence[CheckResult]) -> str:
    """Render check results as an aligned, human-readable report."""
    width = max((len(r.name) for r in results), default=0)
    lines = [f"  [{'OK ' if r.ok else 'MISS'}] {r.name:<{width}}  {r.detail}" for r in results]
    missing = [r.name for r in results if not r.ok]
    footer = (
        "All voice-stack dependencies present." if not missing else f"Missing: {', '.join(missing)}"
    )
    return "\n".join(["Jarvis environment check:", *lines, "", footer])


def run_doctor(
    checks: Sequence[Check] | None = None,
    *,
    write: Callable[[str], None] = print,
) -> int:
    """Probe the environment, print a report, and return an exit code.

    Returns 0 when every dependency is present, 1 otherwise. ``checks`` and
    ``write`` are injectable so the behaviour can be tested without the native
    stack and without touching real stdout.
    """
    results = run_checks(checks)
    write(format_report(results))
    return 0 if all(r.ok for r in results) else 1
