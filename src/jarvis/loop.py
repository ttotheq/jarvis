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
Phase 3 (G3.1): the mic stays hot during SPEAKING, and an injected onset watcher
can cancel playback and the in-flight ``claude`` stream mid-sentence, returning to
LISTENING. Every edge is injected, so the machine is tested without hardware
(tests/test_loop*.py, tests/test_barge_in.py).
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from jarvis.audio import Clip, Speaker
from jarvis.brain import SentenceStreamer
from jarvis.stt import Transcriber
from jarvis.tts import Synthesizer, speak

#: A token stream: a prompt in, assistant text deltas out (``Brain.stream``).
TokenStream = Callable[[str], Iterator[str]]

#: A barge-in watcher: given an ``on_onset`` callback and a ``stop`` event, it
#: watches the hot mic and calls ``on_onset`` once on the first speech onset,
#: returning early if ``stop`` is set first (SPEAKING ended on its own). Injected
#: so tests fire onset on demand without a microphone.
BargeInWatcher = Callable[[Callable[[], None], threading.Event], None]


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
    barged_in: bool = False  # whether the user interrupted SPEAKING (G3.1)
    barge_in_latency_s: float | None = None  # onset -> playback halted, if barged in


@dataclass(frozen=True)
class _SpeakResult:
    """The outcome of one SPEAKING phase: what was voiced and how it ended."""

    spoken: list[str]
    barged_in: bool
    latency_s: float | None


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
    #: Optional barge-in watcher; when set, the mic stays hot during SPEAKING and
    #: speech onset cancels playback + the in-flight stream (G3.1). ``None`` keeps
    #: the Phase 2 behaviour: SPEAKING runs to completion.
    watch_barge_in: BargeInWatcher | None = None
    #: Clock for barge-in latency (onset -> playback halted); injected in tests.
    clock: Callable[[], float] = time.perf_counter

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
        result = self._think_and_speak(transcript)
        # A barge-in returns to LISTENING (the user is now speaking); a clean
        # finish returns to IDLE.
        self._enter(State.LISTENING if result.barged_in else State.IDLE)
        return Turn(
            transcript=transcript,
            reply=" ".join(result.spoken),
            spoke=bool(result.spoken),
            barged_in=result.barged_in,
            barge_in_latency_s=result.latency_s,
        )

    def _think_and_speak(self, transcript: str) -> _SpeakResult:
        """Overlap THINKING and SPEAKING; cancel both on barge-in (G2.4 + G3.1).

        The producer thread pulls token deltas, segments them with
        :class:`SentenceStreamer`, and queues complete sentences; this (consumer)
        thread speaks them in order. A ``None`` sentinel marks end-of-stream. A
        producer exception is captured and re-raised here so the caller sees it.

        When a barge-in watcher is wired, an onset thread runs on the hot mic. On
        speech onset it aborts the in-flight clip (``speaker.stop()``, so latency
        is bounded by ``stop`` rather than the sentence length) and sets a cancel
        flag; the consumer then stops and the producer breaks its loop and closes
        the token stream — in :class:`~jarvis.brain.Brain.stream` that terminates
        the ``claude`` child. The onset/halt timestamps come from the injected
        clock.
        """
        sentences: queue.Queue[str | None] = queue.Queue()
        error: list[BaseException] = []
        cancel = threading.Event()  # set on barge-in: stop consumer + producer
        done_speaking = threading.Event()  # set when SPEAKING ends: stop the watcher
        token_stream = self.stream(transcript)

        onset_at: float | None = None
        halt_at: float | None = None

        def produce() -> None:
            streamer = SentenceStreamer()
            try:
                for delta in token_stream:
                    if cancel.is_set():  # barge-in: stop pulling tokens
                        break
                    for sentence in streamer.feed(delta):
                        sentences.put(sentence)
                else:  # ran to completion (no barge-in): voice the trailing prose
                    for sentence in streamer.flush():
                        sentences.put(sentence)
            except BaseException as exc:  # re-raised on the consumer thread
                error.append(exc)
            finally:
                # Owning thread closes the generator: GeneratorExit unwinds
                # Brain.stream and terminates the claude child. Plain iterators
                # (e.g. in tests) have no close(); the sentinel is sent regardless
                # so the consumer never blocks waiting for it.
                try:
                    closer = getattr(token_stream, "close", None)
                    if callable(closer):
                        closer()
                finally:
                    sentences.put(None)  # end-of-stream sentinel

        def trigger_barge_in() -> None:
            nonlocal onset_at, halt_at
            onset_at = self.clock()
            # Set the flag *before* aborting the clip: stop() unblocks the
            # consumer's play(), so cancel must already be visible or the consumer
            # could grab the next sentence before it sees the interrupt.
            cancel.set()
            self.speaker.stop()  # abort the in-flight clip to bound latency
            halt_at = self.clock()

        producer = threading.Thread(target=produce, name="jarvis-brain-stream", daemon=True)
        producer.start()

        watcher: threading.Thread | None = None
        if self.watch_barge_in is not None:
            watcher = threading.Thread(
                target=self.watch_barge_in,
                args=(trigger_barge_in, done_speaking),
                name="jarvis-barge-in",
                daemon=True,
            )
            watcher.start()

        spoken: list[str] = []
        speaking = False
        try:
            while not cancel.is_set():
                sentence = sentences.get()
                if sentence is None or cancel.is_set():
                    break
                if not speaking:
                    self._enter(State.SPEAKING)
                    speaking = True
                speak(sentence, self.synthesize, self.speaker)
                spoken.append(sentence)
        finally:
            cancel.set()  # idempotent: also unblocks the producer's token loop
            done_speaking.set()  # release the watcher if it is still waiting
            producer.join()
            if watcher is not None:
                watcher.join(timeout=1.0)

        if error:
            raise error[0]
        latency = halt_at - onset_at if onset_at is not None and halt_at is not None else None
        return _SpeakResult(spoken=spoken, barged_in=onset_at is not None, latency_s=latency)

    def converse(self, should_continue: Callable[[int], bool]) -> list[Turn]:
        """Run turns while ``should_continue(turns_done)`` is true.

        The CLI passes a predicate that runs until interrupted; tests pass a
        bounded one to drive a fixed number of exchanges.
        """
        turns: list[Turn] = []
        while should_continue(len(turns)):
            turns.append(self.one_turn())
        return turns


def build_default_barge_in_watcher() -> BargeInWatcher:  # pragma: no cover - real mic + Silero
    """Wire the live barge-in watcher: hot mic -> Silero onset detector.

    Reads 512-sample frames from the input device (Silero's required frame size)
    and fires ``on_onset`` on the first frame scoring at/above ``vad_threshold``,
    so the user can interrupt mid-reply. Returns when ``stop`` is set (SPEAKING
    ended on its own) so the thread is never left running.
    """
    from jarvis.audio import make_sounddevice_source
    from jarvis.config import get_settings
    from jarvis.vad import FRAME_SAMPLES, SAMPLE_RATE, OnsetDetector, build_default_detector

    settings = get_settings()

    def watch(on_onset: Callable[[], None], stop: threading.Event) -> None:
        source = make_sounddevice_source(
            SAMPLE_RATE, block_frames=FRAME_SAMPLES, device=settings.input_device
        )
        onset = OnsetDetector(detect=build_default_detector(), threshold=settings.vad_threshold)
        while not stop.is_set():
            if onset.feed(source()):
                on_onset()
                return

    return watch
