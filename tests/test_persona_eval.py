"""Tests for the voice persona and its conciseness metric (Phase 3 goal G3.2).

Written before ``jarvis.persona`` exists (TDD, per ADR-0005). The persona is the
``--append-system-prompt`` voice contract from ``docs/voice-persona.md``: spoken
replies stay short (≤ 50 words), never read code/paths/output aloud, lead with the
decision-relevant point, and confirm destructive actions first.

The G3.2 metric is *pure* and exercised here over committed fixtures. The metric
cannot prove Claude *obeys* the prompt — that is the live 20-prompt eval
(``scripts/eval_persona.py``), whose recorded set is absent in CI, so that path
skips. The eval script's subprocess is injected, so nothing here spawns ``claude``
or touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import eval_persona
import pytest

from jarvis.brain import Brain, extract_speakable
from jarvis.config import Settings
from jarvis.persona import (
    VOICE_SYSTEM_PROMPT,
    WORD_CAP,
    PersonaReport,
    count_words,
    evaluate_persona,
    voice_system_prompt,
)

FIXTURES = Path(__file__).parent / "fixtures" / "persona"
COMMITTED = FIXTURES / "replies.json"
RECORDED = FIXTURES / "recorded.json"


def _settings() -> Settings:
    return Settings(claude_binary="claude-test")


class _FakeRunner:
    """Records each argv and returns canned ``claude -p`` JSON, in order."""

    def __init__(self, results: list[str]) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        return self._results.pop(0)


class _FakeStreamRunner:
    """Records each argv and replays canned ``stream-json`` lines, in order."""

    def __init__(self, line_groups: list[list[str]]) -> None:
        self._groups = list(line_groups)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> list[str]:
        self.calls.append(list(argv))
        return self._groups.pop(0)


def _json(result: str, session_id: str = "sess-1") -> str:
    return json.dumps({"type": "result", "session_id": session_id, "result": result})


def _result_event(text: str, session_id: str = "sess-1") -> str:
    return json.dumps(
        {"type": "result", "subtype": "success", "result": text, "session_id": session_id}
    )


# --- the persona contract -------------------------------------------------


def test_persona_prompt_encodes_contract() -> None:
    """The voice prompt is non-empty and encodes the speakable-output directives."""
    prompt = voice_system_prompt()
    assert prompt
    assert prompt == VOICE_SYSTEM_PROMPT
    lowered = prompt.lower()
    # Concise, with the explicit ≤ 50-word cap (the G3.2 target).
    assert "50 words" in lowered
    assert "concise" in lowered or "brief" in lowered
    # Never read code / paths / output aloud.
    assert "code" in lowered
    assert "path" in lowered
    assert "aloud" in lowered
    # Lead with the decision-relevant point.
    assert "decision" in lowered
    # Confirm destructive / irreversible actions before running.
    assert "destructive" in lowered or "irreversible" in lowered
    assert "confirm" in lowered
    # Advisor register, addresses the user as "sir".
    assert "advisor" in lowered
    assert "sir" in lowered
    # Runtime self-awareness: a background daemon with no terminal/screen of
    # its own — so it cannot rely on a permission prompt landing somewhere
    # the user can see (fixed 2026-05-27 after live failures opening
    # ~/.claude/CLAUDE.md and Pages.app).
    assert "daemon" in lowered or "no terminal" in lowered or "no screen" in lowered
    # Open-in-editor rule for visual access: replaces the original "on your
    # screen, sir" example, which assumed an interactive terminal that no
    # longer exists under the launchd service.
    assert "open" in lowered
    # Do not theater-confirm safe actions — just run them. Verbal "yes" can
    # only ever be ordinary conversation, never a real permission grant
    # (the real gate is jarvis.permissions, and it only fires on destructive
    # Bash anyway).
    assert "non-destructive" in lowered or "safe" in lowered


# --- the flag is wired into both call shapes ------------------------------


def test_brain_ask_injects_persona() -> None:
    runner = _FakeRunner([_json("Paris, sir.")])
    brain = Brain(settings=_settings(), runner=runner)
    brain.ask("capital of France?")
    argv = runner.calls[0]
    assert argv[argv.index("--append-system-prompt") + 1] == VOICE_SYSTEM_PROMPT


def test_brain_stream_injects_persona() -> None:
    runner = _FakeStreamRunner([[_result_event("Paris, sir.")]])
    brain = Brain(settings=_settings(), stream_runner=runner)
    list(brain.stream("capital of France?"))
    argv = runner.calls[0]
    assert argv[argv.index("--append-system-prompt") + 1] == VOICE_SYSTEM_PROMPT


# --- the pure metric primitives -------------------------------------------


def test_count_words_counts_speakable_tokens() -> None:
    assert count_words("Paris, sir.") == 2
    assert count_words("") == 0
    assert count_words("one two three four five") == 5


def test_evaluate_persona_measures_on_speakable_text() -> None:
    """Code is stripped before measuring; a stripped reply is short and code-free."""
    reply = "Done, sir.\n```python\nx = 1\n```\nIt's on your screen."
    assert "```" not in extract_speakable(reply)  # paired fence is stripped
    report = evaluate_persona([reply])
    assert report.total == 1
    assert report.spoke_code == 0
    assert report.within_cap == 1
    assert report.meets_target


def test_evaluate_persona_flags_surviving_fence_as_spoken_code() -> None:
    """An *unclosed* fence survives whole-string extraction — that is the bug it catches."""
    leaked = "Here's the snippet, sir:\n```python\nx = 1"  # no closing fence
    report = evaluate_persona([leaked])
    assert report.spoke_code == 1
    assert report.meets_target is False


def test_evaluate_persona_word_cap_and_rate() -> None:
    long_reply = " ".join(["word"] * (WORD_CAP + 10))  # over the cap
    short = "Yes, sir."
    report = evaluate_persona(
        [short, short, short, short, short, short, short, short, short, long_reply]
    )
    assert report.total == 10
    assert report.within_cap == 9
    assert report.concise_rate == pytest.approx(0.9)
    assert report.meets_target  # exactly 90% passes, 0 code


def test_evaluate_persona_below_target_when_too_verbose() -> None:
    long_reply = " ".join(["word"] * (WORD_CAP + 1))
    report = evaluate_persona([long_reply, long_reply, "Yes, sir."])
    assert report.concise_rate == pytest.approx(1 / 3)
    assert report.meets_target is False


def test_evaluate_persona_empty_is_well_defined() -> None:
    report = evaluate_persona([])
    assert report.total == 0
    assert report.concise_rate == 1.0  # vacuously concise, never divide-by-zero
    assert report.meets_target


# --- the committed fixtures meet the target -------------------------------


def test_persona_responses_are_concise() -> None:
    """≥ 90% of the committed exemplar replies are ≤ 50 words and none read code."""
    data = json.loads(COMMITTED.read_text())
    replies = [pair["reply"] for pair in data["replies"]]
    assert len(replies) >= 20, "G3.2 measures over a 20-reply set"
    report = evaluate_persona(replies)
    assert report.concise_rate >= 0.9, f"only {report.concise_rate:.0%} ≤ {WORD_CAP} words"
    assert report.spoke_code == 0, f"{report.spoke_code} replies leaked code to TTS"
    assert report.meets_target


# --- the live eval skips without a recorded set ---------------------------


def test_persona_eval_skips_without_recorded_set() -> None:
    """The live 20-prompt eval is gated on a recorded set; absent it, CI skips."""
    if not RECORDED.exists():
        pytest.skip("live persona eval not recorded (run scripts/eval_persona.py --record)")
    data = json.loads(RECORDED.read_text())
    replies = [pair["reply"] for pair in data["replies"]]
    assert len(replies) >= 20, "the live eval runs 20 neutral prompts"
    report = evaluate_persona(replies)
    assert report.meets_target, (
        f"{report.concise_rate:.0%} ≤ {WORD_CAP} words, {report.spoke_code} leaked code"
    )


# --- the eval script builds the persona-injected argv ----------------------


def test_eval_script_uses_neutral_prompts() -> None:
    assert len(eval_persona.NEUTRAL_PROMPTS) >= 20
    blob = " ".join(eval_persona.NEUTRAL_PROMPTS).lower()
    # Neutral throwaway factual prompts only — must not touch the vault or tools.
    for forbidden in ("quanos", "vault", "delete", "rm ", "git ", "push"):
        assert forbidden not in blob


def test_eval_script_injects_persona_and_no_resume() -> None:
    argv = eval_persona.build_argv("claude-test", "What's the capital of France?")
    assert argv[0] == "claude-test"
    assert "-p" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == VOICE_SYSTEM_PROMPT
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--resume" not in argv  # each eval prompt is a fresh, context-free session


def test_eval_run_collects_replies_and_report() -> None:
    captured: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        captured.append(argv)
        return _json("A concise answer, sir.")

    replies, report = eval_persona.run_eval(["q1", "q2", "q3"], binary="claude-test", runner=runner)
    assert replies == ["A concise answer, sir."] * 3
    assert len(captured) == 3
    assert isinstance(report, PersonaReport)
    assert report.total == 3
    assert report.meets_target


def test_eval_main_runs_without_live_subprocess() -> None:
    def runner(_argv: list[str]) -> str:
        return _json("Two words.")

    printed: list[str] = []
    code = eval_persona.main(
        ["--binary", "claude-test", "--limit", "2"],
        runner=runner,
        write=printed.append,
    )
    assert code == 0
    assert any("50 words" in line or "concise" in line.lower() for line in printed)
