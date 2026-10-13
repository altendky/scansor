# Runtime redistribution inventory

The pinned runtime is usable for local probes. Publication requires preserving
the applicable licenses, notices and source/build obligations; a wheel filename
or top-level license classifier is not a complete inventory. No new alternative
binding is selected here.

| Component | License / required follow-up |
| --- | --- |
| Pyodide 314.0.7 | MPL-2.0; retain license, corresponding covered source and build/patch provenance |
| CPython 3.14.2 | PSF and bundled component notices; preserve actual browser build patches |
| NumPy 2.4.6 | BSD and vendored component notices; wheel notices are extracted |
| Pydantic 2.12.5 | MIT; wheel license is extracted |
| pydantic-core 2.41.5 | MIT glue plus statically compiled Rust dependencies; top-level wheel license is incomplete, collect exact Cargo.lock component notices |
| annotated-types / typing-inspection | MIT; wheel licenses are extracted |
| typing-extensions | PSF; wheel license is extracted |
| Emscripten / core native libraries | Preserve libc/compiler-support, ffi, hiwire, lzma, SQLite, zstd, zlib, bzip2 and MiniLZ4 notices matching the build |
| Optional OCP.wasm | MIT glue; upstream wheel lacks a complete notice/source bundle |
| OCP 8.0.1.1 | Apache-2.0 generated bindings and source inputs |
| OCCT 8.0.1 | Previously approved LGPL-2.1 lineage with header exception; preserve corresponding source, patches and usable build/relink route; exception does not remove all library obligations |
| CAD native dependencies | pybind11 BSD-3, RapidJSON MIT/component notices, fmt MIT, FreeType permissive FTL choice, zlib and Emscripten notices; verify components actually linked |

The pydantic-core inventory includes Unicode-3.0 ICU data and
Unicode-DFS-2016 material in unicode-ident in addition to MIT/Apache crates.
An optional LGPL alternative for the UEFI-specific r-efi crate is not a browser
dependency and is not selected. No mandatory new copyleft numerical component
has been identified; that does not replace complete notice collection.

Core license/source inputs are pinned by URL, SHA-256 and size in
`runtime-lock.json` and included under `notices/core/`. Some notices are the
copyright headers in retained upstream source files. The pydantic-core source
archive supplies its exact Cargo.lock for follow-up notice collection. Scansor's
own license choices and the numerical wheels' notice trees are included too.
The XZ inventory describes both its public-domain liblzma and separate copyleft
utilities/build scripts; those utilities are not adopted by this runtime. SQLite
is [public domain](https://www.sqlite.org/copyright.html), and its browser build
disables GNU readline.

The optional CAD wheel is pinned to upstream OCP.wasm revision
`6be21f668a33c86fd58675d8e551dff8817b0e58` and
[published build run](https://github.com/yeicor/OCP.wasm/actions/runs/36032580576).
Its publisher/hash/build metadata was inspected; the attestation signature was
not independently verified. Its recipe used mutable Pyodide metadata and a
latest Binaryen download, so exact reproducible rebuilding has not been shown.
Pinned binary hashes provide repeatable asset selection, not a proven
byte-identical source rebuild.

Before distributing CAD, preserve the exact OCCT/OCP and generated binding
sources, OCP.wasm patches/recipe, permissively licensed transitive sources and
licenses, toolchain versions and instructions sufficient to rebuild/relink.
The OCP generated-source release is available separately from its wheel; it
also lacks a complete license bundle. Keep the build route usable rather than
relying solely on a transient CI artifact.

Primary starting sources, verified during the October 2026 investigation:

- [Pyodide source](https://github.com/pyodide/pyodide/tree/314.0.7)
- [Pinned Pyodide package catalog](https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide-lock.json)
- [pydantic-core source](https://github.com/pydantic/pydantic-core/tree/v2.41.5)
- [OCP.wasm source and recipe](https://github.com/yeicor/OCP.wasm/tree/6be21f668a33c86fd58675d8e551dff8817b0e58)
- [OCP generated-source release](https://github.com/CadQuery/OCP/releases/tag/8.0.1.1)
- [OCCT 8.0.1 source](https://github.com/Open-Cascade-SAS/OCCT/tree/b8f597c677811d1f9f4d8a97f5ae2825c0353a42)

Later static artifact/deployment work must close these publication gaps or
explicitly remain a local probe. Do not infer publication approval from the
runtime smoke test.
