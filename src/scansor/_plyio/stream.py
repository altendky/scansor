from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from .format import ElementLayout, Layout, PlyError, Sink, Source


def _limits(max_range_bytes: int, io_block_bytes: int) -> None:
    if type(max_range_bytes) is not int or max_range_bytes < 1:
        raise PlyError("range byte limit must be positive", category="resource")
    if type(io_block_bytes) is not int or io_block_bytes < 1:
        raise PlyError("I/O block limit must be positive", category="resource")


def _range(layout: ElementLayout, start: int, stop: int, maximum: int) -> int:
    if (
        type(start) is not int
        or type(stop) is not int
        or not 0 <= start <= stop <= layout.element.count
    ):
        raise PlyError("invalid row range", element=layout.element.name)
    size = (stop - start) * layout.dtype.itemsize
    if size > maximum:
        raise PlyError(
            "row range exceeds byte limit",
            category="resource",
            element=layout.element.name,
            row=start,
        )
    return size


def _check_lists(layout: ElementLayout, rows: np.ndarray, start: int) -> None:
    for name, count in layout.list_counts:
        wrong = rows[name]["count"] != count
        if np.any(wrong):
            first = int(np.argmax(wrong))
            row = start + first
            assert layout.dtype.fields is not None
            property_offset = next(
                field[1]
                for field_name, field in layout.dtype.fields.items()
                if field_name == name
            )
            raise PlyError(
                "unsupported list count",
                category="unsupported",
                element=layout.element.name,
                row=row,
                property_name=name,
                offset=layout.offset + row * layout.dtype.itemsize + property_offset,
            )
        del wrong


def _error_at(
    layout: Layout, offset: int, message: str, *, category: str = "io"
) -> PlyError:
    for block in layout.elements:
        relative = offset - block.offset
        if 0 <= relative < block.element.count * block.dtype.itemsize:
            row, within_row = divmod(relative, block.dtype.itemsize)
            assert block.dtype.fields is not None
            name = next(
                (
                    name
                    for name, field in block.dtype.fields.items()
                    if field[1] <= within_row < field[1] + field[0].itemsize
                ),
                None,
            )
            return PlyError(
                message,
                category=category,
                offset=offset,
                element=block.element.name,
                row=row,
                property_name=name,
            )
    return PlyError(message, category=category, offset=offset)


class Reader:
    """Caller-owned seekable stream; each range returns owned, read-only rows.

    A read allocates at most max_range_bytes for its result, io_block_bytes for
    transient read bytes, and one byte per row while checking a list property.
    The caller budgets these, header/layout metadata, and retained results. Calls
    on the shared stream must be serialized. No mapping or hidden cache is kept.
    Construction validates file size; validate_all is required to attest every
    list count rather than only the ranges the caller happened to request.
    """

    def __init__(
        self,
        stream: Source,
        layout: Layout,
        *,
        max_range_bytes: int,
        io_block_bytes: int = 65_536,
    ) -> None:
        _limits(max_range_bytes, io_block_bytes)
        self.stream: Source = stream
        self.layout: Layout = layout
        self.max_range_bytes: int = max_range_bytes
        self.io_block_bytes: int = io_block_bytes
        _ = stream.seek(0, 2)
        actual = stream.tell()
        if actual != layout.byte_count:
            detail = (
                "trailing bytes" if actual > layout.byte_count else "truncated payload"
            )
            raise _error_at(
                layout, min(actual, layout.byte_count), detail, category="structure"
            )

    def read_range(self, element: str, start: int, stop: int) -> np.ndarray:
        layout = self.layout.element(element)
        size = _range(layout, start, stop, self.max_range_bytes)
        rows = np.empty(stop - start, dtype=layout.dtype)
        target = memoryview(rows).cast("B")
        offset = layout.offset + start * layout.dtype.itemsize
        _ = self.stream.seek(offset)
        done = 0
        while done < size:
            requested = min(self.io_block_bytes, size - done)
            try:
                chunk = self.stream.read(requested)
            except OSError as error:
                raise _error_at(
                    self.layout, offset + done, "payload read failed"
                ) from error
            if not chunk or len(chunk) > requested:
                raise _error_at(
                    self.layout,
                    offset + done,
                    "truncated payload or invalid stream read",
                )
            target[done : done + len(chunk)] = chunk
            done += len(chunk)
        _check_lists(layout, rows, start)
        rows.flags.writeable = False
        return rows

    def iter_ranges(
        self, element: str, *, chunk_rows: int
    ) -> Iterator[tuple[int, np.ndarray]]:
        if type(chunk_rows) is not int or chunk_rows < 1:
            raise PlyError("chunk row count must be positive", category="resource")
        count = self.layout.element(element).element.count
        for start in range(0, count, chunk_rows):
            yield start, self.read_range(element, start, min(start + chunk_rows, count))

    def validate_all(self, *, chunk_rows: int) -> None:
        for layout in self.layout.elements:
            for _start, _rows in self.iter_ranges(
                layout.element.name, chunk_rows=chunk_rows
            ):
                del _rows


class Writer:
    """Sequential exact-layout writer; caller owns stream flush/close/publication.

    Input must be C-contiguous records with the exact declared dtype. No hidden
    dtype conversion or whole-range byte copy is performed. finish() verifies
    complete coverage; a partial or failed writer cannot certify completion.
    """

    def __init__(
        self,
        stream: Sink,
        layout: Layout,
        *,
        max_range_bytes: int,
        io_block_bytes: int = 65_536,
    ) -> None:
        _limits(max_range_bytes, io_block_bytes)
        self.stream: Sink = stream
        self.layout: Layout = layout
        self.max_range_bytes: int = max_range_bytes
        self.io_block_bytes: int = io_block_bytes
        self._element_index: int = 0
        self._row: int = 0
        self._failed: bool = False
        self._finished: bool = False
        _ = stream.seek(0, 2)
        if stream.tell() != 0:
            raise PlyError("writer requires an empty stream", category="io")
        self._write(memoryview(layout.header.raw))

    def _write(self, data: memoryview) -> None:
        done = 0
        offset = self.stream.tell()
        try:
            while done < len(data):
                part = data[done : done + self.io_block_bytes]
                count = self.stream.write(part)
                if count is None or not 0 < count <= len(part):
                    raise _error_at(
                        self.layout, offset + done, "short write made no progress"
                    )
                done += count
        except OSError as error:
            self._failed = True
            raise _error_at(self.layout, offset + done, "write failed") from error
        except BaseException:
            self._failed = True
            raise

    def _advance(self) -> None:
        while self._element_index < len(self.layout.elements):
            if self._row != self.layout.elements[self._element_index].element.count:
                break
            self._element_index += 1
            self._row = 0

    def write_range(self, element: str, start: int, rows: np.ndarray) -> None:
        if self._failed or self._finished:
            raise PlyError("writer is failed or finished", category="io")
        self._advance()
        if self._element_index >= len(self.layout.elements):
            raise PlyError("all declared rows already written")
        layout = self.layout.elements[self._element_index]
        if element != layout.element.name or start != self._row:
            raise PlyError(
                "write must follow declared element/row order",
                element=element,
                row=start,
            )
        if rows.ndim != 1 or rows.dtype != layout.dtype or not rows.flags.c_contiguous:
            raise PlyError(
                "write requires contiguous records with exact dtype", element=element
            )
        _ = _range(layout, start, start + len(rows), self.max_range_bytes)
        _check_lists(layout, rows, start)
        expected = layout.offset + start * layout.dtype.itemsize
        if self.stream.tell() != expected:
            self._failed = True
            raise PlyError(
                "stream position changed outside writer", category="io", offset=expected
            )
        self._write(memoryview(rows).cast("B"))
        self._row += len(rows)

    def finish(self) -> None:
        self._advance()
        if self._failed or self._element_index != len(self.layout.elements):
            raise PlyError("writer has incomplete or failed payload", category="io")
        if self.stream.tell() != self.layout.byte_count:
            raise PlyError("writer byte count mismatch", category="io")
        _ = self.stream.seek(0, 2)
        if self.stream.tell() != self.layout.byte_count:
            self._failed = True
            raise PlyError("writer has trailing bytes", category="io")
        self._finished = True
