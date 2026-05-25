"""Push-to-talk conversation loop (Phase 1 walking skeleton).

This is the synchronous, turn-based orchestrator: capture one utterance,
transcribe it, ask the brain, speak the reply, repeat. It is deliberately small
— no wake word, VAD, streaming, or barge-in (those grow this module in Phases
2-3, per docs/architecture.md). Every edge is injected, so the loop is tested
without hardware.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jarvis.audio import Clip, Speaker
from jarvis.brain import Brain
from jarvis.stt import Transcriber
from jarvis.tts import Synthesizer, speak


@dataclass(frozen=True)
class Turn:
    """The record of one push-to-talk exchange."""

    transcript: str  # what the user said
    reply: str  # the speakable text Claude returned
    spoke: bool  # whether anything was voiced (False on a blank turn)


@dataclass
class VoiceLoop:
    """Wires capture -> STT -> brain -> TTS for one turn, and repeats."""

    record_turn: Callable[[], Clip]
    transcribe: Transcriber
    brain: Brain
    synthesize: Synthesizer
    speaker: Speaker

    def one_turn(self) -> Turn:
        """Run a single exchange. A blank transcript skips the brain entirely."""
        clip = self.record_turn()
        transcript = self.transcribe(clip)
        if not transcript.strip():
            return Turn(transcript=transcript, reply="", spoke=False)
        reply = self.brain.ask(transcript)
        spoke = speak(reply.text, self.synthesize, self.speaker)
        return Turn(transcript=transcript, reply=reply.text, spoke=spoke)

    def converse(self, should_continue: Callable[[int], bool]) -> list[Turn]:
        """Run turns while ``should_continue(turns_done)`` is true.

        The CLI passes a predicate that runs until interrupted; tests pass a
        bounded one to drive a fixed number of exchanges.
        """
        turns: list[Turn] = []
        while should_continue(len(turns)):
            turns.append(self.one_turn())
        return turns
