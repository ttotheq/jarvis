"""Tests for the push-to-talk conversation loop (Phase 1 goal G1.1).

The loop wires capture -> STT -> brain -> TTS for one turn and repeats. Every
edge (mic, transcriber, brain runner, synthesizer, speaker) is injected, so the
orchestration is exercised end-to-end without hardware: G1.1's *logic* (>= 5
consecutive turns, no crash) is verified here; the live recorded session is the
manual check noted in the phase Outcomes.
"""

from __future__ import annotations

import json

from jarvis.audio import Clip
from jarvis.brain import Brain
from jarvis.loop import VoiceLoop


class FakeRunner:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        return json.dumps({"session_id": "sess-1", "result": self._replies.pop(0)})


class FakeSpeaker:
    def __init__(self) -> None:
        self.played: list[Clip] = []

    def play(self, clip: Clip) -> None:
        self.played.append(clip)


def _clip() -> Clip:
    return Clip(samples=b"\x00\x00", sample_rate=16_000)


def _loop(transcripts: list[str], replies: list[str], speaker: FakeSpeaker) -> VoiceLoop:
    scripted = iter(transcripts)
    return VoiceLoop(
        record_turn=_clip,
        transcribe=lambda _clip: next(scripted),
        brain=Brain(runner=FakeRunner(replies)),
        synthesize=lambda text: Clip(samples=text.encode(), sample_rate=24_000),
        speaker=speaker,
    )


def test_one_turn_returns_transcript_and_reply() -> None:
    speaker = FakeSpeaker()
    loop = _loop(["what time is it"], ["Half past three, sir."], speaker)
    turn = loop.one_turn()
    assert turn.transcript == "what time is it"
    assert turn.reply == "Half past three, sir."
    assert turn.spoke is True
    assert len(speaker.played) == 1


def test_five_consecutive_exchanges_no_crash() -> None:
    speaker = FakeSpeaker()
    transcripts = [f"question {n}" for n in range(5)]
    replies = [f"answer {n}, sir." for n in range(5)]
    loop = _loop(transcripts, replies, speaker)
    turns = loop.converse(should_continue=lambda done: done < 5)
    assert len(turns) == 5
    assert [t.reply for t in turns] == replies
    assert len(speaker.played) == 5


def test_second_turn_resumes_session() -> None:
    speaker = FakeSpeaker()
    runner = FakeRunner(["first, sir.", "second, sir."])
    scripted = iter(["hello", "again"])
    loop = VoiceLoop(
        record_turn=_clip,
        transcribe=lambda _clip: next(scripted),
        brain=Brain(runner=runner),
        synthesize=lambda text: Clip(samples=b"", sample_rate=24_000),
        speaker=speaker,
    )
    loop.converse(should_continue=lambda done: done < 2)
    assert "--resume" in runner.calls[1]


def test_blank_transcript_skips_brain() -> None:
    speaker = FakeSpeaker()
    runner = FakeRunner([])  # would IndexError if the brain were called
    loop = VoiceLoop(
        record_turn=_clip,
        transcribe=lambda _clip: "   ",
        brain=Brain(runner=runner),
        synthesize=lambda text: Clip(samples=b"", sample_rate=24_000),
        speaker=speaker,
    )
    turn = loop.one_turn()
    assert turn.reply == ""
    assert turn.spoke is False
    assert runner.calls == []
    assert speaker.played == []
