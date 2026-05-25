"""Tests for the stateful streaming speakable filter (Phase 2 goal G2.4).

Written before ``jarvis.brain.SentenceStreamer`` exists (TDD, per ADR-0005).
The contract: token deltas are fed in as they arrive from Claude's
``stream-json`` stream, and the streamer emits *complete, safe-to-speak
sentences* — and only those — at sentence boundaries. It must:

- emit a sentence only once a real boundary is reached (buffer partials);
- never split inside abbreviations ("Mr.") or decimals ("3.14");
- strip fenced code and tool blocks, and — critically — a code fence that opens
  mid-stream and never closes must *never* be spoken;
- unwrap inline ``code`` spans (keep the word, drop the backticks);
- flush the trailing partial sentence at end-of-stream.
"""

from __future__ import annotations

from jarvis.brain import SentenceStreamer


def _drain(deltas: list[str]) -> list[str]:
    """Feed every delta then flush; return all emitted sentences in order."""
    streamer = SentenceStreamer()
    out: list[str] = []
    for delta in deltas:
        out.extend(streamer.feed(delta))
    out.extend(streamer.flush())
    return out


def test_emits_complete_sentence_at_boundary() -> None:
    streamer = SentenceStreamer()
    # A terminator followed by whitespace confirms the boundary.
    assert streamer.feed("Half past three, sir. ") == ["Half past three, sir."]


def test_buffers_partial_sentence_until_boundary() -> None:
    streamer = SentenceStreamer()
    assert streamer.feed("Half past") == []
    assert streamer.feed(" three, sir. ") == ["Half past three, sir."]


def test_unterminated_text_held_until_flush() -> None:
    streamer = SentenceStreamer()
    # No trailing whitespace, so the boundary is not yet confirmed mid-stream.
    assert streamer.feed("Right away, sir.") == []
    assert streamer.flush() == ["Right away, sir."]


def test_does_not_split_on_abbreviation() -> None:
    assert _drain(["Mr. Stark will see you now. "]) == ["Mr. Stark will see you now."]


def test_does_not_split_on_decimal() -> None:
    assert _drain(["Pi is about 3.14 today, sir. "]) == ["Pi is about 3.14 today, sir."]


def test_emits_multiple_sentences_in_order() -> None:
    out = _drain(["Yes, sir. ", "It is done. ", "Anything else?"])
    assert out == ["Yes, sir.", "It is done.", "Anything else?"]


def test_strips_closed_fenced_code() -> None:
    out = _drain(["Done. ", "```py\nx = 1\n```", " Saved, sir. "])
    assert out == ["Done.", "Saved, sir."]
    assert all("x = 1" not in s and "```" not in s for s in out)


def test_unclosed_fence_is_never_spoken() -> None:
    # The trap: a fence opens mid-stream and the stream ends before it closes.
    # The prose before it is spoken; the fence content never is.
    out = _drain(["Here is the code. ", "```python\nsecret = 42\n"])
    assert out == ["Here is the code."]
    assert all("secret" not in s for s in out)


def test_fence_only_reply_is_silent() -> None:
    assert _drain(["```python\nprint('hi')\n```"]) == []


def test_fence_marker_split_across_deltas_is_stripped() -> None:
    # The triple-backtick marker arrives one character at a time.
    out = _drain(["Before. ", "`", "`", "`secret```", " After. "])
    assert out == ["Before.", "After."]
    assert all("secret" not in s for s in out)


def test_unwraps_inline_code() -> None:
    assert _drain(["Run `npm test` now, sir. "]) == ["Run npm test now, sir."]


def test_strips_tool_block() -> None:
    out = _drain(['Working on it. <tool_use name="Bash">ls</tool_use> Done. '])
    assert out == ["Working on it.", "Done."]
    assert all("tool_use" not in s and "ls" not in s for s in out)


def test_flush_emits_trailing_partial_without_terminator() -> None:
    streamer = SentenceStreamer()
    out = streamer.feed("First. ")
    out += streamer.feed("A trailing thought")
    out += streamer.flush()
    assert out == ["First.", "A trailing thought"]


def test_flush_is_empty_when_nothing_pending() -> None:
    streamer = SentenceStreamer()
    streamer.feed("All done, sir. ")
    assert streamer.flush() == []


def test_strips_self_closing_tool_tag() -> None:
    out = _drain(['Done. <tool_use name="Bash" /> All set, sir. '])
    assert out == ["Done.", "All set, sir."]
    assert all("tool_use" not in s for s in out)


def test_unclosed_tool_block_is_never_spoken() -> None:
    out = _drain(["Working. ", '<tool_use name="Bash">rm -rf /'])
    assert out == ["Working."]
    assert all("rm -rf" not in s and "tool_use" not in s for s in out)


def test_tool_marker_split_across_deltas_is_held() -> None:
    # A tool tag arriving piecemeal must not leak its prefix as spoken text.
    out = _drain(["Ready. ", "<tool", "_use ", 'name="Bash">ls</tool_use>', " Gone. "])
    assert out == ["Ready.", "Gone."]
    assert all("tool" not in s and "ls" not in s for s in out)


def test_unterminated_inline_code_at_end_drops_backtick() -> None:
    # A stray opening backtick at end-of-stream is dropped, not voiced.
    assert _drain(["The flag is `verbose"]) == ["The flag is verbose"]
