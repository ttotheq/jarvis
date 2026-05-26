"""Tests for the launchd service lifecycle (G4.1).

Written before ``jarvis.service`` exists (TDD, per ADR-0005). The ``launchctl``
calls go through an injected ``Runner`` seam so install/uninstall/status
orchestration is exercised with a fake runner and a ``tmp_path`` HOME — no real
``launchctl`` and no writes to the real ``~/Library/LaunchAgents``. Only the
``default_runner`` that actually spawns ``launchctl`` is excluded from coverage.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jarvis import service
from jarvis.cli import app
from jarvis.config import Settings, get_settings
from jarvis.service import (
    RunResult,
    ServiceStatus,
    build_plist_spec,
    format_status,
    plist_path,
    render_plist,
)

runner = CliRunner()


class FakeRunner:
    """Records every ``launchctl`` argv and returns a canned result."""

    def __init__(self, result: RunResult | None = None) -> None:
        self.calls: list[list[str]] = []
        self._result = result or RunResult(returncode=0, stdout="", stderr="")

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(argv)
        return self._result


def _tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Point HOME at a tmp dir and return freshly-resolved settings."""
    monkeypatch.setenv("HOME", str(tmp_path))
    get_settings.cache_clear()
    return get_settings()


# --- Pure plist generation -------------------------------------------------


def test_service_plist_is_valid() -> None:
    """The generated plist parses via plistlib and points at the entry point."""
    spec = build_plist_spec(Settings(), python="/venv/bin/python", project_root=Path("/repo"))
    parsed = plistlib.loads(render_plist(spec))
    assert parsed["Label"] == "com.jarvis.voice"
    assert parsed["ProgramArguments"] == ["/venv/bin/python", "-m", "jarvis", "run"]
    assert parsed["WorkingDirectory"] == "/repo"


def test_plist_entry_point_resolved_at_install_time() -> None:
    """No path is baked into source: the interpreter is whatever is resolved."""
    spec = build_plist_spec(Settings(), python="/custom/python", project_root=Path("/repo"))
    assert spec["ProgramArguments"][0] == "/custom/python"
    # With no override, it resolves to the running interpreter (install-time).
    assert build_plist_spec(Settings())["ProgramArguments"][0] == sys.executable


def test_plist_autostarts_and_restarts_on_crash() -> None:
    spec = build_plist_spec(Settings())
    assert spec["RunAtLoad"] is True
    assert spec["KeepAlive"] == {"Crashed": True}


def test_plist_log_paths_use_configured_log_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_SERVICE_LOG_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    spec = build_plist_spec(get_settings())
    assert spec["StandardOutPath"].startswith(str(tmp_path / "logs"))
    assert spec["StandardErrorPath"].startswith(str(tmp_path / "logs"))


def test_plist_label_is_config_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_SERVICE_LABEL", "com.example.custom")
    get_settings.cache_clear()
    spec = build_plist_spec(get_settings())
    assert spec["Label"] == "com.example.custom"
    assert plist_path("com.example.custom").name == "com.example.custom.plist"


# --- Lifecycle orchestration (fake runner) ---------------------------------


def test_install_writes_plist_and_bootstraps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _tmp_home(tmp_path, monkeypatch)
    fake = FakeRunner()

    path = service.install(settings, runner=fake)

    assert path == plist_path(settings.service_label)
    assert path.exists()
    parsed = plistlib.loads(path.read_bytes())
    assert parsed["Label"] == settings.service_label
    # The log directory is created so launchd can write stdout/stderr.
    assert settings.service_log_dir.exists()
    # launchctl was asked to bootstrap the plist into the gui domain.
    assert any("bootstrap" in argv and str(path) in argv for argv in fake.calls)


def test_uninstall_removes_plist_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _tmp_home(tmp_path, monkeypatch)
    fake = FakeRunner()
    service.install(settings, runner=fake)
    path = plist_path(settings.service_label)
    assert path.exists()

    assert service.uninstall(settings, runner=fake) is True
    assert not path.exists()
    assert any("bootout" in argv for argv in fake.calls)

    # A second uninstall is a clean no-op (nothing to remove).
    assert service.uninstall(settings, runner=fake) is False


def test_status_reports_loaded_when_print_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _tmp_home(tmp_path, monkeypatch)
    path = plist_path(settings.service_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_plist(build_plist_spec(settings)))

    st = service.status(settings, runner=FakeRunner(RunResult(0, "state = running", "")))

    assert isinstance(st, ServiceStatus)
    assert st.loaded is True
    assert st.installed is True


def test_status_reports_unloaded_when_print_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _tmp_home(tmp_path, monkeypatch)
    st = service.status(settings, runner=FakeRunner(RunResult(113, "", "Could not find service")))
    assert st.loaded is False
    assert st.installed is False


def test_format_status_renders_label_and_state() -> None:
    report = format_status(
        ServiceStatus(
            label="com.jarvis.voice", installed=True, loaded=True, detail="state = running"
        )
    )
    assert "com.jarvis.voice" in report
    assert "running" in report or "loaded" in report.lower()


def test_format_status_without_detail_omits_detail_line() -> None:
    report = format_status(
        ServiceStatus(label="com.jarvis.voice", installed=False, loaded=False, detail="")
    )
    assert "not loaded" in report
    assert "detail:" not in report


# --- CLI surface (monkeypatched runner) ------------------------------------


def test_service_install_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _tmp_home(tmp_path, monkeypatch)
    fake = FakeRunner()
    monkeypatch.setattr(service, "default_runner", fake)

    result = runner.invoke(app, ["service", "install"])

    assert result.exit_code == 0
    assert plist_path(settings.service_label).exists()
    assert any("bootstrap" in argv for argv in fake.calls)


def test_service_uninstall_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _tmp_home(tmp_path, monkeypatch)
    fake = FakeRunner()
    monkeypatch.setattr(service, "default_runner", fake)
    service.install(settings, runner=fake)

    result = runner.invoke(app, ["service", "uninstall"])

    assert result.exit_code == 0
    assert not plist_path(settings.service_label).exists()
    assert any("bootout" in argv for argv in fake.calls)


def test_service_status_command_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _tmp_home(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "default_runner", FakeRunner(RunResult(0, "state = running", "")))
    loaded = runner.invoke(app, ["service", "status"])
    assert loaded.exit_code == 0

    monkeypatch.setattr(service, "default_runner", FakeRunner(RunResult(113, "", "not found")))
    unloaded = runner.invoke(app, ["service", "status"])
    assert unloaded.exit_code == 1
