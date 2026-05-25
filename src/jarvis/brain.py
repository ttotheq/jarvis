"""The brain: drive Claude Code headlessly and keep one session across turns.

Jarvis does not reimplement Claude Code (ADR-0003). It shells out to the real
``claude`` CLI in headless mode and treats it as the reasoning engine. Two call
shapes are used:

- :meth:`Brain.ask` — blocking, ``--output-format json``; returns one
  :class:`BrainReply` (Phase 1, and the session-continuity check, G1.4).
- :meth:`Brain.stream` — ``--output-format stream-json
  --include-partial-messages``; *yields assistant text deltas* as they arrive so
  TTS can begin on the first sentence (Phase 2, G2.4).

The first call returns a ``session_id``; every later call passes
``--resume <session_id>`` so context carries turn to turn. Only natural-language
prose is forwarded to TTS — :class:`SentenceStreamer` is a *stateful* filter that
strips fenced code and tool blocks and emits complete, speakable sentences at
their boundaries as the stream flows. :func:`extract_speakable` is the
whole-string form used where the full reply is already in hand.

Both subprocess shapes are injected (:data:`Runner`, :data:`StreamRunner`) so
argv assembly, session handling, and extraction are all testable without
spawning ``claude`` or hitting the network (see ``tests/test_brain_*.py`` and
``tests/test_speakable_stream.py``).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from jarvis.config import Settings, get_settings

#: A blocking runner: takes a ``claude`` argv and returns its full stdout.
Runner = Callable[[list[str]], str]
#: A streaming runner: takes an argv and yields stdout lines as they arrive.
StreamRunner = Callable[[list[str]], Iterator[str]]


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
    """Reduce a whole Claude reply to the prose that is safe to read aloud.

    Strips fenced code blocks and tool-use/tool-result blocks entirely, unwraps
    inline code spans (keeping their words), and collapses the empty lines the
    removals leave behind. For the streaming path use :class:`SentenceStreamer`.
    """
    text = _TOOL_BLOCK.sub("", text)
    text = _TOOL_SELF_CLOSING.sub("", text)
    text = _FENCE.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


# --- Streaming speakable filter -------------------------------------------

# Abbreviations whose trailing dot is NOT a sentence boundary. Stored without the
# trailing dot, lower-cased; internal dots ("i.e") are kept for the lookup.
_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "approx",
        "inc",
        "ltd",
        "co",
        "corp",
        "dept",
        "fig",
        "no",
        "gen",
        "col",
        "capt",
        "sgt",
        "lt",
        "rev",
        "hon",
        "messrs",
        "i.e",
        "e.g",
        "a.m",
        "p.m",
        "u.s",
        "u.k",
        "ph.d",
    }
)
# A sentence terminator: one or more of . ! ? plus any trailing closing quotes.
_TERMINATOR = re.compile(r"""[.!?]+["')\]]*""")
# The word ending immediately before a terminating dot (letters, internal dots).
_TRAILING_WORD = re.compile(r"[A-Za-z][A-Za-z.]*$")
# An opening tool tag (paired or self-closing).
_TOOL_OPEN = re.compile(r"<tool_(use|result)\b[^>]*?(/?)>")


def _is_partial_marker(rest: str) -> bool:
    """True if ``rest`` (the unconsumed tail) might still grow into a marker.

    Holding these back avoids splitting a fence/tool marker across token deltas
    (e.g. seeing ``` arrive as ``​` then `​`` then `​``).
    """
    if rest in ("`", "``", "~", "~~"):
        return True
    if rest.startswith("<") and ">" not in rest:
        return (
            rest.startswith("<tool_")
            or "<tool_use".startswith(rest)
            or "<tool_result".startswith(rest)
        )
    return False


class SentenceStreamer:
    """Turn a stream of assistant text deltas into complete, speakable sentences.

    Fed token deltas via :meth:`feed`, it tracks whether it is inside a fenced
    code block, a tool block, or an inline code span, and emits a sentence only
    once it is **confirmed safe** (outside any such construct) **and** a sentence
    boundary is reached. A code fence that opens and never closes is therefore
    never spoken. :meth:`flush` releases the trailing partial sentence at
    end-of-stream.
    """

    def __init__(self) -> None:
        self._raw = ""
        self._emitted = 0  # chars of the settled clean text already emitted

    def feed(self, delta: str) -> list[str]:
        """Append a token delta; return any sentences now complete and safe."""
        self._raw += delta
        return self._drain(final=False)

    def flush(self) -> list[str]:
        """End the stream: emit the trailing partial sentence, if any prose remains."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[str]:
        clean = self._settled_clean(final=final)
        tail = clean[self._emitted :]
        sentences, consumed = _emit_sentences(tail, final=final)
        self._emitted += consumed
        return sentences

    def _settled_clean(self, *, final: bool) -> str:
        """The prose that is settled (outside any open construct) and safe to scan.

        Fenced code, tool blocks, and inline-code backticks are removed. Content
        inside a construct that is still open is withheld; if the stream ends
        with it still open (``final``), that content is dropped — never spoken.
        """
        raw = self._raw
        out: list[str] = []
        i, n = 0, len(raw)
        while i < n:
            if raw.startswith("```", i) or raw.startswith("~~~", i):
                marker = raw[i : i + 3]
                close = raw.find(marker, i + 3)
                if close == -1:  # fence still open: withhold everything from here
                    break
                i = close + 3
                continue
            tool = _TOOL_OPEN.match(raw, i)
            if tool:
                if tool.group(2) == "/":  # self-closing
                    i = tool.end()
                    continue
                close_tag = f"</tool_{tool.group(1)}>"
                close = raw.find(close_tag, tool.end())
                if close == -1:  # tool block still open: withhold the rest
                    break
                i = close + len(close_tag)
                continue
            if not final and _is_partial_marker(raw[i:]):
                break
            if raw[i] == "`":  # single backtick: inline code span
                close = raw.find("`", i + 1)
                if close == -1:
                    if final:  # unterminated span at end: drop the stray backtick
                        out.append(raw[i + 1 :])
                        i = n
                        continue
                    break  # withhold until the span closes
                out.append(raw[i + 1 : close])  # unwrap: keep inner, drop backticks
                i = close + 1
                continue
            out.append(raw[i])
            i += 1
        return _BLANK_LINES.sub("\n\n", "".join(out))


def _is_boundary(text: str, dot: int, end: int, *, final: bool) -> bool:
    """Whether the terminator at ``text[dot:end]`` is a true sentence boundary."""
    n = len(text)
    # A '.' between two digits is a decimal point (3.14), not a boundary.
    if text[dot] == "." and 0 < dot < n - 1 and text[dot - 1].isdigit() and text[dot + 1].isdigit():
        return False
    # A known abbreviation's trailing dot ("Mr.", "e.g.") is not a boundary.
    word = _TRAILING_WORD.search(text[:dot])
    if word and word.group(0).rstrip(".").lower() in _ABBREVIATIONS:
        return False
    # The boundary is confirmed only by following whitespace; at the very end of
    # the buffer it is confirmed only once the stream has finished (``final``).
    if end < n:
        return text[end].isspace()
    return final


def _emit_sentences(tail: str, *, final: bool) -> tuple[list[str], int]:
    """Split ``tail`` into complete sentences; return them and the chars consumed.

    A sentence is emitted only at a confirmed boundary (see :func:`_is_boundary`).
    Trailing whitespace is consumed so the next sentence starts clean. At
    ``final``, any leftover prose is emitted as the last sentence even without a
    terminator.
    """
    sentences: list[str] = []
    n = len(tail)
    start = 0  # start of the current (not-yet-emitted) sentence
    search = 0
    while True:
        m = _TERMINATOR.search(tail, search)
        if not m:
            break
        dot, end = m.start(), m.end()
        if not _is_boundary(tail, dot, end, final=final):
            if end >= n and not final:
                break  # unconfirmed terminator at the tail: wait for more
            search = end
            continue
        sentence = tail[start:end].strip()
        if sentence:
            sentences.append(sentence)
        j = end
        while j < n and tail[j].isspace():  # consume trailing whitespace
            j += 1
        start = j
        search = j
    if final and start < n:
        rest = tail[start:].strip()
        if rest:
            sentences.append(rest)
        start = n
    return sentences, start


def _default_runner(argv: list[str]) -> str:  # pragma: no cover - spawns a real process
    completed = subprocess.run(argv, capture_output=True, text=True, check=True)
    return completed.stdout


def _default_stream_runner(argv: list[str]) -> Iterator[str]:  # pragma: no cover - real process
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if proc.stdout is None:  # defensive; PIPE is always set
        raise RuntimeError("subprocess produced no stdout pipe")
    try:
        yield from proc.stdout
    finally:
        # On early termination (consumer stopped), don't leave the child running.
        if proc.poll() is None:
            proc.terminate()
        proc.stdout.close()
        proc.wait()


class Brain:
    """A multi-turn conversation with Claude Code over the headless CLI."""

    def __init__(
        self,
        settings: Settings | None = None,
        runner: Runner | None = None,
        stream_runner: StreamRunner | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._runner = runner if runner is not None else _default_runner
        self._stream_runner = stream_runner if stream_runner is not None else _default_stream_runner
        self._session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        """The current Claude session id, or ``None`` before the first turn."""
        return self._session_id

    def _base_argv(self, prompt: str) -> list[str]:
        return [
            self._settings.claude_binary,
            "-p",
            prompt,
            "--permission-mode",
            str(self._settings.permission_mode),
        ]

    def _build_argv(self, prompt: str) -> list[str]:
        argv = self._base_argv(prompt)
        argv[3:3] = ["--output-format", "json"]
        if self._session_id is not None:
            argv += ["--resume", self._session_id]
        return argv

    def _build_stream_argv(self, prompt: str) -> list[str]:
        # `--output-format stream-json` with `-p` requires `--verbose`.
        argv = self._base_argv(prompt)
        argv[3:3] = ["--output-format", "stream-json", "--include-partial-messages", "--verbose"]
        if self._session_id is not None:
            argv += ["--resume", self._session_id]
        return argv

    def ask(self, prompt: str) -> BrainReply:
        """Send one turn to Claude Code (blocking) and return its speakable reply.

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

    def stream(self, prompt: str) -> Iterator[str]:
        """Send one turn and yield assistant *text* deltas as they arrive.

        Only ``text_delta`` content is yielded — tool-call deltas and session/init
        events are skipped. The session id is captured from the events (so the
        next turn can ``--resume``); iteration stops at the ``result`` event.
        """
        for line in self._stream_runner(self._build_stream_argv(prompt)):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = event.get("session_id")
            if session_id:
                self._session_id = str(session_id)
            event_type = event.get("type")
            if event_type == "result":
                break
            if event_type != "stream_event":
                continue
            inner = event.get("event", {})
            if inner.get("type") != "content_block_delta":
                continue
            delta = inner.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text")
                if text:
                    yield str(text)
