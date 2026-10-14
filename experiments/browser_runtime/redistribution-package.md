# Browser redistribution package

The optional notice/source package is separate from computation assets. The
runtime and static builders accept `--redistribution` only when its bytes match
the reviewed `scripts/browser-redistribution-lock.json` and its runtime/UI input
bindings match the selected build. Omitting it retains the local probe's
`redistribution_ready: false` status. Readiness records requirement evidence;
it does not authorize publishing or changing DNS.

## Assembly and consumption

Use fresh external output directories and the existing native/UI environments:

```sh
uv run scripts/build-browser-redistribution.py \
  --inventory scripts/browser-redistribution-lock.json \
  --output "$TASK_NOTICES" --cache "$TASK_NOTICE_CACHE"
uv run scripts/build-browser-runtime.py --output "$TASK_RUNTIME" \
  --cad --redistribution "$TASK_NOTICES"
uv run scripts/build-browser-app.py --runtime "$TASK_RUNTIME" \
  --output "$TASK_SITE" --redistribution "$TASK_NOTICES"
```

The assembler uses the Python standard library and installs or executes no
downloaded package. Every input archive/file is verified before extracting only
pinned notice members. Covered-source archives are retained intact. A corrupt
cache fails instead of being replaced. Output must be fresh and outside project
checkouts; cache and output cannot overlap. Acquisition uses pinned URLs,
hashes and sizes. SDK binaries and installations are excluded from the artifact.

Repeated `--group numeric`, `--group ui` or `--group cad` limits assembly.
A numerical-only runtime consumes the numeric group; the CAD profile additionally
requires CAD. Static UI assembly consumes UI and revalidates a ready runtime's
evidence against the same lock. Missing groups/requirements or mismatched locks
fail. Selected manifests and per-group source-location records refer to copied
files; `inventory.json` preserves the full approved input plan. Source/notice
files remain available through hosting but excluded from worker downloads.

## Requirement evidence

- Numeric: pinned core and actual wheel notices, Cargo-lock notice supersets,
  matching Rust standard-library/compiler-support notices, Pyodide/hiwire
  covered sources, and upstream CPython modification/patch provenance.
- UI: locked npm runtime notices, recorded publisher correspondence for
  supplements, and conservative notices for possible Vite/Rolldown runtime
  helpers. This does not imply all build dependencies ship.
- CAD: notices and corresponding source for the existing approved lineage and
  selected dependencies, build/patch/repair controls, pinned tool retrieval and
  a demonstrated modified-library replacement route. Kernel compilation remains
  outside ordinary static application CI.

The assembler leaves `existing-core-and-wheel-notices` external. The runtime
builder fulfills it by verifying pinned core files and comparing extracted
notices with actual locked wheel members. The approved CAD wheel has no embedded
notice tree; CAD's separate supplement provides binding/kernel/dependency
notices and sources. Static assembly rechecks runtime requirements rather than
accepting a ready flag alone. Final manifests retain requirement evidence and
notice/source identities; missing supplements leave redistribution gates closed.

Notice supersets do not claim an exact measured linked graph. Target exclusions
and correspondence caveats remain recorded; UEFI-specific components and unused
license alternatives are not selected Emscripten dependencies. Source availability
is distinct from historical toolchain attestation or byte-identical rebuilding.
The [component inventory](redistribution.md) records the original lineage and its
limits. No alternative binding or new copyleft dependency is adopted.
