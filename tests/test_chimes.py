"""Tests for status chimes — the eyes-free state-transition cues (Phase 4).

The tone generation and the state->chime mapping are pure and fully tested here;
the live wiring in ``jarvis.cli.run`` (playing through the real speaker) is the
manual hardware edge. A chime must be voiced at Kokoro's 24 kHz so it shares the
persistent output stream's sample rate — otherwise speech after a chime would be
mis-pitched.
"""

from __future__ import annotations

from jarvis.audio import Clip
from jarvis.chimes import (
    CHIME_SAMPLE_RATE,
    LISTENING,
    READY,
    THINKING,
    build_chime_observer,
    chime_for,
    make_tone,
)
from jarvis.loop import State


class FakeSpeaker:
    """Records what was played and whether the chime was drained."""

    def __init__(self) -> None:
        self.played: list[Clip] = []
        self.waits = 0
        self.stops = 0

    def play(self, clip: Clip) -> None:
        self.played.append(clip)

    def wait(self) -> None:
        self.waits += 1

    def stop(self) -> None:
        self.stops += 1


def _samples(clip: Clip) -> list[int]:
    return list(memoryview(clip.samples).cast("h"))


def test_chime_sample_rate_matches_kokoro() -> None:
    # Chimes share the persistent output stream with Kokoro (24 kHz); a mismatch
    # would mis-pitch the speech that follows a chime.
    assert CHIME_SAMPLE_RATE == 24_000


def test_make_tone_has_requested_rate_and_duration() -> None:
    clip = make_tone(440.0, 0.1)
    assert clip.sample_rate == CHIME_SAMPLE_RATE
    assert clip.num_samples == round(0.1 * CHIME_SAMPLE_RATE)


def test_make_tone_is_audible_and_int16_bounded() -> None:
    samples = _samples(make_tone(660.0, 0.05, amplitude=0.5))
    assert any(s != 0 for s in samples)  # not silent
    assert all(-32_768 <= s <= 32_767 for s in samples)


def test_make_tone_fades_the_tail_to_avoid_clicks() -> None:
    # A raw sine ending mid-oscillation clicks; a release envelope attenuates the
    # final milliseconds well below the global peak.
    samples = _samples(make_tone(440.0, 0.2, amplitude=0.9))
    peak = max(abs(s) for s in samples)
    tail = samples[-round(0.003 * CHIME_SAMPLE_RATE) :]  # last 3 ms
    assert max(abs(s) for s in tail) < peak * 0.5


def test_make_tone_fades_the_attack_to_avoid_clicks() -> None:
    samples = _samples(make_tone(440.0, 0.2, amplitude=0.9))
    peak = max(abs(s) for s in samples)
    head = samples[: round(0.003 * CHIME_SAMPLE_RATE)]  # first 3 ms
    assert max(abs(s) for s in head) < peak * 0.5


def test_named_chimes_are_distinct_audible_clips() -> None:
    for chime in (READY, LISTENING, THINKING):
        assert chime.sample_rate == CHIME_SAMPLE_RATE
        assert chime.num_samples > 0
        assert any(s != 0 for s in _samples(chime))
    assert READY.samples != LISTENING.samples
    assert LISTENING.samples != THINKING.samples
    assert READY.samples != THINKING.samples


def test_chime_for_maps_listening_and_thinking_only() -> None:
    # IDLE recurs (start-of-turn + end-of-turn) so it must not chime; SPEAKING is
    # Jarvis's own voice. Only the two in-turn cues map.
    assert chime_for(State.LISTENING) is LISTENING
    assert chime_for(State.THINKING) is THINKING
    assert chime_for(State.IDLE) is None
    assert chime_for(State.SPEAKING) is None


def test_observer_plays_mapped_chime_and_drains_when_enabled() -> None:
    speaker = FakeSpeaker()
    observe = build_chime_observer(speaker, enabled=True)
    observe(State.LISTENING)
    assert speaker.played == [LISTENING]
    # Drained: the tone finishes before LISTENING capture begins, so it cannot
    # bleed into the microphone.
    assert speaker.waits == 1


def test_observer_is_silent_for_unmapped_states() -> None:
    speaker = FakeSpeaker()
    observe = build_chime_observer(speaker, enabled=True)
    observe(State.SPEAKING)
    observe(State.IDLE)
    assert speaker.played == []
    assert speaker.waits == 0


def test_observer_is_silent_when_disabled() -> None:
    speaker = FakeSpeaker()
    observe = build_chime_observer(speaker, enabled=False)
    observe(State.LISTENING)
    observe(State.THINKING)
    assert speaker.played == []


def test_observer_dedupes_consecutive_identical_states() -> None:
    speaker = FakeSpeaker()
    observe = build_chime_observer(speaker, enabled=True)
    observe(State.THINKING)
    observe(State.THINKING)  # same state repeated -> only one chime
    assert speaker.played == [THINKING]


def test_observer_plays_without_drain_when_speaker_has_no_wait() -> None:
    # The Speaker protocol guarantees only play+stop; wait() is the streaming
    # speaker's drain. A speaker without wait must still chime cleanly.
    class MinimalSpeaker:
        def __init__(self) -> None:
            self.played: list[Clip] = []

        def play(self, clip: Clip) -> None:
            self.played.append(clip)

        def stop(self) -> None:
            pass

    speaker = MinimalSpeaker()
    observe = build_chime_observer(speaker, enabled=True)
    observe(State.LISTENING)
    assert speaker.played == [LISTENING]


def test_observer_replays_after_an_intervening_state() -> None:
    speaker = FakeSpeaker()
    observe = build_chime_observer(speaker, enabled=True)
    observe(State.LISTENING)
    observe(State.THINKING)
    observe(State.LISTENING)  # not consecutive -> chimes again
    assert speaker.played == [LISTENING, THINKING, LISTENING]
