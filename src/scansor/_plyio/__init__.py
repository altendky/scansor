"""Bounded little-endian PLY I/O, independent of the containing application.

Only fixed-width scalar properties and explicitly fixed-length lists are supported.
No units, geometry, canonicalization, paths, hashing or publication live here.
"""

from .format import (
    Element,
    ElementLayout,
    Header,
    Layout,
    ListProperty,
    PlyError,
    ScalarProperty,
    build_layout,
    make_header,
    read_header,
)
from .stream import Reader, Writer

__all__ = [
    "Element",
    "ElementLayout",
    "Header",
    "Layout",
    "ListProperty",
    "PlyError",
    "Reader",
    "ScalarProperty",
    "Writer",
    "build_layout",
    "make_header",
    "read_header",
]
