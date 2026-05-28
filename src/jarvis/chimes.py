"""Status chimes — eyes-free audio cues for state transitions (Phase 4).

The always-on service runs headless with no terminal in view, so the runtime
signals its state with short tones: a *ready* cue at startup, a *listening* cue
when the wake phrase is acknowledged (your turn to speak), and a *thinking* cue
once capture ends and Claude is reasoning. They ride on the loop's ``on_state``
seam (:data:`jarvis.loop.VoiceLoop.on_state`).

The tones are **generated**, not sampled — a deliberate echo of the voice
persona's posture (``docs/voice-persona.md``): evoke the refined,
holographic-interface *feel* without copying any copyrighted film audio. Each
tone carries a short linear attack/release envelope so it never clicks, even on
the persistent Bluetooth output stream, and is voiced at Kokoro's 24 kHz so it
shares that stream's sample rate (a mismatch would mis-pitch the speech that
follows). Generation and the state->chime mapping are pure; only the live
playback in ``jarvis.cli.run`` is a hardware edge.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Callable

from jarvis.audio import Clip, Speaker
from jarvis.loop import State

#: Voiced at Kokoro's rate so chimes and speech share the persistent stream.
CHIME_SAMPLE_RATE = 24_000

#: Linear fade applied to each tone's start and end to suppress boundary clicks.
_ENVELOPE_MS = 8.0

_INT16_MAX = 32_767


def make_tone(
    frequency_hz: float,
    duration_s: float,
    *,
    amplitude: float = 0.35,
    sample_rate: int = CHIME_SAMPLE_RATE,
) -> Clip:
    """Render a single click-free sine tone as a mono PCM16 :class:`~jarvis.audio.Clip`.

    ``amplitude`` is a 0..1 fraction of full scale (kept modest so a cue never
    startles). A linear attack/release envelope of :data:`_ENVELOPE_MS` ramps the
    start and end to zero so the tone does not click at its boundaries.
    """
    total = round(duration_s * sample_rate)
    envelope = max(1, round(_ENVELOPE_MS / 1000.0 * sample_rate))
    peak = max(0.0, min(1.0, amplitude)) * _INT16_MAX
    samples = array("h")
    for n in range(total):
        gain = 1.0
        if n < envelope:  # attack
            gain = n / envelope
        elif n >= total - envelope:  # release
            gain = max(0, total - n - 1) / envelope
        value = peak * gain * math.sin(2.0 * math.pi * frequency_hz * n / sample_rate)
        samples.append(max(-_INT16_MAX, min(_INT16_MAX, round(value))))
    return Clip(samples=samples.tobytes(), sample_rate=sample_rate)


def _sequence(*clips: Clip) -> Clip:
    """Concatenate tones (same rate) into one clip — for multi-note motifs."""
    return Clip(
        samples=b"".join(clip.samples for clip in clips),
        sample_rate=clips[0].sample_rate,
    )


#: Startup: a low, settled tone — "powered up, listening for the wake phrase".
READY = make_tone(392.0, 0.16)

#: Wake acknowledged: a bright two-note *rising* motif — "your turn, go ahead".
LISTENING = _sequence(make_tone(587.0, 0.09), make_tone(880.0, 0.11))

#: Capture ended, Claude reasoning: a soft mid tone — "working on it".
THINKING = make_tone(523.0, 0.12, amplitude=0.28)

#: Which transitions chime. IDLE is intentionally absent: a turn enters IDLE both
#: at its start and its end, so chiming it would double-fire; the one-time READY
#: cue covers "ready" instead. SPEAKING is Jarvis's own voice, so it is silent.
_CHIMES: dict[State, Clip] = {
    State.LISTENING: LISTENING,
    State.THINKING: THINKING,
}


def chime_for(state: State) -> Clip | None:
    """Return the chime for a state, or ``None`` if that state is silent."""
    return _CHIMES.get(state)


def build_chime_observer(speaker: Speaker, *, enabled: bool) -> Callable[[State], None]:
    """Build an ``on_state`` observer that voices the mapped chime through ``speaker``.

    When ``enabled`` is false the observer is a no-op. Each chime is played and
    then **drained** (``speaker.wait()`` if available, like the loop's own drain)
    so it finishes before the loop's next action — critically, the LISTENING cue
    plays out before capture begins, so it cannot bleed into the microphone.
    Consecutive identical states are deduped so a repeated transition cannot
    retrigger the same cue.
    """
    drain = getattr(speaker, "wait", None)
    last: list[State | None] = [None]

    def observe(state: State) -> None:
        if not enabled or state == last[0]:
            return
        last[0] = state
        clip = chime_for(state)
        if clip is None:
            return
        speaker.play(clip)
        if callable(drain):
            drain()

    return observe
