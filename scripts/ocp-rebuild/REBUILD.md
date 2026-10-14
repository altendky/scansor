# Rebuild and replace the approved OCP/OCCT browser library

These controls reconstruct the existing OCP.wasm
6be21f668a33c86fd58675d8e551dff8817b0e58 / generated OCP 8.0.1.1 /
OCCT b8f597c677811d1f9f4d8a97f5ae2825c0353a42 lineage.
They do not select another binding. The CAD publication gate remains closed until
the modified-library worker proof and actual Scansor CAD/native readback pass.
Original-wheel byte identity is not claimed.
The [executed replacement proof](validation/replacement-proof.json) records the
supported O1 rebuild, three marker-observed workers and all 12 browser/native
Scansor CAD checks. The disposable candidate is not a release artifact.

Use a fresh disposable working directory outside the repository. Agents must use
their prescribed temporary-directory ownership policy. Copy the `controls/` files
to that directory's root. Scripts resolve all destinations relative to themselves,
so do not execute them in place within a published static site.

An existing Python 3.14 interpreter, uv, CMake >=3.24, Ninja and Node are required.
The executed probe used Python 3.14.8, CMake 4.4.4, Ninja 1.13.2 and Node 26.11.1;
the target remains Python 3.14.2 / PyEmscripten ABI 2026_0. Review the retained
`tool-audit.json` and exact `tools.lock` before installation. No optional dependency
extras, distro tool installations, ccache, LTO, swap or global configuration are
required. Do not repurpose HOME or CODEX_HOME.

From the fresh working directory, set UV_CACHE_DIR to an absolute path for its
`uv-cache/` directory and run:

```sh
uv venv --python /absolute/path/to/existing/python3.14 venv
uv pip sync --python venv/bin/python --require-hashes --only-binary :all: tools.lock
uv run --no-project --offline acquire.py download sources.json
uv run --no-project --offline prepare.py
uv run --no-project --offline stage_sdk.py
uv run --no-project --offline configure_sdk.py --jobs 1 --repair-optimization O1
uv run --no-project --offline adapt.py
uv run --no-project --offline preflight.py
uv run --no-project --offline build_monitored.py
```

The `--offline` flags apply to uv's standard-library-only script execution;
`acquire.py download` explicitly retrieves the hash-pinned URLs. Preexisting
archives are reused only after hash verification. When using the corresponding
source package's local archives, place them in `downloads/` using each record's
`download_name`, or use the matching aliases in `sources.json`. SDK, cross-build
environment and Binaryen payloads are separate pinned retrieval inputs, excluded
from the static package. They are staged/configured privately without an SDK
installer, Git clone/fetch, extra Node install or npm install.

`prepare.py` also prepopulates the exact FreeType/zlib port archives. Their
SHA-512 values match the selected Emscripten port recipes. `adapt.py` regenerates
the acquisition patch for the current location, requiring the verified local
source trees. It retains patch diffs and hashes. Do not use a patch containing
somebody else's absolute temporary-directory paths as a portable input.
The acquisition adaptation is limited to fixed hash-pinned inputs in a fresh
tree. It removes version/stamp invalidation, not the four explicitly invoked
upstream compilation patches. Those patch scripts, downstream CMake inputs and
`repair_wasm.py` remain byte-identical to the pinned recipe archive. Use a new
working directory when changing source pins.

`configure_sdk.py --jobs 2` may be used when resource headroom permits. Four jobs
are supported for resumption only with at least four available CPUs and 16 GiB
available memory. This changes host build throughput, not browser threading or
compiler/link settings. Compiler support builds stay serial. The monitor covers
verified descendant processes, including Ninja's
separate command groups, and enforces per-process address space 14 GiB,
aggregate RSS 12 GiB, available memory >=5 GiB, disk free >=8 GiB and a six-hour
watchdog. Preserve failed-attempt logs before any resumption. Do not run
concurrent heavy browser/native validation if it would erode that headroom.
The tail of this probe reached the aggregate RSS limit with four roughly 3 GiB
compiler processes and resumed serially, then at two jobs after three measured
serial targets. Start serially; four-job early binding
headroom does not establish safe tail compilation. The updated monitor records
source inputs for identity-verified owned compilers, not unrelated host argv.

The pinned upstream `repair_wasm.py` also invokes `wasm-opt -O4`; the recorded
`LDFLAGS=-O1` does not replace this separate repair/optimization pass. The initial
probe left `BINARYEN_CORES` unset. Its optimizer had 17 threads, although two
five-second samples observed only one advancing CPU time. The
[pinned Binaryen thread-pool source][binaryen-threads] supports a separate
`BINARYEN_CORES` limit. That attempt hit the unchanged aggregate RSS guard and
produced no wheel. The distributed configuration now defaults to
`BINARYEN_CORES=1`, matching the capped-thread retry with unchanged source and
optimization flags. That retry was stopped after 94.7 minutes of active work,
with a measured 9.38 GiB aggregate RSS peak and no safety stop. Every recorded
owned descendant exited; its logs and repaired input were preserved. No
completed-wheel memory or performance benefit is claimed.
Do not confuse Ninja parallelism with optimizer or browser runtime threading.

The default `--repair-optimization O1` sets `CI=0`, which selects the pinned
repair script's local release `-O1` branch. `--repair-optimization O4` selects
the upstream CI branch. Both retain `Release`, the same C++ compiler/link
settings, instruction repair, exports and source lineage. Nonempty `CI=0`
also preserves the inspected backend's CI-presence logging and tool-discovery
behavior. This is an explicit probe optimization-profile change, not an original
publisher equivalence claim. The O1 final-target retry completed in 9.71 minutes
with a 3.94 GiB aggregate RSS peak, reusing all C++ objects and the linked module.
Those figures exclude the preceding full compilation. Its exact candidate wheel
then passed the modified-kernel and Scansor browser/native proof.

[binaryen-threads]: https://github.com/WebAssembly/binaryen/blob/fba5005a132d85d578be5bd010e0487ed8e2ce3e/src/support/threads.cpp#L129-L180

The modification used to demonstrate replacement changes only OCCT's compiled
Emscripten `OSD_Process::UserName()` result to `scansor-226-relinked-occt`.
Geometry algorithms and OCP's `8.0.1.1` runtime version remain intact. After a
wheel exists, verify CPython 3.14 / PyEmscripten ABI 2026_0 metadata and record the
wheel/module hashes. Load that exact wheel in a fresh browser Pyodide worker and
assert:

```python
from OCP.OSD import OSD_Process
assert OSD_Process().UserName().ToCString() == "scansor-226-relinked-occt"
```

Construct a fresh disposable Scansor artifact with coherent candidate CAD asset
hashes and no network fallback to the published wheel. Run numerical startup,
the real `experiments/browser_static/validate-cad.mjs` cases and native readback
of browser STEP/ZIP outputs in the existing native OCP environment. Preserve
source/tool/environment/patch identities, actual results and resource evidence.
Kernel compilation or a marker string in an object file alone is insufficient.

The original publisher used mutable Pyodide metadata, a latest Binaryen download
and CI build caches; its run logs/artifacts are no longer available. This newly
pinned route therefore demonstrates source/relink usability, rather than exact
historical reconstruction. Mandatory selected tool licenses are permissive/MPL;
OCCT is the previously approved LGPL-2.1 lineage with its header exception.
Keep the complete original component notices and source modifications, and use
the permissive FTL choice for FreeType. Optional copyleft license texts/build
utilities in source distributions are not selected runtime/tool components.
