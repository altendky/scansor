"""Own an independent RSS helper while the parent alone reaps the worker.

The helper is ready before worker launch. Its held proc descriptor cannot refer
to a reused PID, so observations need no lock shared with wait4. Intervals remain
best effort; actual gaps, lifetime edges and errors are retained.
"""

from __future__ import annotations

import array
import math
import os
import socket
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import replace
from typing import final

from scansor.mesh_rss_protocol import (
    BOOTSTRAP,
    EMPTY,
    MAX_MESSAGE,
    STOP_SECONDS,
    RssObservations,
    decode_state,
    read_rss,
)


def _sampler_command(descriptor: int) -> list[str]:
    return [sys.executable, "-m", "scansor.mesh_rss_sampler", str(descriptor)]


@final
class ProcessObserver:
    def __init__(self, sample_seconds: float) -> None:
        if not math.isfinite(sample_seconds) or not 0 < sample_seconds <= 0.01:
            raise ValueError("invalid RSS sampling interval")
        self.sample_seconds = sample_seconds
        self._observed = EMPTY
        self._failure: str | None = None
        self._final = False
        self._closed = False
        self._started = False
        self._helper: subprocess.Popen[bytes] | None = None
        self._channel, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self._helper = subprocess.Popen(
                _sampler_command(child.fileno()),
                pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            child.close()
            self._channel.settimeout(STOP_SECONDS)
            if self._channel.recv(MAX_MESSAGE) != b"R":
                raise RuntimeError("RSS sampler readiness handshake failed")
            self._channel.setblocking(False)
        except BaseException:
            child.close()
            self.stop()
            raise

    @property
    def pid(self) -> int | None:
        return None if self._helper is None else self._helper.pid

    @property
    def exit_code(self) -> int | None:
        return None if self._helper is None else self._helper.returncode

    def _fail(self, error: BaseException | str) -> None:
        if self._failure is None:
            self._failure = f"RSS sampler: {error}"[:MAX_MESSAGE]

    def start(self, pid: int) -> None:
        if self._started or self._closed:
            raise RuntimeError("RSS sampler cannot be started twice or after closing")
        self._started = True
        try:
            descriptor = os.open(f"/proc/{pid}/statm", os.O_RDONLY)
            try:
                now = time.monotonic_ns()
                rss = read_rss(descriptor, os.sysconf("SC_PAGE_SIZE"))
                if rss is None:
                    raise RuntimeError("worker vanished before first RSS observation")
                self._observed = EMPTY.sample(now, rss)
                descriptors = array.array("i", [descriptor])
                _ = self._channel.sendmsg(
                    [
                        BOOTSTRAP.pack(
                            now, rss, round(self.sample_seconds * 1_000_000_000)
                        )
                    ],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors)],
                )
            finally:
                os.close(descriptor)
        except BaseException as error:
            self._fail(error)
            raise

    def _drain(self) -> None:
        while True:
            try:
                raw, ancillary, flags, _ = self._channel.recvmsg(MAX_MESSAGE)
            except BlockingIOError:
                return
            if not raw:
                if not self._final:
                    self._fail("RSS sampler exited without final observations")
                return
            if flags or ancillary or self._final:
                raise RuntimeError("invalid or extra RSS sampler message")
            if raw[:1] == b"E":
                self._fail(raw[1:].decode("utf-8", errors="replace"))
                continue
            tag, self._observed = decode_state(raw, self._observed)
            self._final = tag == b"F"
            if self._final:
                _ = self._channel.send(b"A")

    def observations(self, *, check: bool = True) -> RssObservations:
        if not self._closed:
            try:
                self._drain()
                if self._helper is not None and self._helper.poll() is not None:
                    self._drain()
                    if self._helper.returncode or not self._final:
                        self._fail("RSS sampler exited abnormally")
            except BaseException as error:
                self._fail(error)
        if check and self._failure is not None:
            raise RuntimeError(self._failure)
        return replace(self._observed, error=self._failure)

    def stop(self) -> None:
        """Bounded shutdown; failures stay in observations instead of escaping."""
        if self._closed:
            return
        helper = self._helper
        try:
            self._channel.setblocking(False)
            if not self._final:
                with suppress(BlockingIOError, BrokenPipeError, ConnectionResetError):
                    _ = self._channel.send(b"Q")
            deadline = time.monotonic() + STOP_SECONDS
            while helper is not None and helper.poll() is None:
                _ = self.observations(check=False)
                if time.monotonic() >= deadline:
                    self._fail("RSS sampler did not stop within its shutdown deadline")
                    break
                time.sleep(0.001)
        except BaseException as error:
            self._fail(error)
        finally:
            if helper is not None:
                if helper.poll() is None:
                    helper.kill()
                _ = helper.wait()
                _ = self.observations(check=False)
                if helper.returncode:
                    self._fail(f"RSS sampler exited with status {helper.returncode}")
                if helper.stderr is not None:
                    stderr = helper.stderr.read(MAX_MESSAGE)
                    helper.stderr.close()
                    if stderr:
                        self._fail(stderr.decode("utf-8", errors="replace"))
            self._channel.close()
            self._closed = True
