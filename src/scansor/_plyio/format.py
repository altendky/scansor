from __future__ import annotations

import io
import re
from collections.abc import Buffer, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np

MAX_OFFSET = 2**63 - 1
MAX_HEADER_BYTES = 65_536
MAX_LINE_BYTES = 4_096
SCALARS = {
    "char": "i1",
    "uchar": "u1",
    "short": "<i2",
    "ushort": "<u2",
    "int": "<i4",
    "uint": "<u4",
    "float": "<f4",
    "double": "<f8",
}
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
_COUNT = re.compile(r"(?:0|[1-9][0-9]*)\Z")


class Source(Protocol):
    def read(self, size: int, /) -> bytes: ...
    def seek(self, offset: int, whence: int = 0, /) -> int: ...
    def tell(self) -> int: ...


class Sink(Protocol):
    def write(self, data: Buffer, /) -> int | None: ...
    def seek(self, offset: int, whence: int = 0, /) -> int: ...
    def tell(self) -> int: ...


class PlyError(ValueError):
    """Plain format/resource/I/O error with source context, when available."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "structure",
        offset: int | None = None,
        element: str | None = None,
        row: int | None = None,
        property_name: str | None = None,
    ) -> None:
        self.category: str = category
        self.offset: int | None = offset
        self.element: str | None = element
        self.row: int | None = row
        self.property_name: str | None = property_name
        context = ", ".join(
            f"{key}={value}"
            for key, value in (
                ("offset", offset),
                ("element", element),
                ("row", row),
                ("property", property_name),
            )
            if value is not None
        )
        super().__init__(f"{message} ({context})" if context else message)


def seek(stream: Source | Sink, offset: int, whence: int = 0) -> None:
    try:
        position = stream.seek(offset, whence)
    except OSError as error:
        raise PlyError(
            "stream seek failed", category="io", offset=offset if whence == 0 else None
        ) from error
    if whence == 0 and position != offset:
        raise PlyError(
            "stream seek returned a different offset", category="io", offset=offset
        )


def tell(stream: Source | Sink) -> int:
    try:
        return stream.tell()
    except OSError as error:
        raise PlyError("stream position query failed", category="io") from error


@dataclass(frozen=True)
class ScalarProperty:
    name: str
    scalar_type: str


@dataclass(frozen=True)
class ListProperty:
    name: str
    count_type: str
    item_type: str


@dataclass(frozen=True)
class Element:
    name: str
    count: int
    properties: tuple[ScalarProperty | ListProperty, ...]


@dataclass(frozen=True)
class Header:
    elements: tuple[Element, ...]
    raw: bytes
    comments: tuple[bytes, ...]
    newline: bytes


@dataclass(frozen=True)
class ElementLayout:
    element: Element
    dtype: np.dtype
    offset: int
    list_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Layout:
    header: Header
    elements: tuple[ElementLayout, ...]
    byte_count: int

    def element(self, name: str) -> ElementLayout:
        for layout in self.elements:
            if layout.element.name == name:
                return layout
        raise PlyError("unknown element", element=name)


def _name(value: str) -> str:
    if len(value) > MAX_LINE_BYTES or not _NAME.fullmatch(value):
        raise PlyError(f"unsupported name {value!r}", category="unsupported")
    return value


def _scalar(value: str, *, count: bool = False) -> str:
    if value not in SCALARS or (count and value in {"float", "double"}):
        raise PlyError(f"unsupported scalar type {value!r}", category="unsupported")
    return value


def _count(value: str) -> int:
    if len(value) > 19 or not _COUNT.fullmatch(value):
        raise PlyError("count must be a canonical nonnegative decimal integer")
    result = int(value)
    if result > MAX_OFFSET:
        raise PlyError("count exceeds signed 64-bit offset range")
    return result


def read_header(
    stream: Source,
    *,
    max_header_bytes: int = MAX_HEADER_BYTES,
    max_line_bytes: int = MAX_LINE_BYTES,
) -> Header:
    """Read from file offset zero, without reading any payload or unbounded line.

    The caller owns the seekable binary stream and its lifetime. Header storage is
    bounded by max_header_bytes; individual read requests are exactly one byte.
    """
    if max_header_bytes < 1 or max_line_bytes < 1:
        raise PlyError("header limits must be positive", category="resource")
    seek(stream, 0)
    raw = bytearray()
    lines: list[bytes] = []
    newline: bytes | None = None
    while True:
        line = bytearray()
        while not line.endswith(b"\n"):
            if len(raw) >= max_header_bytes or len(line) >= max_line_bytes:
                raise PlyError("header or line limit exceeded", offset=len(raw))
            try:
                chunk = stream.read(1)
            except OSError as error:
                raise PlyError(
                    "header read failed", category="io", offset=len(raw)
                ) from error
            if not chunk:
                raise PlyError("truncated header", offset=len(raw))
            if len(chunk) != 1:
                raise PlyError("stream exceeded requested read", category="io")
            raw.extend(chunk)
            line.extend(chunk)
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n"
        if newline is None:
            newline = ending
        if ending != newline:
            raise PlyError("mixed header line endings", offset=len(raw) - len(line))
        content = bytes(line[: -len(ending)])
        if not content or any(value < 32 or value > 126 for value in content):
            raise PlyError(
                "header requires nonblank printable ASCII lines",
                offset=len(raw) - len(line),
            )
        lines.append(content)
        if content == b"end_header":
            break
    if lines[:2] != [b"ply", b"format binary_little_endian 1.0"]:
        raise PlyError("unsupported PLY magic or format", category="unsupported")
    elements: list[Element] = []
    comments: list[bytes] = []
    for line in lines[2:-1]:
        if line == b"comment" or line.startswith(b"comment "):
            comments.append(line + newline)
            continue
        parts = line.decode("ascii").split(" ")
        if len(parts) == 3 and parts[0] == "element":
            name = _name(parts[1])
            if any(element.name == name for element in elements):
                raise PlyError("duplicate element", element=name)
            elements.append(Element(name, _count(parts[2]), ()))
            continue
        prop: ScalarProperty | ListProperty
        if len(parts) == 3 and parts[0] == "property":
            prop = ScalarProperty(_name(parts[2]), _scalar(parts[1]))
        elif len(parts) == 5 and parts[:2] == ["property", "list"]:
            prop = ListProperty(
                _name(parts[4]), _scalar(parts[2], count=True), _scalar(parts[3])
            )
        else:
            raise PlyError(
                f"unsupported header declaration {line!r}", category="unsupported"
            )
        if not elements:
            raise PlyError("property precedes an element", property_name=prop.name)
        element = elements[-1]
        if any(existing.name == prop.name for existing in element.properties):
            raise PlyError(
                "duplicate property", element=element.name, property_name=prop.name
            )
        elements[-1] = replace(element, properties=(*element.properties, prop))
    if not elements or any(not element.properties for element in elements):
        raise PlyError("each header requires elements with properties")
    return Header(tuple(elements), bytes(raw), tuple(comments), newline)


def make_header(
    elements: Sequence[Element],
    *,
    comments: Sequence[str] = (),
    newline: bytes = b"\n",
) -> Header:
    """Build the same validated metadata used by the reader; no application fields."""
    if newline not in {b"\n", b"\r\n"}:
        raise PlyError("unsupported header newline")
    raw = bytearray()

    def append(line: str) -> None:
        size = len(line) + len(newline)
        if size > MAX_LINE_BYTES or len(raw) + size > MAX_HEADER_BYTES:
            raise PlyError("header or line limit exceeded")
        raw.extend(line.encode("ascii"))
        raw.extend(newline)

    append("ply")
    append("format binary_little_endian 1.0")
    for comment in comments:
        if len(comment) + 8 + len(newline) > MAX_LINE_BYTES:
            raise PlyError("comment line limit exceeded")
        if any(ord(char) < 32 or ord(char) > 126 for char in comment):
            raise PlyError("comments require printable ASCII")
        append("comment " + comment)
    for element in elements:
        if type(element.count) is not int or not 0 <= element.count <= MAX_OFFSET:
            raise PlyError("element count must be a nonnegative signed 64-bit integer")
        append(f"element {_name(element.name)} {element.count}")
        for prop in element.properties:
            if isinstance(prop, ScalarProperty):
                append(f"property {_scalar(prop.scalar_type)} {_name(prop.name)}")
            else:
                append(
                    f"property list {_scalar(prop.count_type, count=True)} {_scalar(prop.item_type)} {_name(prop.name)}"
                )
    append("end_header")
    return read_header(io.BytesIO(raw))


def build_layout(
    header: Header,
    *,
    fixed_lists: Mapping[tuple[str, str], int] | None = None,
) -> Layout:
    """Resolve fixed record offsets without inspecting geometry or allocating rows.

    List lengths are caller-supplied structural assertions, checked on every read
    and write. General variable-length PLY lists are deliberately unsupported.
    """
    # Validate even manually constructed metadata before deriving offsets.
    if read_header(io.BytesIO(header.raw)) != header:
        raise PlyError("header metadata differs from source bytes")
    fixed = dict(fixed_lists or {})
    used: set[tuple[str, str]] = set()
    offset = len(header.raw)
    layouts: list[ElementLayout] = []
    for element in header.elements:
        fields: list[tuple[str, object]] = []
        lists: list[tuple[str, int]] = []
        for prop in element.properties:
            if isinstance(prop, ScalarProperty):
                fields.append((prop.name, SCALARS[prop.scalar_type]))
            else:
                key = (element.name, prop.name)
                length = fixed.get(key)
                if length is None:
                    raise PlyError(
                        "list requires an explicit fixed length",
                        category="unsupported",
                        element=element.name,
                        property_name=prop.name,
                    )
                if (
                    type(length) is not int
                    or not 0 <= length <= np.iinfo(SCALARS[prop.count_type]).max
                ):
                    raise PlyError(
                        "fixed list length exceeds count type",
                        element=element.name,
                        property_name=prop.name,
                    )
                used.add(key)
                # NumPy record item sizes use a signed C int even on 64-bit hosts.
                if (
                    length
                    > (2**31 - 1 - np.dtype(SCALARS[prop.count_type]).itemsize)
                    // np.dtype(SCALARS[prop.item_type]).itemsize
                ):
                    raise PlyError(
                        "record layout exceeds NumPy limits",
                        element=element.name,
                        property_name=prop.name,
                    )
                fields.append(
                    (
                        prop.name,
                        np.dtype(
                            [
                                ("count", SCALARS[prop.count_type]),
                                ("values", SCALARS[prop.item_type], (length,)),
                            ]
                        ),
                    )
                )
                lists.append((prop.name, length))
        try:
            dtype = np.dtype(fields)
        except (ValueError, TypeError, OverflowError) as error:
            raise PlyError("record layout exceeds NumPy limits") from error
        if (
            dtype.itemsize <= 0
            or element.count > (MAX_OFFSET - offset) // dtype.itemsize
        ):
            raise PlyError(
                "record offsets exceed signed 64-bit range", element=element.name
            )
        layouts.append(ElementLayout(element, dtype, offset, tuple(lists)))
        offset += element.count * dtype.itemsize
    if used != set(fixed):
        raise PlyError("fixed list specification contains unknown properties")
    return Layout(header, tuple(layouts), offset)
