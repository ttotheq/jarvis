# ADR-0005: TDD and quality gates

- **Status:** Accepted
- **Date:** 2026-05-24

## Context

A voice loop has many integration seams (audio, native libs, a subprocess
brain) where regressions are easy and hard to notice by ear. The project also
wants measurable phase goals that can later be tracked as Claude Code goals.

## Decision

- **Test-driven development**: failing test first, then implementation. Each
  phase doc lists its "write-first" tests.
- **Coverage gate** enforced in `pyproject.toml` (`--cov-fail-under`), starting
  at **80%** and rising to 85% in Phases 3–4.
- **CI gate** on every push/PR to `main` (`.github/workflows/ci.yml`): ruff
  lint + format check, mypy strict, pytest. `make check` reproduces it locally.
- **Branch protection** on `main`: PR required, CI must pass before merge.
- Hardware-dependent paths (real mic/speaker, native models) are isolated behind
  interfaces and tested with fakes/fixtures; true end-to-end audio checks are
  manual and recorded in phase "Outcomes".

## Consequences

- Regressions in pure logic are caught automatically; the audio edges are
  covered by design (dependency inversion) plus documented manual checks.
- Measurable goals double as acceptance tests, giving each phase an objective
  definition of done.
