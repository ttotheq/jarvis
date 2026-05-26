"""The conversation loop: a streaming IDLE→LISTENING→THINKING→SPEAKING machine.

This is the turn orchestrator (docs/architecture.md). Phase 1 was a synchronous
push-to-talk skeleton; Phase 2 (G2.4) makes it stream: the brain yields token
deltas, and TTS begins on the *first complete sentence* rather than waiting for
the whole reply. That overlap of THINKING and SPEAKING is concurrency — a
**producer** consumes the token stream and segments it into sentences onto a
queue, while a **consumer** speaks sentences off the queue. While playback of
sentence one blocks, the producer keeps pulling and segmenting later tokens.

Phase 4 wires the always-on entry point: the optional ``wait_for_wake`` seam
parks a turn at IDLE until the wake phrase (``wait_for_wake_phrase``), then
``record_turn`` captures the utterance until VAD end-of-speech
(``capture_until_endpoint``). Barge-in is Phase 3's cancellable SPEAKING state,
tightened in G4.0 so the hot mic only interrupts on the wake phrase while Jarvis
is talking. Every edge is injected, so the machine is tested without hardware
(tests/test_loop*.py, tests/test_always_on.py, tests/test_barge_in.py).
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from jarvis.audio import Clip, FrameSource, Speaker, resample_mono_pcm16
from jarvis.brain import SentenceStreamer
from jarvis.stt import Transcriber
from jarvis.tts import Synthesizer
from jarvis.vad import (
    FRAME_BYTES as VAD_FRAME_BYTES,
)
from jarvis.vad import (
    SAMPLE_RATE as VAD_SAMPLE_RATE,
)
from jarvis.vad import (
    Endpointer,
)
from jarvis.wakeword import (
    FRAME_BYTES as WAKEWORD_FRAME_BYTES,
)
from jarvis.wakeword import (
    SAMPLE_RATE as WAKEWORD_SAMPLE_RATE,
)
from jarvis.wakeword import (
    WakeWordListener,
)

logger = logging.getLogger(__name__)

#: A token stream: a prompt in, assistant text deltas out (``Brain.stream``).
TokenStream = Callable[[str], Iterator[str]]

#: A barge-in watcher: given an ``on_onset`` callback and a ``stop`` event, it
#: watches the hot mic and calls ``on_onset`` once on the wake phrase,
#: returning early if ``stop`` is set first (SPEAKING ended on its own).
#: Injected so tests fire barge-in on demand without a microphone.
BargeInWatcher = Callable[[Callable[[], None], threading.Event], None]


class Lazy[T]:
    """Build a value once, on first use, thread-safe — for deferred cold-start loads.

    The always-on cold start (G4.2) gates readiness on the wake detector alone:
    only the persistent mic and the openWakeWord listener must be live to hear
    "hey jarvis". The heavier components — Silero VAD, Kokoro, the barge-in
    watcher's second openWakeWord model, and the ``import torch`` they pull in —
    are needed only *after* a wake, so ``jarvis.cli.run`` wraps them in ``Lazy``
    and warms them in the background once the wake gate is up
    (:func:`warm_in_background`). ``get`` builds on first call and caches under a
    lock, so the background warm and a first real use serialize rather than race:
    a turn that starts before warm-up finishes simply blocks until the build is
    done instead of double-building.
    """

    def __init__(self, build: Callable[[], T]) -> None:
        self._build = build
        self._lock = threading.Lock()
        self._value: T | None = None
        self._ready = False

    def get(self) -> T:
        """Return the value, building it once on first call (blocking if mid-build)."""
        with self._lock:
            if not self._ready:
                self._value = self._build()
                self._ready = True
            return cast(T, self._value)


def warm_in_background(*lazies: Lazy[Any], name: str = "jarvis-warmup") -> threading.Thread:
    """Build every ``lazy`` on a daemon thread; return the thread (joinable in tests).

    Called after the wake gate is up and the ready message is printed, so the
    multi-second Kokoro + torch loads overlap the user's walk-up-and-speak time
    rather than blocking readiness (G4.2).
    """

    def _warm() -> None:
        for lazy in lazies:
            lazy.get()

    thread = threading.Thread(target=_warm, name=name, daemon=True)
    thread.start()
    return thread


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
    #: Optional wake gate; when set, each turn opens at IDLE and blocks here until
    #: the wake phrase is heard before entering LISTENING (the always-on runtime).
    #: ``None`` keeps the developer-harness behaviour: a turn opens at LISTENING.
    wait_for_wake: Callable[[], None] | None = None
    #: Optional barge-in watcher; when set, the mic stays hot during SPEAKING and
    #: the wake phrase cancels playback + the in-flight stream (G3.1/G4.0).
    #: ``None`` keeps the Phase 2 behaviour: SPEAKING runs to completion.
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
        SPEAKING. With a ``wait_for_wake`` gate, the turn first parks at IDLE
        until the wake phrase is heard (the always-on runtime).
        """
        if self.wait_for_wake is not None:
            self._enter(State.IDLE)
            self.wait_for_wake()
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
        """Overlap THINKING and SPEAKING as a pipeline; cancel on barge-in (G2.4/G3.1/G4.6).

        Three stages run concurrently so a multi-sentence reply plays as one
        continuous utterance: a **producer** pulls token deltas and segments them
        into sentences (:class:`SentenceStreamer`); a **synth** stage renders each
        sentence to audio *ahead* of playback; and this (consumer) thread plays
        the rendered clips in order. Decoupling synthesis from playback means
        sentence N+1 is ready the instant N finishes, so there is no inter-sentence
        gap. A ``None`` sentinel flows down each stage to mark end-of-stream; a
        producer or synth exception is captured and re-raised here.

        On a clean finish the consumer drains the speaker (optional ``wait``) so
        buffered audio is heard before the turn ends. When a barge-in watcher is
        wired and fires, it sets a cancel flag and aborts the speaker
        (``speaker.stop()``, so latency is bounded by the abort rather than the
        clip length); every stage then unwinds and the producer closes the token
        stream — in :class:`~jarvis.brain.Brain.stream` that terminates the
        ``claude`` child. The onset/halt timestamps come from the injected clock.
        """
        sentences: queue.Queue[str | None] = queue.Queue()
        clips: queue.Queue[tuple[str, Clip] | None] = queue.Queue()
        error: list[BaseException] = []
        cancel = threading.Event()  # set on barge-in: stop every stage
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
                # so the synth stage never blocks waiting for it.
                try:
                    closer = getattr(token_stream, "close", None)
                    if callable(closer):
                        closer()
                finally:
                    sentences.put(None)  # end-of-stream sentinel

        def synthesize_ahead() -> None:
            # Render each sentence to audio before playback needs it. Empty
            # sentences are skipped (never voiced); the None sentinel is forwarded
            # so the consumer can stop.
            try:
                while not cancel.is_set():
                    sentence = sentences.get()
                    if sentence is None:
                        break
                    if cancel.is_set():
                        break
                    if sentence.strip():
                        clips.put((sentence, self.synthesize(sentence)))
            except BaseException as exc:  # re-raised on the consumer thread
                error.append(exc)
            finally:
                clips.put(None)  # end-of-stream sentinel for the consumer

        def trigger_barge_in() -> None:
            nonlocal onset_at, halt_at
            onset_at = self.clock()
            # Set the flag *before* aborting: stop() unblocks the consumer's play(),
            # so cancel must already be visible or the consumer could grab the next
            # clip before it sees the interrupt.
            cancel.set()
            self.speaker.stop()  # abort buffered audio to bound latency
            halt_at = self.clock()

        producer = threading.Thread(target=produce, name="jarvis-brain-stream", daemon=True)
        synth = threading.Thread(target=synthesize_ahead, name="jarvis-tts-synth", daemon=True)
        producer.start()
        synth.start()

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
                item = clips.get()
                if item is None or cancel.is_set():
                    break
                sentence, clip = item
                if not speaking:
                    self._enter(State.SPEAKING)
                    speaking = True
                self.speaker.play(clip)
                spoken.append(sentence)
        finally:
            # On a clean finish, drain buffered audio before leaving SPEAKING; on
            # barge-in (cancel already set) the speaker was aborted instead.
            if speaking and not cancel.is_set():
                drain = getattr(self.speaker, "wait", None)
                if callable(drain):
                    drain()
            cancel.set()  # idempotent: also unblocks the producer + synth stages
            done_speaking.set()  # release the watcher if it is still waiting
            producer.join()
            synth.join()
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


def _pcm16_rms(frame: bytes) -> float:
    """A normalized RMS level for debug instrumentation of watcher input."""
    samples = memoryview(frame).cast("h")
    if not samples:
        return 0.0
    mean_square = sum(int(sample) * int(sample) for sample in samples) / len(samples)
    return math.sqrt(float(mean_square)) / 32768.0


def _coerce_wakeword_frame(frame: bytes) -> bytes:
    """Pad or trim a frame so openWakeWord receives its exact required geometry."""
    if len(frame) == WAKEWORD_FRAME_BYTES:
        return frame
    if len(frame) > WAKEWORD_FRAME_BYTES:
        return frame[:WAKEWORD_FRAME_BYTES]
    return frame + b"\x00" * (WAKEWORD_FRAME_BYTES - len(frame))


def _to_wakeword_frame(raw_frame: bytes, source_sample_rate: int) -> bytes:
    """Resample a raw mic frame to 16 kHz (if needed) and coerce to wake geometry.

    Shared by the IDLE wake gate and the SPEAKING barge-in watcher so the two
    paths feed openWakeWord identically.
    """
    frame = raw_frame
    if source_sample_rate != WAKEWORD_SAMPLE_RATE:
        frame = resample_mono_pcm16(
            raw_frame,
            input_rate=source_sample_rate,
            output_rate=WAKEWORD_SAMPLE_RATE,
        )
    return _coerce_wakeword_frame(frame)


def build_wake_phrase_barge_in_watcher(
    source: FrameSource,
    *,
    listener: WakeWordListener,
    source_sample_rate: int = WAKEWORD_SAMPLE_RATE,
    reset_source: Callable[[], None] | None = None,
) -> BargeInWatcher:
    """Build the pure barge-in watcher: shared mic frames -> wake phrase.

    The source and listener are injected so the barge-in decision remains
    unit-testable. The live path wires a persistent sounddevice mic plus the
    openWakeWord detector on top of this seam.
    """

    def watch(on_onset: Callable[[], None], stop: threading.Event) -> None:
        if reset_source is not None:
            reset_source()
        reset = getattr(listener.detect, "reset", None)
        if callable(reset):
            reset()
        while not stop.is_set():
            try:
                raw_frame = source()
            except Exception:
                logger.exception("barge-in wake watch read_error=1 during SPEAKING")
                raise
            frame = _to_wakeword_frame(raw_frame, source_sample_rate)
            score = listener.score(frame)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "barge-in wake watch rms=%.4f score=%.3f read_error=0 "
                    "raw_bytes=%d frame_bytes=%d",
                    _pcm16_rms(frame),
                    score,
                    len(raw_frame),
                    len(frame),
                )
            if score >= listener.threshold:
                logger.info("barge-in wake phrase detected during SPEAKING score=%.3f", score)
                on_onset()
                return

    return watch


def build_default_barge_in_watcher(
    source: FrameSource | None = None,
    *,
    source_sample_rate: int | None = None,
    reset_source: Callable[[], None] | None = None,
) -> BargeInWatcher:  # pragma: no cover - real mic + openWakeWord
    """Wire the live barge-in watcher: hot mic -> openWakeWord wake phrase."""
    from jarvis.audio import make_sounddevice_source
    from jarvis.config import get_settings
    from jarvis.wakeword import FRAME_SAMPLES, SAMPLE_RATE, build_default_detector

    settings = get_settings()
    sample_rate = settings.sample_rate if source_sample_rate is None else source_sample_rate
    if source is None:
        block_frames = max(1, round(sample_rate * FRAME_SAMPLES / SAMPLE_RATE))
        source = make_sounddevice_source(
            sample_rate,
            block_frames=block_frames,
            device=settings.input_device,
        )
    listener = WakeWordListener(
        detect=build_default_detector(),
        threshold=settings.wake_threshold,
    )
    return build_wake_phrase_barge_in_watcher(
        source,
        listener=listener,
        source_sample_rate=sample_rate,
        reset_source=reset_source,
    )


def _reset(obj: object) -> None:
    """Call ``obj.reset()`` if it exists — detectors/endpointers between turns."""
    reset = getattr(obj, "reset", None)
    if callable(reset):
        reset()


def wait_for_wake_phrase(
    source: FrameSource,
    *,
    listener: WakeWordListener,
    source_sample_rate: int = WAKEWORD_SAMPLE_RATE,
    reset_source: Callable[[], None] | None = None,
) -> None:
    """Block reading mic frames until the wake phrase scores at/above threshold.

    The IDLE primitive of the always-on runtime. Each raw frame is resampled and
    coerced to openWakeWord's geometry, then scored; the call returns on the first
    frame to cross ``listener.threshold``. Source and listener are injected so the
    wake gate is unit-tested without a microphone.
    """
    if reset_source is not None:
        reset_source()
    _reset(listener.detect)
    while True:
        frame = _to_wakeword_frame(source(), source_sample_rate)
        score = listener.score(frame)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("idle wake watch rms=%.4f score=%.3f", _pcm16_rms(frame), score)
        if score >= listener.threshold:
            logger.info("wake phrase detected score=%.3f", score)
            return


def capture_until_endpoint(
    source: FrameSource,
    *,
    endpointer: Endpointer,
    sample_rate: int,
    max_seconds: float,
    reset_source: Callable[[], None] | None = None,
) -> Clip:
    """Capture one utterance, ending on VAD trailing silence (or a duration cap).

    The LISTENING primitive of the always-on runtime. Raw frames are accumulated
    into the returned :class:`~jarvis.audio.Clip` at the capture ``sample_rate``;
    a copy is resampled to Silero's 16 kHz and re-chunked into its exact
    512-sample frames — any remainder is carried across reads, since the mic's
    block size need not be a multiple of the VAD frame — then fed to the
    ``Endpointer``. Capture stops on the endpoint, or once ``max_seconds`` of
    audio has been read (a safety cap against a stuck endpointer). Source and
    endpointer are injected so the re-chunking is unit-tested without hardware.
    """
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    if reset_source is not None:
        reset_source()
    endpointer.reset()
    _reset(endpointer.detect)

    chunks: list[bytes] = []
    vad_buffer = bytearray()
    captured_samples = 0
    max_samples = round(max_seconds * sample_rate)
    fired = False
    while not fired and captured_samples < max_samples:
        raw = source()
        chunks.append(raw)
        captured_samples += len(raw) // 2
        vad_bytes = (
            raw
            if sample_rate == VAD_SAMPLE_RATE
            else resample_mono_pcm16(raw, input_rate=sample_rate, output_rate=VAD_SAMPLE_RATE)
        )
        vad_buffer.extend(vad_bytes)
        while len(vad_buffer) >= VAD_FRAME_BYTES:
            frame = bytes(vad_buffer[:VAD_FRAME_BYTES])
            del vad_buffer[:VAD_FRAME_BYTES]
            if endpointer.feed(frame):
                fired = True
                break
    return Clip(samples=b"".join(chunks), sample_rate=sample_rate)


def build_wait_for_wake(  # pragma: no cover - real mic + openWakeWord
    source: FrameSource | None = None,
    *,
    source_sample_rate: int | None = None,
    reset_source: Callable[[], None] | None = None,
) -> Callable[[], None]:
    """Wire the live IDLE wake gate: hot mic -> openWakeWord "hey jarvis"."""
    from jarvis.audio import make_sounddevice_source
    from jarvis.config import get_settings
    from jarvis.wakeword import FRAME_SAMPLES, SAMPLE_RATE, build_default_listener

    settings = get_settings()
    sample_rate = settings.sample_rate if source_sample_rate is None else source_sample_rate
    if source is None:
        block_frames = max(1, round(sample_rate * FRAME_SAMPLES / SAMPLE_RATE))
        source = make_sounddevice_source(
            sample_rate, block_frames=block_frames, device=settings.input_device
        )
    listener = build_default_listener(settings)
    mic: FrameSource = source

    def _wait() -> None:
        wait_for_wake_phrase(
            mic,
            listener=listener,
            source_sample_rate=sample_rate,
            reset_source=reset_source,
        )

    return _wait


def build_vad_record_turn(  # pragma: no cover - real mic + Silero VAD
    source: FrameSource | None = None,
    *,
    sample_rate: int | None = None,
    max_seconds: float | None = None,
    reset_source: Callable[[], None] | None = None,
) -> Callable[[], Clip]:
    """Wire the live LISTENING capture: hot mic -> Silero VAD endpointing."""
    from jarvis.audio import make_sounddevice_source
    from jarvis.config import get_settings
    from jarvis.vad import build_default_endpointer
    from jarvis.wakeword import FRAME_SAMPLES, SAMPLE_RATE

    settings = get_settings()
    rate = settings.sample_rate if sample_rate is None else sample_rate
    cap = settings.listen_max_seconds if max_seconds is None else max_seconds
    if source is None:
        block_frames = max(1, round(rate * FRAME_SAMPLES / SAMPLE_RATE))
        source = make_sounddevice_source(
            rate, block_frames=block_frames, device=settings.input_device
        )
    endpointer = build_default_endpointer(settings)
    mic: FrameSource = source

    def _record() -> Clip:
        return capture_until_endpoint(
            mic,
            endpointer=endpointer,
            sample_rate=rate,
            max_seconds=cap,
            reset_source=reset_source,
        )

    return _record
