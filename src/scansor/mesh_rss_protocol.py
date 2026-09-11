"""Bounded private Linux sampler messages and cumulative RSS observations."""

from __future__ import annotations

import errno
import os
import struct
from dataclasses import dataclass

BOOTSTRAP = struct.Struct("!3Q")
STATE = struct.Struct("!c6Q")
MAX_MESSAGE = 4096
STOP_SECONDS = 5.0


@dataclass(frozen=True)
class RssObservations:
    peak_bytes: int
    samples: int
    first_ns: int | None
    last_ns: int | None
    largest_gap_ns: int
    gaps_over_10ms: int
    error: str | None

    def packet(self, tag: bytes) -> bytes:
        return STATE.pack(
            tag,
            self.peak_bytes,
            self.samples,
            self.first_ns or 0,
            self.last_ns or 0,
            self.largest_gap_ns,
            self.gaps_over_10ms,
        )

    def sample(self, now: int, rss: int) -> RssObservations:
        gap = 0 if self.last_ns is None else now - self.last_ns
        return RssObservations(
            max(self.peak_bytes, rss),
            self.samples + 1,
            now if self.first_ns is None else self.first_ns,
            now,
            max(self.largest_gap_ns, gap),
            self.gaps_over_10ms + int(gap > 10_000_000),
            self.error,
        )


EMPTY = RssObservations(0, 0, None, None, 0, 0, None)


def read_rss(descriptor: int, page_bytes: int) -> int | None:
    """Read the held process descriptor; never reopen a possibly reused PID."""
    try:
        raw = os.pread(descriptor, 256, 0)
    except OSError as error:
        if error.errno == errno.ESRCH:
            return None
        raise
    if not raw:
        return None
    fields = raw.split()
    if len(raw) >= 256 or len(fields) != 7:
        raise RuntimeError("invalid worker statm record")
    pages = int(fields[1])
    if pages < 0:
        raise RuntimeError("negative worker RSS")
    return pages * page_bytes


def decode_state(
    raw: bytes, previous: RssObservations
) -> tuple[bytes, RssObservations]:
    if len(raw) != STATE.size or raw[:1] not in (b"P", b"F"):
        raise RuntimeError("invalid RSS sampler state message")
    tag, peak, samples, first, last, gap, misses = STATE.unpack(raw)
    current = RssObservations(
        peak, samples, first or None, last or None, gap, misses, None
    )
    if (
        peak < previous.peak_bytes
        or samples < previous.samples
        or (previous.first_ns is not None and first != previous.first_ns)
        or last < (previous.last_ns or 0)
        or gap < previous.largest_gap_ns
        or misses < previous.gaps_over_10ms
        or (samples == 0 and any((peak, first, last, gap, misses)))
        or (samples > 0 and (first == 0 or last < first or misses >= samples))
    ):
        raise RuntimeError("invalid or decreasing RSS sampler observations")
    return tag, current
