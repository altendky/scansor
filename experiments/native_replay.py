"""Exact-input native reuse bounded to one synchronous operation/evaluation."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import get_ident
from typing import Any, cast


@dataclass
class _ReplayCache:
    owner_thread: int
    values: dict[tuple[str, str], Any] = field(default_factory=dict)
    active: bool = True


_current: ContextVar[_ReplayCache | None] = ContextVar("native_replay", default=None)


@contextmanager
def native_replay_scope() -> Generator[None, None, None]:
    """Nested calls share a pass; failures, later passes and threads do not."""
    existing = _current.get()
    if (
        existing is not None
        and existing.active
        and existing.owner_thread == get_ident()
    ):
        yield
        return
    cache = _ReplayCache(get_ident())
    token = _current.set(cache)
    try:
        yield
    finally:
        cache.active = False
        cache.values.clear()
        _current.reset(token)


def cached_native_replay[T](key: tuple[str, str], construct: Callable[[], T]) -> T:
    """Reuse successful constructions; discard cache references after the pass."""
    cache = _current.get()
    if cache is None or not cache.active or cache.owner_thread != get_ident():
        return construct()
    if key not in cache.values:
        cache.values[key] = construct()
    return cast(T, cache.values[key])
