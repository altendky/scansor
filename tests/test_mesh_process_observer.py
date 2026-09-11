from __future__ import annotations

import os
import resource
import threading
from types import SimpleNamespace
from typing import cast

import psutil
import pytest

from scansor.mesh_process_observer import ProcessObserver


def test_reaping_waits_for_inflight_observation_and_prevents_pid_reuse_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampling, release, reaped = threading.Event(), threading.Event(), threading.Event()
    reaper_entered = threading.Event()
    parent = threading.get_ident()
    calls: list[int] = []
    results: list[int] = []
    errors: list[BaseException] = []

    def memory_info() -> SimpleNamespace:
        if threading.get_ident() != parent:
            sampling.set()
            assert release.wait(5)
        assert not reaped.is_set(), "PID was read after it became reusable"
        calls.append(threading.get_ident())
        return SimpleNamespace(rss=1234)

    def wait4(pid: int, _options: int) -> tuple[int, int, resource.struct_rusage]:
        assert pid == 123
        reaped.set()
        return pid, 0, resource.getrusage(resource.RUSAGE_SELF)

    monkeypatch.setattr(os, "wait4", wait4)
    # A synthetic process lets the test model PID reuse without touching an
    # unrelated host process. Only pid and memory_info belong to this seam.
    process: object = SimpleNamespace(pid=123, memory_info=memory_info)
    observer = ProcessObserver(cast(psutil.Process, cast(object, process)), 0.001)

    def reap() -> None:
        reaper_entered.set()
        try:
            results.append(observer.reap(os.WNOHANG)[0])
        except BaseException as error:
            errors.append(error)

    reaper = threading.Thread(target=reap)
    observer.start()
    try:
        assert sampling.wait(5)
        reaper.start()
        assert reaper_entered.wait(5)
        assert not reaped.wait(0.02)
        release.set()
        reaper.join(5)
        assert not reaper.is_alive()
        assert results == [123] and errors == []
    finally:
        release.set()
        observer.stop()
        if reaper.ident is not None:
            reaper.join(5)
    observed = observer.observations()
    assert observed.error is None
    assert observed.samples == len(calls) >= 2
    assert observed.peak_bytes == 1234
