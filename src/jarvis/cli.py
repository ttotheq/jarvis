"""Command-line entry point for Jarvis.

Commands available today are intentionally minimal; the conversational
runtime (``jarvis run``) and environment self-check (``jarvis doctor``) are
introduced in Phase 0/1 per ``docs/phases/``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import typer

from jarvis import __version__, service
from jarvis.audio import Clip
from jarvis.config import get_settings
from jarvis.doctor import run_doctor

app = typer.Typer(
    name="jarvis",
    help="A local-first, voice-controlled interface for Claude Code.",
    no_args_is_help=True,
    add_completion=False,
)

service_app = typer.Typer(
    name="service",
    help="Manage the Jarvis always-on background service (macOS launchd).",
    no_args_is_help=True,
)
app.add_typer(service_app)


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


@service_app.command("install")
def service_install() -> None:
    """Install and load the launchd LaunchAgent (auto-starts at login)."""
    settings = get_settings()
    path = service.install(settings, runner=service.default_runner)
    typer.echo(f"Installed {settings.service_label} -> {path}")
    typer.echo("Auto-starts at login and restarts on crash. `jarvis service status` to check.")


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Unload the LaunchAgent and remove its plist."""
    settings = get_settings()
    removed = service.uninstall(settings, runner=service.default_runner)
    if removed:
        typer.echo(f"Uninstalled {settings.service_label}.")
    else:
        typer.echo(f"{settings.service_label} was not installed; nothing to remove.")


@service_app.command("status")
def service_status() -> None:
    """Report whether the service is installed and loaded (exit 0 if loaded)."""
    settings = get_settings()
    st = service.status(settings, runner=service.default_runner)
    typer.echo(service.format_status(st))
    raise typer.Exit(code=0 if st.loaded else 1)


def _continue_for(max_turns: int | None) -> Callable[[int], bool]:
    """Build the loop predicate: run forever (``None``) or stop after N turns."""
    if max_turns is None:
        return lambda _done: True
    return lambda done: done < max_turns


def _push_to_talk_record_turn(  # pragma: no cover - requires a real microphone
    sample_rate: int,
    source: Callable[[], bytes],
    *,
    reset_source: Callable[[], None] | None = None,
) -> Callable[[], Clip]:
    """Build a record-one-turn callable: Enter to start, Enter again to stop."""
    import threading

    from jarvis.audio import record

    def _record() -> Clip:
        input("Press Enter, speak, then press Enter again to send… ")
        stop = threading.Event()

        def _watch_for_stop() -> None:
            input()
            stop.set()

        threading.Thread(target=_watch_for_stop, daemon=True).start()
        if reset_source is not None:
            reset_source()
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
    source: Callable[[], bytes],
    *,
    reset_source: Callable[[], None] | None = None,
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

    from jarvis.audio import record

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
        if reset_source is not None:
            reset_source()
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
    from jarvis.audio import SoundDeviceMicrophone
    from jarvis.brain import Brain
    from jarvis.loop import VoiceLoop, build_default_barge_in_watcher
    from jarvis.stt import WhisperCppTranscriber
    from jarvis.tts import build_default_speaker, build_default_synthesizer
    from jarvis.wakeword import FRAME_SAMPLES, SAMPLE_RATE

    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    block_frames = max(1, round(settings.sample_rate * FRAME_SAMPLES / SAMPLE_RATE))
    microphone = SoundDeviceMicrophone(
        settings.sample_rate,
        block_frames=block_frames,
        device=settings.input_device,
    )
    try:
        if settings.ptt_seconds is not None:
            record_turn = _timed_record_turn(
                settings.sample_rate,
                settings.ptt_seconds,
                microphone.read,
                reset_source=microphone.flush,
                prompts=_GUIDED_PROMPTS,
            )
        else:
            record_turn = _push_to_talk_record_turn(
                settings.sample_rate,
                microphone.read,
                reset_source=microphone.flush,
            )
        loop = VoiceLoop(
            record_turn=record_turn,
            transcribe=WhisperCppTranscriber(settings),
            stream=Brain(settings).stream,
            synthesize=build_default_synthesizer(),
            speaker=build_default_speaker(),
            watch_barge_in=build_default_barge_in_watcher(
                microphone.read,
                source_sample_rate=settings.sample_rate,
                reset_source=microphone.flush,
            ),
        )
        typer.echo("Jarvis is listening. Press Ctrl-C to stop.")
        try:
            turns = loop.converse(should_continue=_continue_for(settings.max_turns))
        except KeyboardInterrupt:
            typer.echo("\nGoodbye, sir.")
            return
    finally:
        microphone.close()
    for i, turn in enumerate(turns, 1):
        typer.echo(f"[{i}] you: {turn.transcript!r}  jarvis: {turn.reply!r}")
