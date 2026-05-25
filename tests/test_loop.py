"""Tests for the conversation loop's orchestration contract.

The loop wires capture -> STT -> brain token stream -> sentence-by-sentence TTS
and repeats. Every edge (mic, transcriber, token stream, synthesizer, speaker)
is injected, so orchestration is exercised without hardware. The streaming
overlap, state machine, and concurrency specifics live in
``tests/test_loop_streaming.py``; here we cover the turn/Turn contract that the
CLI depends on.
"""

from __future__ import annotations

from collections.abc import Iterator

from jarvis.audio import Clip
from jarvis.loop import VoiceLoop


class FakeSpeaker:
    def __init__(self) -> None:
        self.played: list[Clip] = []

    def play(self, clip: Clip) -> None:
        self.played.append(clip)


def _clip() -> Clip:
    return Clip(samples=b"\x00\x00", sample_rate=16_000)


def _loop(transcripts: list[str], replies: list[str], speaker: FakeSpeaker) -> VoiceLoop:
    scripted = iter(transcripts)
    replies_iter = iter(replies)

    def stream(_prompt: str) -> Iterator[str]:
        # One reply per turn, delivered as a single trailing-space-terminated
        # delta so it forms exactly one spoken sentence.
        yield next(replies_iter)

    return VoiceLoop(
        record_turn=_clip,
        transcribe=lambda _clip: next(scripted),
        stream=stream,
        synthesize=lambda text: Clip(samples=text.encode(), sample_rate=24_000),
        speaker=speaker,
    )


def test_one_turn_returns_transcript_and_reply() -> None:
    speaker = FakeSpeaker()
    loop = _loop(["what time is it"], ["Half past three, sir. "], speaker)
    turn = loop.one_turn()
    assert turn.transcript == "what time is it"
    assert turn.reply == "Half past three, sir."
    assert turn.spoke is True
    assert len(speaker.played) == 1


def test_converse_drives_distinct_turns() -> None:
    speaker = FakeSpeaker()
    transcripts = [f"question {n}" for n in range(3)]
    replies = [f"answer {n}, sir. " for n in range(3)]
    loop = _loop(transcripts, replies, speaker)
    turns = loop.converse(should_continue=lambda done: done < 3)
    assert [t.transcript for t in turns] == transcripts
    assert [t.reply for t in turns] == [f"answer {n}, sir." for n in range(3)]
    assert len(speaker.played) == 3


def test_converse_stops_when_predicate_is_false() -> None:
    speaker = FakeSpeaker()
    loop = _loop([], [], speaker)
    turns = loop.converse(should_continue=lambda _done: False)
    assert turns == []
    assert speaker.played == []
