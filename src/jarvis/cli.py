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


@app.command()
def run() -> None:  # pragma: no cover - end-to-end hardware path, checked manually
    """Hold a push-to-talk spoken conversation with Claude Code.

    Push a key, speak, hear the reply; repeat until Ctrl-C. The local voice stack
    must be installed — run ``jarvis doctor`` first. The loop's logic is covered
    by tests/test_loop.py; this wiring is the manual end-to-end check (G1.1).
    """
    from jarvis.brain import Brain
    from jarvis.loop import VoiceLoop
    from jarvis.stt import WhisperCppTranscriber
    from jarvis.tts import build_default_speaker, build_default_synthesizer

    settings = get_settings()
    loop = VoiceLoop(
        record_turn=_push_to_talk_record_turn(settings.sample_rate),
        transcribe=WhisperCppTranscriber(settings),
        brain=Brain(settings),
        synthesize=build_default_synthesizer(),
        speaker=build_default_speaker(),
    )
    typer.echo("Jarvis is listening. Press Ctrl-C to stop.")
    try:
        loop.converse(should_continue=lambda _done: True)
    except KeyboardInterrupt:
        typer.echo("\nGoodbye, sir.")
