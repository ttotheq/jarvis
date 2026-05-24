"""Command-line entry point for Jarvis.

Commands available today are intentionally minimal; the conversational
runtime (``jarvis run``) and environment self-check (``jarvis doctor``) are
introduced in Phase 0/1 per ``docs/phases/``.
"""

from __future__ import annotations

import typer

from jarvis import __version__
from jarvis.config import get_settings

app = typer.Typer(
    name="jarvis",
    help="A local-first, voice-controlled interface for Claude Code.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the installed Jarvis version."""
    typer.echo(__version__)


@app.command()
def config() -> None:
    """Print the resolved runtime configuration (defaults + env + .env)."""
    settings = get_settings()
    for key, value in settings.model_dump().items():
        typer.echo(f"{key} = {value}")
