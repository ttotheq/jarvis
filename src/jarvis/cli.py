"""Command-line entry point for Jarvis.

Commands available today are intentionally minimal; the conversational
runtime (``jarvis run``) and environment self-check (``jarvis doctor``) are
introduced in Phase 0/1 per ``docs/phases/``.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from jarvis import __version__
from jarvis.audio import Clip
from jarvis.config import get_settings
from jarvis.doctor import run_doctor

app = typer.Typer(
    name="jarvis",
    help="A local-first, voice-controlled interface for Claude Code.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the installed Jarvis version."""
    typer.echo(__version__)


@app.command()
def config() -> None:
    """Print the resolved runtime configuration (defaults + env + .env)."""
    settings = get_settings()
    for key, value in settings.model_dump().items():
        typer.echo(f"{key} = {value}")


@app.command()
def doctor() -> None:
    """Check the local voice stack (PortAudio, whisper.cpp, openWakeWord, Kokoro).

    Exits non-zero and names any missing dependency; exits 0 when all present.
    """
    raise typer.Exit(code=run_doctor(write=typer.echo))


def _continue_for(max_turns: int | None) -> Callable[[int], bool]:
    """Build the loop predicate: run forever (``None``) or stop after N turns."""
    if max_turns is None:
        return lambda _done: True
    return lambda done: done < max_turns


def _push_to_talk_record_turn(  # pragma: no cover - requires a real microphone
    sample_rate: int,
) -> Callable[[], Clip]:
    """Build a record-one-turn callable: Enter to start, Enter again to stop."""
    import threading

    from jarvis.audio import make_sounddevice_source, record

    def _record() -> Clip:
        input("Press Enter, speak, then press Enter again to send… ")
        stop = threading.Event()

        def _watch_for_stop() -> None:
            input()
            stop.set()

        threading.Thread(target=_watch_for_stop, daemon=True).start()
        source = make_sounddevice_source(sample_rate)
        return record(source, stop=stop.is_set, sample_rate=sample_rate)

    return _record


# A guided script for the hands-free session: each line is spoken aloud and you
# repeat it, so there's no guesswork about what to say. Line 4 deliberately
# tests session continuity (it refers back to line 2) — G1.1 + live --resume.
_GUIDED_PROMPTS = (
    "Hello Jarvis, can you hear me?",
    "My name is Ty. Please remember it.",
    "What is two plus two?",
    "What did I tell you my name was?",
    "Thank you, that is all for now.",
)


def _timed_record_turn(  # pragma: no cover - requires a real microphone
    sample_rate: int,
    seconds: float,
    prompts: tuple[str, ...] = (),
) -> Callable[[], Clip]:
    """Build a hands-free record-one-turn callable: spoken cue, beep, fixed window.

    Works in non-interactive shells (no keyboard), unlike the Enter-gated mode.
    If ``prompts`` is given, the matching line is spoken (and printed) before each
    turn so you know exactly what to repeat.
    """
    import shutil
    import subprocess
    import time

    from jarvis.audio import make_sounddevice_source, record

    say = shutil.which("say")
    turn = {"i": 0}

    def _record() -> Clip:
        i = turn["i"]
        turn["i"] += 1
        line = prompts[i] if i < len(prompts) else None
        if line is not None:
            typer.echo(f"\n[turn {i + 1}] Repeat aloud:  {line!r}")
        if say is not None:
            cue = f"Repeat after the beep: {line}" if line else "Your turn. Speak after the beep."
            subprocess.run([say, cue], check=False)
            subprocess.run([say, "[[volm 0.4]] beep"], check=False)
        source = make_sounddevice_source(sample_rate)
        deadline = time.monotonic() + seconds
        return record(source, stop=lambda: time.monotonic() >= deadline, sample_rate=sample_rate)

    return _record


@app.command()
def run() -> None:  # pragma: no cover - end-to-end hardware path, checked manually
    """Hold a push-to-talk spoken conversation with Claude Code.

    Push a key, speak, hear the reply; repeat until Ctrl-C. The local voice stack
    must be installed — run ``jarvis doctor`` first. Set ``JARVIS_PTT_SECONDS`` for
    a hands-free timed turn (and ``JARVIS_MAX_TURNS`` to stop after N turns) when no
    interactive keyboard is available. The loop's logic is covered by
    tests/test_loop.py; this wiring is the manual end-to-end check (G1.1).
    """
    from jarvis.brain import Brain
    from jarvis.loop import VoiceLoop, build_default_barge_in_watcher
    from jarvis.stt import WhisperCppTranscriber
    from jarvis.tts import build_default_speaker, build_default_synthesizer

    settings = get_settings()
    if settings.ptt_seconds is not None:
        record_turn = _timed_record_turn(
            settings.sample_rate, settings.ptt_seconds, prompts=_GUIDED_PROMPTS
        )
    else:
        record_turn = _push_to_talk_record_turn(settings.sample_rate)
    loop = VoiceLoop(
        record_turn=record_turn,
        transcribe=WhisperCppTranscriber(settings),
        stream=Brain(settings).stream,
        synthesize=build_default_synthesizer(),
        speaker=build_default_speaker(),
        watch_barge_in=build_default_barge_in_watcher(),
    )
    typer.echo("Jarvis is listening. Press Ctrl-C to stop.")
    try:
        turns = loop.converse(should_continue=_continue_for(settings.max_turns))
    except KeyboardInterrupt:
        typer.echo("\nGoodbye, sir.")
        return
    for i, turn in enumerate(turns, 1):
        typer.echo(f"[{i}] you: {turn.transcript!r}  jarvis: {turn.reply!r}")
