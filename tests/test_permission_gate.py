"""Tests for the spoken permission gate (Phase 3 goal G3.3).

Written before ``jarvis.permissions`` exists (TDD, per ADR-0005). The gate is a
Claude Code ``PreToolUse`` hook: it classifies a tool call as destructive or not,
and for destructive calls it routes a spoken yes/no confirmation *before* the call
runs. The contract G3.3 enforces mechanically is the persona line "confirm
destructive or irreversible actions verbally before executing them".

The split between what CI proves and what only a live run can (mirrors G3.2):

- **CI proves the pure classifier and the decision emission.** ``is_destructive``
  and ``decide`` are pure; ``main`` parses a ``PreToolUse`` JSON payload from stdin
  and writes the Claude Code ``permissionDecision`` JSON to stdout. ``confirm`` —
  the speak-question/capture-yes-no seam — is *injected* as a fake, so nothing here
  speaks, records, or spawns ``claude``.
- **The live audio wiring is the integration step.** ``build_live_confirm`` (TTS +
  STT + mic) and the ``settings.json`` ``PreToolUse`` matcher are exercised
  manually and recorded in the Phase 3 doc Outcomes — not in CI.
"""

from __future__ import annotations

import io
import json

import pytest

from jarvis.permissions import (
    decide,
    interpret_confirmation,
    is_destructive,
    main,
    summarize,
)


class _Confirm:
    """A fake spoken-confirmation seam: records its questions, returns a fixed verdict."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict
        self.questions: list[str] = []

    def __call__(self, question: str) -> bool:
        self.questions.append(question)
        return self._verdict


def _payload(tool_name: str, **tool_input: object) -> dict[str, object]:
    """A minimal PreToolUse payload (the fields the gate reads)."""
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


# --- the pure classifier --------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build/",
        "rm -rf /Users/ttotheq/.cache/jarvis",
        "git push origin main",
        "git push --force",
        "git reset --hard HEAD~1",
        "git clean -fd",
        "cd /tmp && rm -rf node_modules",  # destructive verb in a compound command
        "sudo rm /etc/hosts",
        "git branch -D feature/old",
        "git checkout --force main",
        "sudo systemctl stop nginx",  # unrecognised, but privileged -> still gates
        "/bin/rm secret.key",  # absolute path resolves to the rm basename
        "dd if=/dev/zero of=/dev/sda",
    ],
)
def test_classifier_flags_destructive(command: str) -> None:
    """rm -rf, git push, git reset --hard (and friends) classify destructive."""
    assert is_destructive("Bash", {"command": command}) is True


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat README.md",
        "git status",
        "git log --oneline -5",
        "git diff",
        "git reset HEAD~1",  # a non --hard reset keeps the working tree
        "grep -r jarvis src",
        "echo hello",
    ],
)
def test_classifier_passes_read_only_bash(command: str) -> None:
    """Read-only / non-mutating Bash never gates (gating it wrecks the voice UX)."""
    assert is_destructive("Bash", {"command": command}) is False


def test_classifier_ignores_non_bash_tools() -> None:
    """Read/Edit/Write are not classified destructive by the gate (Bash is the surface)."""
    assert is_destructive("Read", {"file_path": "/etc/passwd"}) is False
    assert is_destructive("Edit", {"file_path": "src/jarvis/loop.py"}) is False
    assert is_destructive("Grep", {"pattern": "rm -rf"}) is False


def test_classifier_handles_empty_and_wrapper_only_commands() -> None:
    """A command with no real program (empty, env-only, bare sudo) does not crash or gate."""
    assert is_destructive("Bash", {"command": ""}) is False
    assert is_destructive("Bash", {"command": "FOO=bar"}) is False
    assert is_destructive("Bash", {}) is False  # missing 'command' key
    # An unbalanced quote makes shlex raise; the classifier falls back to a plain split.
    assert is_destructive("Bash", {"command": 'echo "unbalanced'}) is False


# --- the decision emission (the Claude Code PreToolUse hook output) -------


def test_hook_blocks_until_confirmed() -> None:
    """Destructive: confirm()->False emits deny, ->True emits allow; confirm runs first."""
    payload = _payload("Bash", command="rm -rf build/")

    denied = _Confirm(verdict=False)
    decision = decide(payload, denied)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    # confirm() was consulted BEFORE the decision — the verdict is its return value.
    assert len(denied.questions) == 1

    approved = _Confirm(verdict=True)
    decision = decide(payload, approved)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert len(approved.questions) == 1


def test_hook_passes_through_safe() -> None:
    """A non-destructive payload is allowed and confirm() is never called."""
    confirm = _Confirm(verdict=False)
    decision = decide(_payload("Bash", command="ls -la"), confirm)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert confirm.questions == []  # never asked


def test_hook_reads_stdin_blocks_denied_via_exit_code() -> None:
    """Entrypoint parses the stdin payload; a denied call blocks via exit 2 + stderr.

    Verified live: Claude Code 2.1.150 does NOT block on a stdout ``deny`` JSON, so
    a denial must travel through the exit-code protocol (exit 2, reason on stderr).
    """
    stdin = io.StringIO(json.dumps(_payload("Bash", command="git push origin main")))
    stdout, stderr = io.StringIO(), io.StringIO()
    confirm = _Confirm(verdict=False)

    code = main(stdin=stdin, stdout=stdout, stderr=stderr, confirm=confirm)

    assert code == 2  # exit 2 is the block Claude Code honors
    assert "confirmation" in stderr.getvalue().lower()  # the reason is fed back to Claude
    assert stdout.getvalue() == ""  # nothing on stdout for a blocked call
    assert len(confirm.questions) == 1  # the gate spoke before deciding


def test_main_emits_allow_json_when_confirmed() -> None:
    """A confirmed destructive call is allowed via the documented stdout JSON at exit 0."""
    stdin = io.StringIO(json.dumps(_payload("Bash", command="rm -rf build")))
    stdout, stderr = io.StringIO(), io.StringIO()
    confirm = _Confirm(verdict=True)

    code = main(stdin=stdin, stdout=stdout, stderr=stderr, confirm=confirm)

    assert code == 0
    written = json.loads(stdout.getvalue())
    assert written["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert written["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert stderr.getvalue() == ""
    assert len(confirm.questions) == 1  # spoke before allowing


def test_main_allows_safe_call_from_stdin() -> None:
    """The entrypoint allows a read-only payload without consulting confirm()."""
    stdin = io.StringIO(json.dumps(_payload("Bash", command="git status")))
    stdout = io.StringIO()
    confirm = _Confirm(verdict=True)

    assert main(stdin=stdin, stdout=stdout, confirm=confirm) == 0
    assert json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert confirm.questions == []


def test_main_skips_live_confirm_build_on_non_destructive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-destructive calls must not construct the heavy live confirm (Kokoro + whisper).

    Once the hook is registered on the Bash matcher, every ``ls`` / ``git status``
    invokes ``main``. Building the live confirm there would make the hook so slow
    it would dominate every Bash call — so the fast path must short-circuit
    *before* ``build_live_confirm`` is ever touched.
    """
    from jarvis import permissions

    def _explode() -> object:
        raise AssertionError("build_live_confirm must not run on a non-destructive call")

    monkeypatch.setattr(permissions, "build_live_confirm", _explode)
    stdin = io.StringIO(json.dumps(_payload("Bash", command="ls -la")))
    stdout = io.StringIO()

    # confirm=None forces the real live-build path if the short-circuit is missing.
    assert main(stdin=stdin, stdout=stdout, confirm=None) == 0
    assert json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"] == "allow"


# --- the spoken question respects the persona ------------------------------


def test_summary_speaks_intent_not_the_command() -> None:
    """The confirmation summarizes intent — no command, flags, or paths read aloud."""
    question = summarize("Bash", {"command": "rm -rf /Users/ttotheq/.cache/jarvis"})
    assert "sir" in question.lower()
    assert question.rstrip().endswith("?")  # it asks for the go-ahead
    # No verbatim command/flags/paths leak into the spoken question.
    for fragment in ("rm", "-rf", "/Users/ttotheq", ".cache", "jarvis/"):
        assert fragment not in question


def test_summary_varies_by_action() -> None:
    """Different destructive actions get distinct, intent-level descriptions."""
    push = summarize("Bash", {"command": "git push origin main"})
    reset = summarize("Bash", {"command": "git reset --hard HEAD~1"})
    delete = summarize("Bash", {"command": "rm -rf build"})
    assert push != reset != delete
    assert "push" in push.lower()
    assert "discard" in reset.lower() or "uncommitted" in reset.lower()


def test_summary_falls_back_for_non_bash() -> None:
    """A non-Bash destructive action gets a generic intent description, still in persona."""
    question = summarize("SomeMcpTool", {"target": "prod"})
    assert "sir" in question.lower()
    assert question.rstrip().endswith("?")


def test_decide_allows_non_destructive_tool_without_asking() -> None:
    """A non-Bash tool call is allowed and never routed to a spoken confirmation."""
    confirm = _Confirm(verdict=False)
    decision = decide(_payload("Edit", file_path="src/jarvis/loop.py"), confirm)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert confirm.questions == []


# --- interpreting the spoken yes/no ---------------------------------------


@pytest.mark.parametrize(
    "transcript",
    ["Yes.", "Yes, go ahead.", "Go ahead, sir.", "Do it.", "Proceed.", "Yeah, confirm."],
)
def test_interpret_confirmation_accepts_affirmatives(transcript: str) -> None:
    assert interpret_confirmation(transcript) is True


@pytest.mark.parametrize(
    "transcript",
    ["No.", "No, stop.", "Cancel that.", "Don't.", "Abort.", "", "Hmm, I'm not sure."],
)
def test_interpret_confirmation_rejects_or_defaults_to_no(transcript: str) -> None:
    """Negatives and ambiguous/empty answers default to NO — never auto-run on doubt."""
    assert interpret_confirmation(transcript) is False


def test_interpret_confirmation_negative_overrides_affirmative() -> None:
    """A negative word anywhere wins — 'yes... no, stop' must not run the command."""
    assert interpret_confirmation("Yes — actually no, stop.") is False
