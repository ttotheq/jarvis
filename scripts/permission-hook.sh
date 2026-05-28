#!/usr/bin/env bash
# Claude Code PreToolUse hook entrypoint for jarvis.permissions.
# Registered in .claude/settings.json on the Bash matcher: non-destructive
# Bash calls are allowed silently; destructive ones (rm -rf, git push, …) are
# spoken aloud and gated on a yes/no answer. The shim resolves the project
# venv from its own location so the hook is not tied to an absolute path.
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
exec "${project_root}/.venv/bin/python" -m jarvis.permissions
