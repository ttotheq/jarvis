"""Live G3.2 eval: does the voice persona keep replies concise and code-free?

The :mod:`jarvis.persona` metric is pure and CI-tested, but CI cannot prove Claude
*obeys* the ``--append-system-prompt`` contract. This script is the judge: it runs
a fixed set of **neutral, throwaway factual prompts** through
``claude -p --append-system-prompt <persona> --output-format json`` — a fresh,
context-free session each (no ``--resume``) — extracts the speakable text, and
reports the G3.2 distribution (fraction ≤ 50 words, replies that leak code).

The prompts are deliberately neutral so the live brain never touches the QuanOS
vault or runs tools (ADR-0003: the brain is the *real* ``claude`` with full memory
and tool access). ``--record`` writes the captured (prompt, reply) pairs to
``tests/fixtures/persona/recorded.json`` (gitignored), which unlocks the live
assertion in ``tests/test_persona_eval.py``.

Usage::

    uv run python scripts/eval_persona.py --record

The live ``claude`` call happens only on direct execution; the subprocess is
injected (``runner``), so ``tests/test_persona_eval.py`` exercises the logic with a
fake and never touches the network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from jarvis.persona import (
    TARGET_CONCISE_RATE,
    VOICE_SYSTEM_PROMPT,
    WORD_CAP,
    PersonaReport,
    evaluate_persona,
)

#: A blocking runner: takes a ``claude`` argv and returns its full stdout.
Runner = Callable[[list[str]], str]

#: Where ``--record`` writes the captured set (gitignored; absent in CI).
RECORDED_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "persona" / "recorded.json"
)

#: 20 neutral, factual throwaway prompts. They must not reference the vault or ask
#: the brain to run tools — the live ``claude`` has full memory and tool access.
NEUTRAL_PROMPTS = [
    "What's the capital of France?",
    "How many continents are there?",
    "What year did the Apollo 11 moon landing happen?",
    "What's the boiling point of water at sea level?",
    "Who wrote Pride and Prejudice?",
    "What's the largest planet in the solar system?",
    "How many bones are in the adult human body?",
    "What's the speed of light?",
    "What's the chemical symbol for gold?",
    "Which language has the most native speakers?",
    "When did the Second World War end?",
    "What's the tallest mountain on Earth?",
    "How far is the Moon from Earth on average?",
    "What's the smallest prime number?",
    "Who painted the Mona Lisa?",
    "What's the freezing point of water in Fahrenheit?",
    "How many sides does a hexagon have?",
    "What gas do plants absorb from the atmosphere?",
    "What's the longest river in the world?",
    "What's the currency of Japan?",
]


def build_argv(binary: str, prompt: str) -> list[str]:
    """The argv the brain uses, with the persona injected and a fresh session.

    ``--permission-mode default`` (not the brain's ``acceptEdits``) keeps the eval
    conservative — neutral prompts never edit, and nothing should run unattended.
    No ``--resume``: each prompt is independent, so replies don't influence each
    other.
    """
    return [
        binary,
        "-p",
        prompt,
        "--permission-mode",
        "default",
        "--append-system-prompt",
        VOICE_SYSTEM_PROMPT,
        "--output-format",
        "json",
    ]


def _default_runner(argv: list[str]) -> str:  # pragma: no cover - spawns a real process
    completed = subprocess.run(argv, capture_output=True, text=True, check=True)
    return completed.stdout


def run_eval(
    prompts: Sequence[str],
    *,
    binary: str = "claude",
    runner: Runner = _default_runner,
) -> tuple[list[str], PersonaReport]:
    """Send each prompt to ``claude`` and score the replies against G3.2."""
    replies: list[str] = []
    for prompt in prompts:
        stdout = runner(build_argv(binary, prompt))
        replies.append(str(json.loads(stdout)["result"]))
    return replies, evaluate_persona(replies)


def _format_summary(report: PersonaReport) -> str:
    verdict = "PASS" if report.meets_target else "FAIL"
    return (
        f"persona eval over {report.total} reply(ies):\n"
        f"  within {WORD_CAP} words: {report.within_cap}/{report.total} "
        f"({report.concise_rate:.0%}, target >= {int(TARGET_CONCISE_RATE * 100)}%)\n"
        f"  replies leaking code to TTS: {report.spoke_code} (target 0)\n"
        f"  G3.2: {verdict}"
    )


def _record(prompts: Sequence[str], replies: Sequence[str], path: Path) -> None:
    pairs = [{"prompt": p, "reply": r} for p, r in zip(prompts, replies, strict=True)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"replies": pairs}, indent=2) + "\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = _default_runner,
    write: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description="Live G3.2 persona eval (neutral prompts).")
    parser.add_argument("--binary", default="claude", help="claude binary name")
    parser.add_argument(
        "--limit", type=int, default=None, help="run only the first N prompts (default: all 20)"
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help=f"write captured pairs to {RECORDED_PATH} (unlocks the live test)",
    )
    args = parser.parse_args(argv)

    prompts = NEUTRAL_PROMPTS[: args.limit] if args.limit else NEUTRAL_PROMPTS
    replies, report = run_eval(prompts, binary=args.binary, runner=runner)
    write(_format_summary(report))
    if args.record:
        _record(prompts, replies, RECORDED_PATH)
        write(f"recorded {len(replies)} pair(s) to {RECORDED_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by direct execution
    sys.exit(main())
