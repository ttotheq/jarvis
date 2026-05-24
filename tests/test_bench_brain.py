"""Tests for the Phase 0 Claude-brain latency benchmark.

The real script spawns `claude -p` and measures time-to-first-token. Here the
subprocess is replaced by an injected fake runner, so the suite never makes a
live network call (the spike's only live call happens when the script is run
directly). Written before ``scripts/bench_brain.py`` exists, per ADR-0005.
"""

from __future__ import annotations

from collections.abc import Sequence

import bench_brain


def _runner_factory(captured: list[Sequence[str]], lines: list[str]):  # type: ignore[no-untyped-def]
    def runner(cmd: Sequence[str], **_kwargs: object) -> object:
        captured.append(cmd)

        class _Proc:
            def __init__(self) -> None:
                self.stdout = _LineReader(lines)

            def wait(self) -> int:
                return 0

        return _Proc()

    return runner


class _LineReader:
    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def readline(self) -> str:
        return self._lines.pop(0) if self._lines else ""

    def read(self) -> str:
        rest = "".join(self._lines)
        self._lines.clear()
        return rest


def test_measure_ttft_returns_positive_and_builds_claude_command() -> None:
    captured: list[Sequence[str]] = []
    runner = _runner_factory(captured, ['{"type":"stream_event"}\n', "more\n"])
    elapsed = bench_brain.measure_ttft("ping", binary="claude", runner=runner)
    assert elapsed >= 0.0
    cmd = captured[0]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "ping" in cmd


def test_run_benchmark_collects_one_sample_per_run() -> None:
    captured: list[Sequence[str]] = []
    runner = _runner_factory(captured, ["first\n"])
    result = bench_brain.run_benchmark(runs=3, prompt="ping", runner=runner)
    assert result.runs == 3
    assert len(result.samples_s) == 3
    assert result.median_s >= 0.0
    assert len(captured) == 3


def test_measure_ttft_skips_the_session_init_event() -> None:
    captured: list[Sequence[str]] = []
    lines = ['{"type":"system","subtype":"init"}\n', '{"type":"stream_event"}\n']
    runner = _runner_factory(captured, lines)
    # Should not raise and should return a timing; the system line is skipped.
    assert bench_brain.measure_ttft("ping", runner=runner) >= 0.0


def test_is_model_output_classifies_lines() -> None:
    assert bench_brain._is_model_output('{"type":"system","subtype":"init"}') is False
    assert bench_brain._is_model_output('{"type":"stream_event"}') is True
    assert bench_brain._is_model_output("   ") is False
    assert bench_brain._is_model_output("not json at all") is True
    assert bench_brain._is_model_output("[1, 2, 3]") is True


def test_main_runs_without_live_subprocess() -> None:
    captured: list[Sequence[str]] = []
    runner = _runner_factory(captured, ["first\n"])
    printed: list[str] = []
    code = bench_brain.main(
        ["--runs", "2", "--prompt", "ping"],
        runner=runner,
        write=printed.append,
    )
    assert code == 0
    assert len(captured) == 2
    assert any("median" in line.lower() for line in printed)
