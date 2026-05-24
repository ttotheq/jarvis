# ADR-0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-05-24

## Context

This project will make a series of consequential, hard-to-reverse technical
choices (how to drive Claude Code, local vs cloud voice, language/runtime).
Future-me needs to know *why* each was made, not just *what* was chosen.

## Decision

Record each significant decision as a short, numbered, immutable Architecture
Decision Record in `docs/adr/`, using the lightweight format seen here
(Context / Decision / Consequences). Superseding decisions get a new ADR that
references the one it replaces; existing ADRs are not rewritten.

## Consequences

- Decisions are auditable and onboarding is faster.
- A small, enforced discipline: any PR that changes architecture adds an ADR
  (see `CONTRIBUTING.md`).
