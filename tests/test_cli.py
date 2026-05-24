"""Tests for the Jarvis CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from jarvis import __version__
from jarvis.cli import app

runner = CliRunner()


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_command_lists_settings() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "wake_word = hey_jarvis" in result.stdout
    assert "permission_mode" in result.stdout


def test_help_shows_description() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Claude Code" in result.stdout
