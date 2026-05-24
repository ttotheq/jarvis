"""Tests for the `jarvis doctor` environment self-check.

Written before ``jarvis.doctor`` exists (TDD, per ADR-0005). Every probe is
injected as a fake so the native voice stack need not be installed to exercise
both the all-present and missing-dependency paths.
"""

from __future__ import annotations

from typer.testing import CliRunner

from jarvis import doctor
from jarvis.cli import app
from jarvis.doctor import Check, CheckResult, format_report, run_checks, run_doctor

runner = CliRunner()

FOUR_COMPONENTS = {"PortAudio", "whisper.cpp", "openWakeWord", "Kokoro"}


def _ok(name: str) -> Check:
    return lambda: CheckResult(name=name, ok=True, detail="found")


def _missing(name: str) -> Check:
    return lambda: CheckResult(name=name, ok=False, detail="not found — install it")


def _all_present() -> list[Check]:
    return [_ok(name) for name in FOUR_COMPONENTS]


def test_doctor_reports_missing_component() -> None:
    lines: list[str] = []
    code = run_doctor(
        [_ok("PortAudio"), _missing("whisper.cpp"), _ok("openWakeWord"), _ok("Kokoro")],
        write=lines.append,
    )
    report = "\n".join(lines)
    assert code != 0
    assert "whisper.cpp" in report
    assert "not found" in report.lower()


def test_doctor_all_present_exits_zero() -> None:
    code = run_doctor(_all_present(), write=lambda _: None)
    assert code == 0


def test_run_checks_default_probes_the_four_components() -> None:
    results = run_checks()
    assert {r.name for r in results} == FOUR_COMPONENTS
    assert all(isinstance(r.ok, bool) for r in results)
    assert all(r.detail for r in results)


def test_format_report_marks_each_status() -> None:
    report = format_report(
        [
            CheckResult(name="PortAudio", ok=True, detail="found"),
            CheckResult(name="Kokoro", ok=False, detail="not found — pip install kokoro"),
        ]
    )
    assert "PortAudio" in report
    assert "Kokoro" in report
    assert "pip install kokoro" in report


def test_doctor_command_exit_zero_when_all_present(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", tuple(_all_present()))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "PortAudio" in result.stdout


def test_doctor_command_exit_nonzero_names_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", (_ok("PortAudio"), _missing("Kokoro")))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code != 0
    assert "Kokoro" in result.stdout
