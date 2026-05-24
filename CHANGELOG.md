# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffolding: `uv`-managed Python 3.12 package with a `jarvis` CLI
  (`version`, `config` commands).
- Twelve-factor configuration layer (`jarvis.config`) driven by `JARVIS_*`
  environment variables and `.env`, with full test coverage.
- Quality gates: ruff (lint + format), mypy (strict), pytest with an 80%
  coverage floor; a `make check` target that mirrors CI.
- CI workflow (lint + type + test on macOS) and a tag-driven release workflow.
- GitHub repository conventions: issue/PR templates, CODEOWNERS, Dependabot.
- Documentation: architecture, voice persona, five Architecture Decision
  Records, and the five-phase plan with measurable goals.

[Unreleased]: https://github.com/ttotheq/jarvis/commits/main
