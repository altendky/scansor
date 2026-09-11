"""RSS observations independent of worker protocol handling, with serialized reaping.

The observer and the sole wait4 reaper share a lock. Once the child is reaped,
no further PID-based observation can read a subsequently reused process ID.
Requested intervals remain best effort; actual gaps and lifetime edges are kept.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

import psutil

if TYPE_CHECKING:
    import resource


@dataclass(frozen=True)
class RssObservations:
    peak_bytes: int
    samples: int
    first_ns: int | None
    last_ns: int | None
    largest_gap_ns: int
    gaps_over_10ms: int
    error: str | None


@final
class ProcessObserver:
    def __init__(self, process: psutil.Process, sample_seconds: float) -> None:
        self.process = process
        self.sample_seconds = sample_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None
        self._peak = 0
        self._samples = 0
        self._first: int | None = None
        self._last: int | None = None
        self._gap = 0
        self._misses = 0

    def _sample(self) -> None:
        now = time.monotonic_ns()
        try:
            rss = self.process.memory_info().rss
        except psutil.NoSuchProcess:
            return
        self._peak = max(self._peak, rss)
        self._samples += 1
        if self._first is None:
            self._first = now
        if self._last is not None:
            gap = now - self._last
            self._gap = max(self._gap, gap)
            self._misses += int(gap > 10_000_000)
        self._last = now

    def start(self) -> None:
        # Take an immediate observation before startup metadata is flushed.
        with self._lock:
            self._sample()
        thread = threading.Thread(target=self._run, name="scansor-worker-rss")
        self._thread = thread
        thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.sample_seconds):
            with self._lock:
                if self._stop.is_set():
                    return
                try:
                    self._sample()
                except BaseException as error:
                    self._failure = error
                    self._stop.set()
                    return

    def observations(self, *, check: bool = True) -> RssObservations:
        with self._lock:
            if check and self._failure is not None:
                raise self._failure
            return RssObservations(
                self._peak,
                self._samples,
                self._first,
                self._last,
                self._gap,
                self._misses,
                None
                if self._failure is None
                else f"{type(self._failure).__name__}: {self._failure}"[:4096],
            )

    def reap(self, options: int) -> tuple[int, int, resource.struct_rusage]:
        with self._lock:
            result = os.wait4(self.process.pid, options)
            if result[0]:
                self._stop.set()
            return result

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join()
