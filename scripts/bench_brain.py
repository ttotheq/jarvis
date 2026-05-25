"""Phase 0 spike: measure the Claude headless brain's time-to-first-token.

ADR-0003 commits Jarvis to driving Claude Code via `claude -p`. The open
question that gates the rest of the build is latency: when we speak, how long
until the brain starts replying? This throwaway benchmark answers it by
spawning ``claude -p`` N times and timing, per run, the wall-clock interval
from process launch to the first byte of model output on stdout
(time-to-first-token, TTFT).

Usage::

    uv run python scripts/bench_brain.py --runs 10

The live ``claude`` call happens only when this script is executed directly.
The subprocess is fully injected (``runner``), so ``tests/test_bench_brain.py``
exercises the logic with a fake and never touches the network.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

DEFAULT_PROMPT = "Reply with exactly one word: ready"


def _is_model_output(line: str) -> bool:
    """True once a stdout line carries model output rather than session setup.

    `claude -p --output-format stream-json` emits a `{"type":"system",...}` init
    event first; timing to *that* would measure process startup, not the brain
    responding. Any non-empty, non-``system`` line is treated as the first token.
    """
    stripped = line.strip()
    if not stripped:
        return False
    try:
        return json.loads(stripped).get("type") != "system"
    except (json.JSONDecodeError, AttributeError):
        return True


class _Readable(Protocol):
    def readline(self) -> str: ...
    def read(self) -> str: ...


class _Process(Protocol):
    stdout: _Readable | None

    def wait(self) -> int: ...


#: Spawns the brain process. The real one is ``subprocess.Popen``; tests pass a
#: fake with the same call shape.
Runner = Callable[..., _Process]


@dataclass(frozen=True)
class BenchResult:
    """Aggregated TTFT measurements across a benchmark run."""

    runs: int
    samples_s: list[float]

    @property
    def median_s(self) -> float:
        return statistics.median(self.samples_s)

    @property
    def mean_s(self) -> float:
        return statistics.fmean(self.samples_s)

    @property
    def min_s(self) -> float:
        return min(self.samples_s)

    @property
    def max_s(self) -> float:
        return max(self.samples_s)


def _build_command(binary: str, prompt: str) -> list[str]:
    return [
        binary,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]


def measure_ttft(
    prompt: str,
    *,
    binary: str = "claude",
    runner: Runner = subprocess.Popen,
) -> float:
    """Return seconds from launching ``claude -p`` to its first model-output line."""
    cmd = _build_command(binary, prompt)
    start = time.perf_counter()
    proc = runner(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    stream = proc.stdout
    if stream is None:  # pragma: no cover - defensive; PIPE is always set above
        raise RuntimeError("subprocess produced no stdout pipe")
    # Skip the session-init event; time to the first line of actual model output.
    while True:
        line = stream.readline()
        if line == "" or _is_model_output(line):
            break
    elapsed = time.perf_counter() - start
    stream.read()  # drain so the child can exit cleanly
    proc.wait()
    return elapsed


def run_benchmark(
    runs: int,
    *,
    prompt: str = DEFAULT_PROMPT,
    binary: str = "claude",
    runner: Runner = subprocess.Popen,
) -> BenchResult:
    """Measure TTFT ``runs`` times and aggregate the samples."""
    samples = [measure_ttft(prompt, binary=binary, runner=runner) for _ in range(runs)]
    return BenchResult(runs=runs, samples_s=samples)


def _format_summary(result: BenchResult) -> str:
    return (
        f"claude -p time-to-first-token over {result.runs} run(s):\n"
        f"  median {result.median_s * 1000:.0f} ms | "
        f"mean {result.mean_s * 1000:.0f} ms | "
        f"min {result.min_s * 1000:.0f} ms | "
        f"max {result.max_s * 1000:.0f} ms"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = subprocess.Popen,
    write: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description="Benchmark claude -p TTFT.")
    parser.add_argument("--runs", type=int, default=10, help="number of calls")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt to send")
    parser.add_argument("--binary", default="claude", help="claude binary name")
    args = parser.parse_args(argv)

    result = run_benchmark(args.runs, prompt=args.prompt, binary=args.binary, runner=runner)
    write(_format_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by direct execution
    sys.exit(main())
