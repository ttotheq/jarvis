"""Allow ``python -m jarvis`` to invoke the CLI."""

from __future__ import annotations

from jarvis.cli import app

if __name__ == "__main__":
    app()
