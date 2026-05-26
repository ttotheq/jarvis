"""Tests for the cold-start deferral primitive (Phase 4 goal G4.2).

The always-on runtime gates readiness on the wake detector alone and warms the
heavier components (Silero VAD, Kokoro, the barge-in watcher) in the background
during the IDLE wait. :class:`jarvis.loop.Lazy` is the seam that makes that
safe: it builds its value once, on first use, under a lock, so a background warm
and a first real use never double-build or race. ``warm_in_background`` kicks
the builds off on a daemon thread. Both are pure (no models, no mic) and tested
here without the voice extra.
"""

from __future__ import annotations

import threading
import time

from jarvis.loop import Lazy, warm_in_background


def test_lazy_builds_once_and_caches() -> None:
    calls = {"n": 0}

    def build() -> str:
        calls["n"] += 1
        return "built"

    lazy = Lazy(build)
    assert calls["n"] == 0  # not built until first use
    assert lazy.get() == "built"
    assert lazy.get() == "built"
    assert calls["n"] == 1  # built exactly once across repeated gets


def test_lazy_returns_the_same_instance_each_time() -> None:
    lazy: Lazy[object] = Lazy(object)
    assert lazy.get() is lazy.get()


def test_lazy_get_is_thread_safe_under_contention() -> None:
    """Concurrent first-uses build exactly once (the background-warm vs first-use race)."""
    calls = {"n": 0}

    def build() -> object:
        # A slow build widens the window for a double-build if the lock is wrong.
        time.sleep(0.05)
        calls["n"] += 1
        return object()

    lazy: Lazy[object] = Lazy(build)
    results: list[object] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()  # release all threads into get() at once
        results.append(lazy.get())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls["n"] == 1  # built once despite 8 concurrent first-uses
    assert len(results) == 8
    assert all(r is results[0] for r in results)  # everyone got the one instance


def test_warm_in_background_builds_every_lazy() -> None:
    built: list[str] = []

    def make(label: str):  # type: ignore[no-untyped-def]
        def build() -> str:
            built.append(label)
            return label

        return build

    lazies = [Lazy(make("a")), Lazy(make("b")), Lazy(make("c"))]
    thread = warm_in_background(*lazies)
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert sorted(built) == ["a", "b", "c"]


def test_warm_in_background_does_not_rebuild_on_later_use() -> None:
    calls = {"n": 0}

    def build() -> str:
        calls["n"] += 1
        return "warmed"

    lazy = Lazy(build)
    warm_in_background(lazy).join(timeout=2.0)
    assert lazy.get() == "warmed"  # served from the warm-up, not rebuilt
    assert calls["n"] == 1


def test_warm_in_background_returns_a_daemon_thread() -> None:
    thread = warm_in_background(Lazy(object))
    assert thread.daemon
    thread.join(timeout=2.0)
