"""The brain: drive Claude Code headlessly and keep one session across turns.

Jarvis does not reimplement Claude Code (ADR-0003). It shells out to the real
``claude`` CLI in headless mode and treats it as the reasoning engine:

    claude -p "<transcript>" --output-format json \
        --permission-mode <mode> [--resume <session_id>]

The first call returns a ``session_id``; every later call passes
``--resume <session_id>`` so context carries turn to turn. Only natural-language
prose is forwarded to TTS — :func:`extract_speakable` strips fenced code,
tool-use blocks, and tool-result blocks, which are unlistenable read aloud.

The subprocess is injected as a :data:`Runner` value so the brain's argv
assembly, session handling, and extraction are all testable without spawning
``claude`` or hitting the network (see ``tests/test_brain_*.py``).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from jarvis.config import Settings, get_settings

#: A runner: takes a ``claude`` argv and returns its stdout. Injected in tests.
Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class BrainReply:
    """One turn's response from Claude Code."""

    text: str  # speakable prose, code/tool blocks stripped
    session_id: str  # the session to ``--resume`` on the next turn
    raw_result: str  # the unedited ``.result`` field, for logging/debugging


# Fenced code: ``` ... ``` or ~~~ ... ~~~ (the whole block, never spoken).
_FENCE = re.compile(r"(?:```|~~~).*?(?:```|~~~)", re.DOTALL)
# Tool-use / tool-result blocks, paired or self-closing.
_TOOL_BLOCK = re.compile(
    r"<tool_(?:use|result)\b[^>]*>.*?</tool_(?:use|result)>",
    re.DOTALL,
)
_TOOL_SELF_CLOSING = re.compile(r"<tool_(?:use|result)\b[^>]*/>")
# Inline `code` spans: keep the word, drop the backticks so TTS does not voice
# the word "backtick".
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BLANK_LINES = re.compile(r"\n{3,}")


def extract_speakable(text: str) -> str:
    """Reduce a Claude reply to the prose that is safe to read aloud.

    Strips fenced code blocks and tool-use/tool-result blocks entirely, unwraps
    inline code spans (keeping their words), and collapses the empty lines the
    removals leave behind.
    """
    text = _TOOL_BLOCK.sub("", text)
    text = _TOOL_SELF_CLOSING.sub("", text)
    text = _FENCE.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _default_runner(argv: list[str]) -> str:  # pragma: no cover - spawns a real process
    completed = subprocess.run(argv, capture_output=True, text=True, check=True)
    return completed.stdout


class Brain:
    """A multi-turn conversation with Claude Code over the headless CLI."""

    def __init__(self, settings: Settings | None = None, runner: Runner | None = None) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._runner = runner if runner is not None else _default_runner
        self._session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        """The current Claude session id, or ``None`` before the first turn."""
        return self._session_id

    def _build_argv(self, prompt: str) -> list[str]:
        argv = [
            self._settings.claude_binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            str(self._settings.permission_mode),
        ]
        if self._session_id is not None:
            argv += ["--resume", self._session_id]
        return argv

    def ask(self, prompt: str) -> BrainReply:
        """Send one turn to Claude Code and return its speakable reply.

        On the first call no ``--resume`` flag is sent; the returned session id
        is remembered and passed on every subsequent call.
        """
        stdout = self._runner(self._build_argv(prompt))
        payload = json.loads(stdout)
        raw_result = str(payload["result"])
        self._session_id = str(payload["session_id"])
        return BrainReply(
            text=extract_speakable(raw_result),
            session_id=self._session_id,
            raw_result=raw_result,
        )
