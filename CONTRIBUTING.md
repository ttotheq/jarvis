# Contributing to Jarvis

This is a personal project, but it runs on real conventions so that each phase
ships with confidence and the history stays legible. The three habits below are
non-negotiable because the whole project is organized around them.

## 1. Test-driven development

Write the failing test first, then the code that makes it pass, then refactor.

- Every phase doc lists a **Test plan (write first)** — those tests define "done".
- A change without a test is incomplete. Bug fixes start with a test that
  reproduces the bug.
- The coverage gate is **85%** (`--cov-fail-under=85` in `pyproject.toml`); it
  started at 80% and rose in Phases 3–4 (see `docs/phases/`). Coverage is a
  floor, not a goal — test behaviour, not lines.

```bash
make test     # pytest with the coverage gate
make check    # the full local gate: ruff + mypy + pytest (mirrors CI exactly)
```

Never push without a green `make check`.

## 2. Iterative documentation

Documentation changes in the **same PR** as the code it describes — never "later".

- Architectural choices get an **ADR** in `docs/adr/` (copy the format of
  `0001-record-architecture-decisions.md`).
- New tunables are added to `src/jarvis/config.py` **and** `.env.example` in the
  same change.
- Phase docs (`docs/phases/`) are living: update the **Status** line and fill in
  the **Outcomes** section as work lands.
- Every PR adds an entry to the `## [Unreleased]` section of `CHANGELOG.md`
  ([Keep a Changelog](https://keepachangelog.com/) format).

## 3. Trunk-based Git workflow

- `main` is always releasable and protected (PR + green CI required to merge).
- Work on short-lived branches: `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- Commits and PR titles follow
  [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(stt): stream whisper.cpp partials`, `fix(vad): debounce endpoint`,
  `docs(phase-2): record latency outcomes`. Scopes mirror the module map in
  `docs/architecture.md` (`audio`, `wakeword`, `vad`, `stt`, `brain`, `tts`,
  `persona`, `loop`, `config`).
- Versioning is [Semantic Versioning](https://semver.org/). Releases are cut by
  tagging `vX.Y.Z`, which triggers `.github/workflows/release.yml`.

## Setup

```bash
uv sync                 # venv + dev dependencies (uv installs Python 3.12)
uv run pre-commit install   # optional: run lint/format on every commit
```

## Definition of Done (every PR)

1. Tests written first, all green; coverage gate met.
2. `make check` passes locally.
3. Docs/ADR/`.env.example`/`CHANGELOG.md` updated as applicable.
4. The linked issue's measurable acceptance criteria are satisfied.
