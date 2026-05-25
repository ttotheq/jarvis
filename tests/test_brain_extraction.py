"""Tests for speakable-text extraction (Phase 1 goal G1.3).

Written before ``jarvis.brain`` exists (TDD, per ADR-0005). The contract: given
Claude's reply text — which may contain fenced code, tool-use blocks, and
tool-result blocks — only natural-language prose reaches TTS. None of the
machine-readable noise (which is unlistenable read aloud) may survive.
"""

from __future__ import annotations

import json
from pathlib import Path

from jarvis.brain import extract_speakable

FIXTURES = Path(__file__).parent / "fixtures" / "claude"


def _load_result(name: str) -> str:
    payload = json.loads((FIXTURES / name).read_text())
    result: str = payload["result"]
    return result


def test_extract_strips_fenced_code() -> None:
    spoken = extract_speakable(_load_result("reply_with_code_and_tools.json"))
    assert "def add" not in spoken
    assert "```" not in spoken
    assert "return a + b" not in spoken


def test_extract_strips_tool_blocks() -> None:
    spoken = extract_speakable(_load_result("reply_with_code_and_tools.json"))
    assert "tool_use" not in spoken
    assert "tool_result" not in spoken
    assert "calc.py" not in spoken


def test_extract_keeps_prose() -> None:
    spoken = extract_speakable(_load_result("reply_with_code_and_tools.json"))
    assert "Right away, sir." in spoken
    assert "It's on your screen now." in spoken


def test_extract_unwraps_inline_code() -> None:
    # Inline `spans` keep their words but lose the backticks, so TTS does not
    # read the word "backtick" aloud.
    assert extract_speakable("Open the `config.py` file.") == "Open the config.py file."


def test_extract_collapses_blank_lines() -> None:
    # Removing blocks must not leave a ladder of empty lines behind.
    spoken = extract_speakable(_load_result("reply_with_code_and_tools.json"))
    assert "\n\n\n" not in spoken


def test_extract_strips_self_closing_tool_tag() -> None:
    spoken = extract_speakable('Done.\n<tool_use name="Bash" />\nAll set, sir.')
    assert "tool_use" not in spoken
    assert "Done." in spoken
    assert "All set, sir." in spoken
