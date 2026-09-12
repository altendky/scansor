"""Private stdlib-only RSS helper, started before the measured worker exists.

The held proc descriptor stays tied to the original process after reaping:
https://docs.kernel.org/filesystems/proc.html. Cumulative updates are nonblocking
so a busy parent cannot stall sampling. SOCK_SEQPACKET reports owner closure.
"""

from __future__ import annotations

import array
import os
import socket
import sys
import time
from contextlib import suppress

from scansor.mesh_rss_protocol import (
    BOOTSTRAP,
    EMPTY,
    MAX_MESSAGE,
    STOP_SECONDS,
    RssObservations,
    read_rss,
)


def sample(channel: socket.socket) -> int:
    descriptor: int | None = None
    observed = EMPTY
    failure: str | None = None
    try:
        channel.settimeout(STOP_SECONDS)
        _ = channel.send(b"R")
        payload, ancillary, flags, _ = channel.recvmsg(
            BOOTSTRAP.size, socket.CMSG_SPACE(array.array("i").itemsize)
        )
        received = array.array("i")
        for level, kind, raw in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                received.frombytes(raw)
        if received:
            descriptor = received[0]
            for extra in received[1:]:
                os.close(extra)
        if payload in (b"Q", b"") and not ancillary and not flags:
            return 0
        if flags or len(payload) != BOOTSTRAP.size or len(received) != 1:
            raise RuntimeError("invalid RSS sampler bootstrap")
        first, peak, interval = BOOTSTRAP.unpack(payload)
        if first == 0 or not 0 < interval <= 10_000_000:
            raise RuntimeError("invalid RSS sampler interval or timestamp")
        observed = RssObservations(peak, 1, first, first, 0, 0, None)
        page_bytes = os.sysconf("SC_PAGE_SIZE")
        channel.setblocking(False)
        assert descriptor is not None
        while True:
            try:
                command = channel.recv(16)
            except BlockingIOError:
                pass
            else:
                if command in (b"Q", b""):
                    break
                raise RuntimeError("invalid RSS sampler command")
            now = time.monotonic_ns()
            rss = read_rss(descriptor, page_bytes)
            if rss is None:
                break
            observed = observed.sample(now, rss)
            with suppress(BlockingIOError):
                _ = channel.send(observed.packet(b"P"))
            time.sleep(interval / 1_000_000_000)
    except BaseException as error:
        failure = f"{type(error).__name__}: {error}"[: MAX_MESSAGE - 1]
    finally:
        if descriptor is not None:
            os.close(descriptor)
        channel.settimeout(STOP_SECONDS)
        with suppress(BrokenPipeError, ConnectionResetError):
            if failure is not None:
                _ = channel.send(b"E" + failure.encode("utf-8")[: MAX_MESSAGE - 1])
            _ = channel.send(observed.packet(b"F"))
            # The owner must acknowledge the final cumulative record before
            # this helper reports successful exit. A queued stop may precede it.
            deadline = time.monotonic() + STOP_SECONDS
            while True:
                channel.settimeout(max(0.001, deadline - time.monotonic()))
                reply = channel.recv(16)
                if reply in (b"A", b""):
                    break
                if reply != b"Q" or time.monotonic() >= deadline:
                    raise RuntimeError("RSS sampler final acknowledgement failed")
    return int(failure is not None)


def main() -> int:
    with socket.socket(
        socket.AF_UNIX, socket.SOCK_SEQPACKET, fileno=int(sys.argv[1])
    ) as channel:
        return sample(channel)


if __name__ == "__main__":
    raise SystemExit(main())
