"""The conversation loop: a streaming IDLE→LISTENING→THINKING→SPEAKING machine.

This is the turn orchestrator (docs/architecture.md). Phase 1 was a synchronous
push-to-talk skeleton; Phase 2 (G2.4) makes it stream: the brain yields token
deltas, and TTS begins on the *first complete sentence* rather than waiting for
the whole reply. That overlap of THINKING and SPEAKING is concurrency — a
**producer** consumes the token stream and segments it into sentences onto a
queue, while a **consumer** speaks sentences off the queue. While playback of
sentence one blocks, the producer keeps pulling and segmenting later tokens.

Wake word (IDLE→LISTENING) and VAD endpointing (LISTENING→THINKING) are separate
Phase 2 goals; here ``record_turn`` still captures one utterance. Barge-in is
Phase 3 — SPEAKING runs to completion before returning to IDLE. Every edge is
injected, so the machine is tested without hardware (tests/test_loop*.py).
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from jarvis.audio import Clip, Speaker
from jarvis.brain import SentenceStreamer
from jarvis.stt import Transcriber
from jarvis.tts import Synthesizer, speak

#: A token stream: a prompt in, assistant text deltas out (``Brain.stream``).
TokenStream = Callable[[str], Iterator[str]]


class State(StrEnum):
    """The orchestrator's states (docs/architecture.md runtime state machine)."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"


@dataclass(frozen=True)
class Turn:
    """The record of one exchange."""

    transcript: str  # what the user said
    reply: str  # the speakable text spoken back (sentences joined)
    spoke: bool  # whether anything was voiced (False on a blank/code-only turn)


@dataclass
class VoiceLoop:
    """Wires capture -> STT -> streaming brain -> sentence-by-sentence TTS."""

    record_turn: Callable[[], Clip]
    transcribe: Transcriber
    stream: TokenStream
    synthesize: Synthesizer
    speaker: Speaker
    #: Optional observer notified on every state transition (used in tests).
    on_state: Callable[[State], None] | None = None

    def _enter(self, state: State) -> None:
        if self.on_state is not None:
            self.on_state(state)

    def one_turn(self) -> Turn:
        """Run a single exchange through the state machine.

        A blank transcript skips the brain entirely (LISTENING→IDLE). A reply
        with no speakable prose (e.g. all code) reaches THINKING but never
        SPEAKING.
        """
        self._enter(State.LISTENING)
        clip = self.record_turn()
        transcript = self.transcribe(clip)
        if not transcript.strip():
            self._enter(State.IDLE)
            return Turn(transcript=transcript, reply="", spoke=False)

        self._enter(State.THINKING)
        spoken = self._think_and_speak(transcript)
        self._enter(State.IDLE)
        return Turn(transcript=transcript, reply=" ".join(spoken), spoke=bool(spoken))

    def _think_and_speak(self, transcript: str) -> list[str]:
        """Overlap THINKING and SPEAKING: stream tokens, speak sentences as ready.

        The producer thread pulls token deltas, segments them with
        :class:`SentenceStreamer`, and queues complete sentences; this (consumer)
        thread speaks them in order. A ``None`` sentinel marks end-of-stream. A
        producer exception is captured and re-raised here so the caller sees it.
        """
        sentences: queue.Queue[str | None] = queue.Queue()
        error: list[BaseException] = []

        def produce() -> None:
            streamer = SentenceStreamer()
            try:
                for delta in self.stream(transcript):
                    for sentence in streamer.feed(delta):
                        sentences.put(sentence)
                for sentence in streamer.flush():
                    sentences.put(sentence)
            except BaseException as exc:  # re-raised on the consumer thread
                error.append(exc)
            finally:
                sentences.put(None)  # end-of-stream sentinel

        producer = threading.Thread(target=produce, name="jarvis-brain-stream", daemon=True)
        producer.start()

        spoken: list[str] = []
        speaking = False
        try:
            while True:
                sentence = sentences.get()
                if sentence is None:
                    break
                if not speaking:
                    self._enter(State.SPEAKING)
                    speaking = True
                speak(sentence, self.synthesize, self.speaker)
                spoken.append(sentence)
        finally:
            producer.join()

        if error:
            raise error[0]
        return spoken

    def converse(self, should_continue: Callable[[int], bool]) -> list[Turn]:
        """Run turns while ``should_continue(turns_done)`` is true.

        The CLI passes a predicate that runs until interrupted; tests pass a
        bounded one to drive a fixed number of exchanges.
        """
        turns: list[Turn] = []
        while should_continue(len(turns)):
            turns.append(self.one_turn())
        return turns
