# Jarvis documentation

| Document | Purpose |
|----------|---------|
| [architecture.md](architecture.md) | The voice cascade, runtime state machine, and module map |
| [voice-persona.md](voice-persona.md) | How Claude is steered to sound like Jarvis and stay speakable |
| [adr/](adr/) | Architecture Decision Records — *why* the project is built this way |
| [phases/](phases/) | The five-phase delivery plan with measurable goals |

## How to read this

Start with the [phase plan](phases/README.md). Each phase has measurable
acceptance goals; the architecture and ADRs explain the system those phases
build. Documentation is **iterative** — phase docs carry a live status and gain
an "Outcomes" section as work lands, and repo-level status surfaces (`README.md`,
this index, the phase overview, and any affected architecture notes) are updated
when a phase closes (see [CONTRIBUTING.md](../CONTRIBUTING.md)).
