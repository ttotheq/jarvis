# ADR-0004: Python runtime and tooling

- **Status:** Accepted
- **Date:** 2026-05-24

## Context

The voice ecosystem (openWakeWord, Silero, whisper.cpp bindings, Kokoro,
`sounddevice`) is Python-first, which makes Python the pragmatic choice. The
machine has Python 3.14, but the ML/audio wheels these libraries depend on
(onnxruntime, torch, native audio) have the most reliable Apple Silicon support
on Python 3.12.

## Decision

- **Python 3.12**, pinned via `.python-version` and `requires-python>=3.12`;
  `uv` provisions the interpreter.
- **uv** for environment and dependency management; `uv.lock` committed for
  reproducible installs (`uv sync --frozen` in CI).
- **ruff** for linting and formatting, **mypy** (strict) for typing, **pytest**
  + **pytest-cov** for tests.
- **hatchling** as the build backend; `src/` layout.
- **typer** for the CLI, **pydantic-settings** for configuration.

## Consequences

- Reproducible, fast installs and a single toolchain (`uv`) across dev and CI.
- Choosing 3.12 over 3.14 trades newest-language features for wheel
  availability — revisit when the voice deps publish 3.14 wheels.
- Strict mypy and the ruff rule set raise the floor on every contribution.
