"""Spoken permission gating: a Claude Code ``PreToolUse`` hook (Phase 3, G3.3).

The voice persona promises to "confirm destructive or irreversible actions
verbally before executing them" (``docs/voice-persona.md``). A system prompt only
buys *willingness*; this module makes the promise mechanical. It is a Claude Code
``PreToolUse`` hook that runs as a **separate process** the ``claude`` child spawns
(not inside the jarvis loop): it reads the tool call about to run from stdin and
writes back a permission decision on stdout, *before* the tool executes.

Why it must exist under ``--permission-mode acceptEdits`` (the brain default,
ADR-0003): acceptEdits auto-approves edits and the headless ``claude -p`` child has
no human at a keyboard to approve a Bash call — so a destructive ``rm -rf`` or
``git push`` would otherwise run unattended. This hook is the thing that stops it
and asks, out loud, first.

The design splits cleanly along the cross-process boundary:

- **Pure and CI-proven:** :func:`is_destructive` classifies a tool call, and
  :func:`decide` turns a payload plus an injected ``confirm`` seam into the Claude
  Code ``PreToolUse`` decision (``hookSpecificOutput.permissionDecision`` =
  ``allow`` / ``deny``). :func:`main` parses stdin and emits the verdict. None of
  these speak, record, or spawn anything.

Emission channel (verified live, not guessed). Claude Code 2.1.150 does **not**
block a tool when a ``PreToolUse`` hook emits ``permissionDecision: "deny"`` as
stdout JSON — the tool still runs (confirmed end to end on 2026-05-25). The block
Claude Code honors is the **exit-code protocol**: exit 2 with the reason on stderr.
So :func:`main` routes a denial through exit 2 + stderr while an allow rides the
documented stdout JSON at exit 0. :func:`decide` stays pure and still returns the
documented decision dict (fully unit-tested); :func:`main` is the thin translation
to the channel that works.
- **Live and manually exercised:** :func:`build_live_confirm` wires the real
  ``confirm`` (TTS speaks the question, mic + whisper.cpp capture the yes/no), and
  the hook is registered in ``settings.json`` with a ``Bash`` matcher. That path is
  hardware-bound, excluded from coverage (ADR-0005), and recorded in the Phase 3
  doc Outcomes.

Classification scope. The destructive surface is **Bash**: acceptEdits already
governs file edits, and read-only tools must never gate (a confirmation prompt on
every ``ls`` would wreck the voice UX). Within Bash the gate flags an explicit set
of irreversible verbs (delete, force-push, hard-reset, disk/process/power, and
privilege escalation), erring toward asking — a benign command that slips through
runs unattended, exactly as it would without the gate, while every verb in the set
is caught with certainty. A spoken yes/no that is negative *or ambiguous* denies:
on doubt the gate never auto-runs.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from collections.abc import Callable, Mapping
from typing import Any, TextIO

#: The injected speak-question -> capture-yes/no seam. A live impl is built by
#: :func:`build_live_confirm`; tests pass a fake so the contract is unit-testable.
Confirm = Callable[[str], bool]

#: The Claude Code hook event this module answers (echoed back in the decision).
HOOK_EVENT_NAME = "PreToolUse"


# --- classifying a tool call ----------------------------------------------

# Bare programs that are destructive regardless of their arguments, mapped to a
# speakable, intent-level description (never the command itself).
_DESTRUCTIVE_PROGRAMS: dict[str, str] = {
    "rm": "delete files",
    "rmdir": "delete a directory",
    "shred": "irreversibly erase a file",
    "dd": "overwrite a disk or device",
    "mkfs": "format a filesystem",
    "truncate": "truncate a file",
    "kill": "stop a running process",
    "killall": "stop running processes",
    "pkill": "stop running processes",
    "shutdown": "shut the machine down",
    "reboot": "restart the machine",
    "halt": "halt the machine",
    "poweroff": "power the machine off",
}

# Leading tokens that wrap the real command without being it; skip past them to
# find the program actually being run (``sudo`` additionally marks privilege).
_WRAPPERS = frozenset({"nohup", "time", "command", "exec", "builtin"})
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Splits a shell line into its sub-commands on &&, ||, ;, |, and newlines, so a
# destructive verb hiding in a chain (``cd x && rm -rf y``) is still inspected.
_SUBCOMMAND_SPLIT = re.compile(r"&&|\|\||[;|\n]")


def _segments(command: str) -> list[str]:
    """Split a (possibly compound) shell line into individual sub-commands."""
    return [seg.strip() for seg in _SUBCOMMAND_SPLIT.split(command) if seg.strip()]


def _tokenize(segment: str) -> list[str]:
    """Token-split one sub-command; fall back to whitespace on unbalanced quotes."""
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _program(tokens: list[str]) -> tuple[str | None, list[str], bool]:
    """Resolve a sub-command's program, its remaining args, and whether it is privileged.

    Strips leading ``VAR=value`` env assignments and command wrappers (``nohup``,
    ``time``, …); a leading ``sudo`` is consumed and flagged as privileged so the
    real program after it is still classified.
    """
    privileged = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _ENV_ASSIGNMENT.match(tok) or tok in _WRAPPERS:
            i += 1
            continue
        if tok == "sudo" or tok == "doas":
            privileged = True
            i += 1
            continue
        break
    if i >= len(tokens):
        return None, [], privileged
    program = tokens[i].rsplit("/", 1)[-1]  # basename: /bin/rm -> rm
    return program, tokens[i + 1 :], privileged


def _classify_git(args: list[str]) -> str | None:
    """Return a speakable reason if a ``git`` invocation is destructive, else None."""
    subcommand = next((a for a in args if not a.startswith("-")), None)
    if subcommand == "push":
        return "push to the remote"
    if subcommand == "clean":
        return "delete untracked files"
    if subcommand == "reset" and "--hard" in args:
        return "discard uncommitted changes"
    if subcommand == "branch" and "-D" in args:
        return "force-delete a branch"
    if subcommand in {"checkout", "restore"} and ("--force" in args or "-f" in args):
        return "discard local changes"
    return None


def _classify_command(command: str) -> str | None:
    """Return a speakable reason if any sub-command is destructive, else None.

    The first destructive sub-command wins; its intent-level description (never the
    raw command) is what gets spoken aloud.
    """
    for segment in _segments(command):
        program, args, privileged = _program(_tokenize(segment))
        if program is None:
            continue
        if program in _DESTRUCTIVE_PROGRAMS:
            return _DESTRUCTIVE_PROGRAMS[program]
        if program == "git":
            reason = _classify_git(args)
            if reason is not None:
                return reason
        if privileged:  # a privileged command we don't otherwise recognise still gates
            return "run a command with elevated privileges"
    return None


def is_destructive(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """Whether this tool call is destructive and must be confirmed before running.

    Only ``Bash`` is classified: acceptEdits governs file edits, and read-only
    tools must never gate. A Bash command gates when it contains an irreversible
    verb (see the module docstring); everything else passes through.
    """
    if tool_name == "Bash":
        return _classify_command(str(tool_input.get("command", ""))) is not None
    return False


def summarize(tool_name: str, tool_input: Mapping[str, Any]) -> str:
    """The spoken confirmation question for a destructive call — intent, not command.

    Per the persona, no command, flags, or paths are read aloud: the user hears
    *what* is about to happen ("delete files") and is asked for the go-ahead.
    """
    reason = "run a destructive action"
    if tool_name == "Bash":
        reason = _classify_command(str(tool_input.get("command", ""))) or reason
    return f"You're about to {reason}, sir — shall I proceed?"


# --- emitting the Claude Code PreToolUse decision -------------------------


def _decision(permission: str, reason: str) -> dict[str, Any]:
    """Wrap a permission verdict in Claude Code's ``PreToolUse`` output shape.

    A ``PreToolUse`` decision is ``hookSpecificOutput.permissionDecision`` of
    ``allow`` / ``deny`` / ``ask``. This is the documented, pure shape :func:`decide`
    returns; note that :func:`main` delivers a *deny* via the exit-code protocol
    (exit 2 + stderr) because 2.1.150 ignores a stdout ``deny`` (see the module
    docstring), while an ``allow`` is emitted as this JSON at exit 0.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT_NAME,
            "permissionDecision": permission,
            "permissionDecisionReason": reason,
        }
    }


def decide(payload: Mapping[str, Any], confirm: Confirm) -> dict[str, Any]:
    """Turn a ``PreToolUse`` payload into a permission decision.

    Destructive calls are spoken aloud via ``confirm`` *before* a verdict is
    formed: an affirmative allows, anything else denies. Non-destructive calls are
    allowed without consulting ``confirm`` — the gate stays silent on safe work.
    """
    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    if not is_destructive(tool_name, tool_input):
        return _decision("allow", "Read-only or non-destructive; no confirmation needed.")
    if confirm(summarize(tool_name, tool_input)):
        return _decision("allow", "Confirmed aloud before running.")
    return _decision("deny", "Declined at the spoken confirmation.")


# --- interpreting the spoken yes/no ---------------------------------------

_NEGATIVES = frozenset(
    {"no", "not", "nope", "don't", "dont", "stop", "cancel", "abort", "negative", "never", "nah"}
)
_AFFIRMATIVES = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "affirmative", "proceed", "confirm"}
)
_AFFIRMATIVE_PHRASES = ("go ahead", "do it", "go for it", "please do")
_WORD = re.compile(r"[a-z']+")


def interpret_confirmation(transcript: str) -> bool:
    """Map a spoken yes/no transcript to a go-ahead, defaulting to NO on doubt.

    A negative word anywhere wins (so "yes — actually no, stop" does not run). An
    affirmative word or phrase otherwise allows. Silence, ambiguity, or an empty
    transcript deny: the gate never auto-runs a destructive command on uncertainty.
    """
    lowered = transcript.lower()
    tokens = set(_WORD.findall(lowered))
    if tokens & _NEGATIVES:
        return False
    if tokens & _AFFIRMATIVES:
        return True
    return any(phrase in lowered for phrase in _AFFIRMATIVE_PHRASES)


# --- the hook entrypoint ---------------------------------------------------


def main(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    confirm: Confirm | None = None,
) -> int:
    """Read a ``PreToolUse`` payload from stdin and emit the gate's decision.

    The emission uses the channel Claude Code actually honors, verified live
    against ``claude`` 2.1.150 (2026-05-25): a ``deny`` emitted as stdout JSON
    (``permissionDecision: "deny"``) is **not** blocked — the tool still runs — so
    a denial travels via the **exit-code protocol** instead: the reason on stderr
    and exit 2, which Claude Code blocks on (and feeds the reason back to the
    model). An ``allow`` rides the documented stdout JSON at exit 0.

    ``confirm`` is injected in tests; left unset it is built live
    (:func:`build_live_confirm`) so the spawned hook speaks the question and
    captures the spoken answer.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    if confirm is None:  # pragma: no cover - live audio path, exercised manually
        confirm = build_live_confirm()
    payload = json.load(stdin)
    output = decide(payload, confirm)["hookSpecificOutput"]
    if output["permissionDecision"] == "deny":
        stderr.write(output["permissionDecisionReason"] + "\n")
        return 2  # exit 2 is the PreToolUse block Claude Code honors
    json.dump({"hookSpecificOutput": output}, stdout)
    stdout.write("\n")
    return 0


def build_live_confirm(  # pragma: no cover - native audio path, exercised manually
    settings: Any | None = None,
    window_seconds: float = 4.0,
) -> Confirm:
    """Build the live ``confirm``: speak the question, capture and interpret a yes/no.

    Reuses the cascade's own components — Kokoro TTS to ask, the sounddevice mic and
    whisper.cpp to hear the answer — then :func:`interpret_confirmation` decides.
    Hardware-bound, so it is excluded from coverage and exercised manually.
    """
    import math

    from jarvis.audio import make_sounddevice_source, record
    from jarvis.config import get_settings
    from jarvis.stt import WhisperCppTranscriber
    from jarvis.tts import build_default_speaker, build_default_synthesizer, speak

    settings = settings if settings is not None else get_settings()
    synthesize = build_default_synthesizer()
    speaker = build_default_speaker()
    transcribe = WhisperCppTranscriber(settings)
    block_frames = 1_600
    source = make_sounddevice_source(settings.sample_rate, block_frames=block_frames)
    reads = math.ceil(window_seconds * settings.sample_rate / block_frames)

    def confirm(question: str) -> bool:
        speak(question, synthesize, speaker)
        remaining = reads

        def done() -> bool:
            nonlocal remaining
            remaining -= 1
            return remaining < 0

        answer = transcribe(record(source, done, settings.sample_rate))
        return interpret_confirmation(answer)

    return confirm


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
