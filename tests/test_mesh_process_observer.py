from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager

import pytest

from scansor import mesh_process_observer
from scansor.mesh_process_observer import ProcessObserver
from scansor.mesh_rss_protocol import RssObservations, decode_state, read_rss


@contextmanager
def worker(
    code: str = "import time; time.sleep(10)",
) -> Generator[subprocess.Popen[bytes]]:
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        _ = process.wait()
        if process.stdout is not None:
            process.stdout.close()


def assert_reaped(pid: int | None) -> None:
    assert pid is not None
    with pytest.raises(ChildProcessError):
        _ = os.waitpid(pid, os.WNOHANG)


def test_samples_continue_while_parent_holds_gil(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[int] = []
    decode = decode_state

    def record(raw: bytes, previous: RssObservations) -> tuple[bytes, RssObservations]:
        tag, state = decode(raw, previous)
        if state.last_ns is not None:
            received.append(state.last_ns)
        return tag, state

    monkeypatch.setattr(mesh_process_observer, "decode_state", record)
    observer = ProcessObserver(0.001)
    with worker() as process:
        try:
            observer.start(process.pid)
            before = observer.observations()
            started = time.monotonic_ns()
            _ = math.factorial(200_000)
            finished = time.monotonic_ns()
            after = observer.observations()
            assert after.samples > before.samples + 2
            assert any(started < stamp < finished for stamp in received)
        finally:
            observer.stop()
        assert observer.observations().error is None
        assert observer.exit_code == 0
        assert_reaped(observer.pid)


def test_final_peak_survives_full_ipc_queue_without_parent_drainage() -> None:
    code = """
import mmap, time
m = mmap.mmap(-1, 64 * 1024 * 1024)
for offset in range(0, len(m), 4096):
    m[offset] = 1
print('allocated', flush=True)
time.sleep(0.6)
m.close()
print('released', flush=True)
"""
    observer = ProcessObserver(0.001)
    with worker(code) as process:
        try:
            observer.start(process.pid)
            assert process.stdout is not None
            assert process.stdout.readline() == b"allocated\n"
            # No observation drainage while the worker allocates, holds and
            # releases64MiB. This fills the bounded IPC queue on this workload;
            # only the cumulative final record is authoritative at shutdown.
            assert process.stdout.readline() == b"released\n"
            assert process.wait(timeout=5) == 0
        finally:
            observer.stop()
        observed = observer.observations()
        assert observed.peak_bytes >= 64 * 1024 * 1024
        assert observed.samples > 2
        assert observed.error is None and observer.exit_code == 0
        assert_reaped(observer.pid)


def test_held_proc_descriptor_cannot_follow_pid_after_reaping() -> None:
    with worker() as process:
        descriptor = os.open(f"/proc/{process.pid}/statm", os.O_RDONLY)
        try:
            assert read_rss(descriptor, os.sysconf("SC_PAGE_SIZE")) is not None
            process.kill()
            _ = process.wait(timeout=5)
            # The same already-open process handle is dead after reaping. A
            # later process with a reused numeric PID cannot change this FD.
            assert read_rss(descriptor, os.sysconf("SC_PAGE_SIZE")) is None
        finally:
            os.close(descriptor)


def test_unused_helper_is_acknowledged_and_reaped() -> None:
    observer = ProcessObserver(0.001)
    observer.stop()
    observer.stop()
    assert observer.exit_code == 0
    assert observer.observations().samples == 0
    assert_reaped(observer.pid)


def test_invalid_helper_message_fails_observation_and_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = """
import socket, sys
with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET, fileno=int(sys.argv[1])) as s:
    s.send(b'R')
    s.recv(128)
    s.send(b'invalid-statistics')
    s.recv(16)
"""

    def command(descriptor: int) -> list[str]:
        return [sys.executable, "-c", code, str(descriptor)]

    monkeypatch.setattr(mesh_process_observer, "_sampler_command", command)
    observer = ProcessObserver(0.001)
    with worker() as process:
        try:
            observer.start(process.pid)
            deadline = time.monotonic() + 5
            while observer.observations(check=False).error is None:
                assert time.monotonic() < deadline
                time.sleep(0.001)
            with pytest.raises(RuntimeError, match="invalid RSS sampler state"):
                _ = observer.observations()
        finally:
            observer.stop()
        assert_reaped(observer.pid)


def test_helper_death_is_reported_and_reaped() -> None:
    observer = ProcessObserver(0.001)
    with worker() as process:
        try:
            observer.start(process.pid)
            assert observer.pid is not None
            os.kill(observer.pid, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while observer.observations(check=False).error is None:
                assert time.monotonic() < deadline
                time.sleep(0.001)
            with pytest.raises(RuntimeError, match="RSS sampler"):
                _ = observer.observations()
        finally:
            observer.stop()
        assert observer.observations(check=False).error is not None
        assert observer.exit_code == -signal.SIGKILL
        assert_reaped(observer.pid)


def test_helper_exits_when_parent_socket_disappears() -> None:
    observer = ProcessObserver(0.001)
    with worker() as process:
        observer.start(process.pid)
        # Model abrupt owner loss without killing the pytest process.
        observer._channel.close()  # pyright: ignore[reportPrivateUsage]
        helper = observer._helper  # pyright: ignore[reportPrivateUsage]
        assert helper is not None
        try:
            _ = helper.wait(timeout=5)
        finally:
            observer.stop()
        assert_reaped(observer.pid)


def test_startup_failure_reaps_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    children: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def launch(
        args: list[str],
        *,
        pass_fds: tuple[int, ...],
        stdin: int,
        stdout: int,
        stderr: int,
    ) -> subprocess.Popen[bytes]:
        result = popen(
            args, pass_fds=pass_fds, stdin=stdin, stdout=stdout, stderr=stderr
        )
        children.append(result)
        return result

    def command(_fd: int) -> list[str]:
        return [sys.executable, "-c", "raise SystemExit(23)"]

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(mesh_process_observer, "_sampler_command", command)
    with pytest.raises(RuntimeError, match="readiness"):
        _ = ProcessObserver(0.001)
    assert len(children) == 1 and children[0].returncode == 23
    assert_reaped(children[0].pid)
