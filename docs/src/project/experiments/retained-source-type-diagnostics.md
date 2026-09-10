# Retained Experiment Type Diagnostics

## Status and Disposition

**Implemented bounded cleanup, 2026-09-09.** [Issue #4][issue] investigated all
`250` basedpyright exceptions present at commit
`e78f3e55d16b42061cade0f6a0146dcaf59921fe`. The approved cleanup resolves `146`
diagnostics in the solver and reconciliation sources and retains `104` exceptions
with the reasons below. Both sources, their regenerated evidence and sidecars,
the baseline, and documented identities have been updated. The generator and
Track 5 capture identities remain unchanged.

The [approved patch archive][approved-patch] records the exact changes prepared
and validated in a disposable copy before approval. Its application reproduced
all `11` reviewed after hashes. All four sources and both retained evidence files
match their adjacent SHA-256 sidecars. The declared-model package workflow has
not superseded these frozen experiment chains.

The issue's [readiness clarification][readiness] explicitly requires:

> Before changing retained source bytes, record and approve the affected
> regeneration/repinning chain; justified retained baseline entries remain
> acceptable.

The user reviewed and approved this specific solver/reconciliation patch and
regeneration chain in the issue-processing conversation before its application.
That approval covers this bounded cleanup. General experiment retention,
generated-source maintenance, and capture-evidence migration policies remain open.

| Source | Before | Corrected | Retained exceptions |
| --- | ---: | ---: | ---: |
| `generate_stepped_rotational_v1.py` | 18 | 0 | 18 |
| `generated_solver_evaluator_v1.py` | 145 | 120 | 25 |
| `generated_track5_cad_reconciliation_v1.py` | 26 | 26 | 0 |
| `track5_onshape_cad_repro.py` | 61 | 0 | 61 |
| **Total** | **250** | **146** | **104** |

## Measurement and Diagnostic Inventory

The measurements use locked basedpyright `1.39.9`, project CPython `3.12.13`, and
the existing recommended-mode settings over `src`, `tests`, and `experiments`.
Before cleanup, analysis without baseline suppression reported `104` errors and
`146` warnings across `79` files. After cleanup it reports `12` errors and `92`
warnings. The reduced baseline suppresses exactly those `104` retained
diagnostics; the ordinary repository gate reports zero errors, warnings, and
notes. A passing baseline gate does not mean the frozen sources are free of type
diagnostics.

The [per-diagnostic inventory][inventory] records every diagnostic's source,
original zero-based line/character range, rule, one-based entry in that file's
original baseline, final disposition, and correction category. Duplicate diagnostics
at the same location are separate entries. Source, baseline, configuration, and
lock hashes bind the inventory to the original measurement; the applied-change
record separately identifies the resulting artifacts. The rule and range-shape
multiset matched the original baseline exactly for each file. The remaining
diagnostics also match the reduced baseline exactly. This comparison uses
columns and line count because baseline entries do not store line numbers.

The original diagnostic totals by rule were:

| Rule | Count |
| --- | ---: |
| `reportUnknownMemberType` | 66 |
| `reportUnusedCallResult` | 59 |
| `reportIndexIssue` | 41 |
| `reportArgumentType` | 40 |
| `reportUnannotatedClassAttribute` | 9 |
| `reportCallIssue` | 6 |
| `reportOperatorIssue` | 6 |
| `reportImplicitStringConcatenation` | 5 |
| `reportAttributeAccessIssue` | 3 |
| `reportGeneralTypeIssues` | 2 |
| `reportMissingImports` | 2 |
| `reportOptionalSubscript` | 2 |
| `reportUnknownLambdaType` | 2 |
| `reportUnnecessaryComparison` | 2 |
| `reportUnusedFunction` | 2 |
| `reportPossiblyUnboundVariable` | 1 |
| `reportReturnType` | 1 |
| `reportUnnecessaryIsInstance` | 1 |
| **Total** | **250** |

An isolated configuration can expose diagnostics without editing the repository
configuration or baseline. Preserve the repository's import roots: relocating
only the configuration creates unrelated missing-import and missing-stub errors.
Also, this basedpyright version gives the configured `baselineFile` precedence
over `--baselinefile`, so that command-line override alone does not expose the
suppressed diagnostics.

From the repository root, create a disposable equivalent configuration:

```console
scratch=$(mktemp -d)
uv run --locked --python 3.12.13 python - "$scratch" <<'PY'
import json
import sys
import tomllib
from pathlib import Path

root = Path.cwd()
scratch = Path(sys.argv[1])
config = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["basedpyright"]
config["include"] = [str(root / item) for item in config["include"]]
config["baselineFile"] = str(scratch / "absent-baseline.json")
config["venvPath"] = str(root)
config["venv"] = ".venv"
config["executionEnvironments"] = [
    {"root": str(root), "extraPaths": [str(root / "src")]}
]
(scratch / "pyrightconfig.json").write_text(json.dumps(config, indent=2) + "\n")
PY
uv run --locked --python 3.12.13 basedpyright \
  --project "$scratch/pyrightconfig.json" --outputjson > "$scratch/diagnostics.json"
```

The final command exits `1` because it exposes the existing errors. Inspect its
JSON summary and diagnostic identities; an exit code alone does not establish
the expected measurement. Run the ordinary baseline gate separately:

```console
uv run --locked --python 3.12.13 basedpyright
```

## Applied Corrections

Inventory entries marked `corrected` are resolved by the approved patch:

| Correction category | Solver | Reconciliation | Concrete change |
| --- | ---: | ---: | --- |
| Explicit discarded result | 6 | 11 | Assign `_ =` while retaining each call and its order, including integrity checks, callback probes, writes, and argument registration. |
| Explicit string concatenation | 3 | 0 | Use `+` between the existing message pieces without changing their resulting text. |
| Class attribute annotation | 6 | 0 | Annotate callback state with its existing parameter or counter type. |
| Local container annotation | 15 | 5 | Annotate numerical rows, residuals, factors, findings, and traversal stacks; preserve all list operations and ordering. |
| Heterogeneous evidence records | 90 | 0 | Annotate scenario, active-bound, and mismatch records at construction, consistently with existing dynamic report interfaces. |
| Verified JSON boundary | 0 | 9 | Give the selected scenario an explicit dictionary type after the existing object check and prior semantic verification. |
| Explicit descriptor lifetime | 0 | 1 | Initialize the directory descriptor to `None` and test it explicitly in `finally`, preserving closure after a successful open and failure before opening. |
| **Total** | **120** | **26** | |

The annotations describe internal collections and existing heterogeneous report
boundaries. `dict[str, Any]` does not validate a schema; the cleanup retains
the independent source, checksum, runtime, semantic, role, ordering, and
geometry checks. It adds no casts, inline ignores, rule exclusions, numerical
algorithm changes, acceptance thresholds, or dependency changes.

The diagnostic count is dominated by inference cascading through the solver's
unannotated `scenario_results` and other heterogeneous dictionaries. Individual
index, call, argument, operator, and iterable diagnostics do not represent `90`
independent numerical defects. Regenerated evidence comparison, rather than that
inference, supplies the bounded behavior-preservation evidence below.

## Reasons for Retained Exceptions

### Generator identity: 18 diagnostics

`retain-generator-identity` preserves the exact `1.0.5` generator used by the
solver and Track 5 source pins. Ten discarded results, one implicit string join,
and one unannotated numerical-row list have straightforward prospective source
corrections. Five member diagnostics concern JSON membership/runtime records
whose container checks do not propagate through the type checker's element
inference. The remaining return diagnostic is a NumPy `bool_` result in a
callback advertised as returning Python `bool`; an explicit conversion is a
possible correction, subject to revalidation of that callback contract.

These are not inherently unfixable diagnostics. The reason to retain them in
this cleanup is the transitive generator identity recorded in both CAD runs'
request metadata and all three other sources. Even `_ =` changes that identity.
Regenerating synthetic observations alone cannot decide how to represent the
historical CAD evidence's old generator provenance. A separate, explicitly
reviewed migration or successor-evidence design would be needed. This cleanup
preserves the existing CAD provenance.

### CAD capture identity: 61 diagnostics

`retain-capture-identity` preserves the verifier/assembler source recorded in both
historical capture bundles. Thirty-two discarded results, one implicit string
join, three class attributes, and local container/stack annotations are candidates
for explicit corrections under a separately reviewed evidence migration.

The remaining categories require more specific treatment:

- JSON member diagnostics and probe response accesses cross validated dynamic
  shapes. `spec.kind`, response shape, and optional `spec.variant` are correlated
  by the frozen operation protocol; a later correction must expose that
  correlation without accepting unsupported responses or weakening checks.
- Optional `right_variants` and trim diagnostics follow the truth-versus-cross-run
  branch. Preserve branch semantics and absent-trim rejection when narrowing.
- The two `bytearray`/`bytes` comparison diagnostics do not demonstrate broken
  gzip header checks: these values compare by content at runtime. Preserve the
  existing gzip rejection tests; removing the checks would weaken verification.
- The apparently unnecessary header `isinstance` is a defensive runtime check
  despite its declared mapping value type. Preserve the rejection boundary.
- The unused private `_stream_gzip_artifact` and `_walk_files` wrappers are not
  used by current repository callers. Removing them only to lower the count
  still changes the captured tool identity; no evidence-safe deletion is selected.

All `61` entries retain individual locations and prospective actions in the
inventory. Retention is tied to capture provenance, not a blanket exception for
every dynamic program or every experiment file.

### Isolated SciPy boundary: 25 diagnostics

`retain-isolated-scipy-boundary` covers the solver's two missing SciPy imports and
`23` downstream member diagnostics: version reads and `least_squares` result
fields. The package environment does not install SciPy. The solver deliberately
uses its standalone inline dependency declaration: exact CPython `3.12.12`,
NumPy `2.3.1`, and SciPy `1.16.1`. Package checking uses CPython `3.12.13` and the
project lock, including NumPy `2.5.1`; it does not establish types for that
separate solver environment.

Adding SciPy to application dependencies, replacing static imports with dynamic
imports, or asserting unchecked solver-result types merely to silence diagnostics
would misrepresent this boundary. Keep the `25` baseline entries visible until
an experiment-specific analysis environment or reviewed, accurate typing boundary
is selected. Runtime result and evidence tests remain separate evidence; they do
not resolve these static diagnostics.

## Source and Evidence Dependency Audit

The four Python files are executable experiment sources. No repository template
or upstream source-generation command was found that emits these Python files.
The word `generated` describes the synthetic experiment/evidence, not a discovered
authoritative code generator. The available artifact-generation paths are:

| Source | Existing generation/verification path | Consequences of a source-byte change |
| --- | --- | --- |
| Generator | `--output`, two independent runs, `--compare`, 37 self-checks | Adjacent source sidecar; generated truth-manifest provenance/bytes; solver's generator pin and regenerated solver evidence; Track 5 `GENERATOR_SOURCE_SHA256`/`SOURCE_PINS`; both runs' compressed request records, logs, manifests, normalization, suite manifest, and checksums; reconciliation pins/evidence; documented identities. Contract/record/scenario/numerical hashes must be independently compared, not silently replaced. |
| Solver | `run --output`, `verify-evidence`, exact-runtime 22-test suite | Adjacent source sidecar; regenerated solver evidence and sidecar; reconciliation's solver source/evidence pins, its source sidecar and regenerated evidence/sidecar; documented identities. No Track 5 capture change is needed when the generator stays fixed. |
| Track 5 | `assemble-capture`, `assemble-suite`, `verify-evidence`, `replay`, `verify-suite`, 39-test suite | Adjacent source sidecar; each run's capture-tool metadata, 8 compressed request records, operation log, normalized output, manifest and checksum; suite manifest/checksum; reconciliation source/manifest pins and regenerated evidence; documented identities. Recompute derived reports and verify response-body invariance. |
| Reconciliation | `generate` to stdout, `verify-evidence`, exact-runtime 25-test suite | Adjacent source sidecar; regenerated reconciliation evidence and sidecar; documented identities. Upstream sources, captures, and solver evidence stay fixed unless independently changed. |

The generator and Track 5 hashes occur in all `16` compressed **request metadata**
records. The audit found neither source hash in the retained compressed response
bodies. Those requests were assembled offline; they are not authenticated HTTP
envelopes. Any future migration must distinguish original acquisition provenance
from later verification/assembly provenance and preserve the original response
bytes. Replacing old capture timestamps with the time of reassembly would not
reproduce the historical capture.

Commit `1ff1290a4e539ca890414940e71c8c1171651647` previously changed Track 5 and
reconciliation formatting, request metadata, manifests, and documented hashes.
That is an observed migration precedent, not an authoritative general migration
procedure or permission to repeat it. `assemble-capture` produces new runtime
metadata, so it is not an identity-preserving historical reassembly command.
Response origin, pairing authenticity, and no-Main-write history remain bounded
as described in the [original experiment observation][observation].

## Approved Regeneration and Repinning Chain

The approved patch changes only the solver/reconciliation branch of the dependency
graph. Its application set is `11` files, in addition to this investigation's
inventory, patch archive, and documentation/navigation additions:

- the two solver/reconciliation `.py` sources and their two `.sha256` sidecars
- the two corresponding `*-evidence.json` files and their two sidecars
- `.basedpyright/baseline.json`
- the existing identity references in [solver planning][solver-planning] and
  the [experiment observation][observation]

Generator, Track 5 source, request, response, operation log, normalized CAD record,
run manifest, suite manifest, raw-change note, and Onshape object identities are
unchanged. The local bytes were copied for candidate replay and compared with the
originals, then checked again after application.

The construction order is significant:

1. Apply and format the solver's explicit source corrections. Compute its new
   source SHA-256 and adjacent sidecar.
2. Run the solver's `run --output` in exact CPython `3.12.12`, NumPy `2.3.1`,
   SciPy `1.16.1`. The runner creates evidence and its checksum. Independently
   compare old and new parsed evidence: permit only `/solver_source_sha256` to
   change. Reject any numerical, scenario, callback, policy, or runtime drift.
3. Apply the reconciliation source corrections and update only `SOLVER_SHA256`
   and `SOLVER_EVIDENCE_SHA256`. Compute the new reconciliation source sidecar.
4. Run reconciliation `generate` into a disposable output. Its isolated solver
   verification must complete before it opens CAD evidence. Compute the output
   checksum. Independently compare parsed old/new evidence: permit only
   `/inputs/reconciliation_source_sha256`, `/inputs/solver_source_sha256`, and
   `/inputs/solver_evidence_sha256` to change.
5. Run both changed sources' exact-runtime tests and evidence verification.
   Recheck unchanged generator/Track 5 bytes and all CAD evidence files. Review
   every source, evidence, and documentation difference.
6. Remove only the `146` resolved baseline entries, retain the individually
   identified `104`, and run broad basedpyright plus applicable repository gates.
   Update documented identities only to the verified candidate hashes.

The current retained sources and evidence use these approved new identities:

| Artifact | SHA-256 |
| --- | --- |
| Solver source | `ebd748bb1da0f6f617085fb1502081a3d590cec2c1dd151dd820b67ff435525d` |
| Solver evidence | `53469faf65ae88d864c301738354be6a540740c6291a6e78c6a2ed0ebfbe446c` |
| Reconciliation source | `3659f46ac93be68b2f85c7a0501c6c9944e74a2e2612d456cffb7d62290c4eab` |
| Reconciliation evidence | `e3f56adb904d906f13ed22af4878ea58790cb3dfa8415abdf4f23a44ee552c57` |

The inventory binds the archived patch hash and every application file's
before/after hash. A fresh disposable application and the approved repository
application both reproduced all `11` expected after hashes. The patch is a
historical review artifact against the original commit; it must not be reapplied
to the updated sources.

## Validation

The original retained sources passed the following commands before source or
evidence changes. After application, both changed sources' test commands and full
reconciliation verification were repeated against the updated repository:

```console
uv run --offline --python 3.12.12 experiments/track5_onshape_cad_repro_test.py
uv run --offline --python 3.12.12 experiments/generated_solver_evaluator_v1_test.py
uv run --offline --python 3.12.12 \
  experiments/generated_track5_cad_reconciliation_v1_test.py
uv run --offline --python 3.12.12 \
  experiments/generated_track5_cad_reconciliation_v1.py verify-evidence \
  experiments/generated-track5-cad-reconciliation-v1-evidence.json
```

These run `39`, `22`, and `25` tests respectively. The solver test suite includes
retained-evidence recomputation. Reconciliation verification additionally runs
the isolated exact-runtime solver verifier, checks all source and manifest pins,
recomputes the Track 5 suite, and replays both CAD runs. Its output remains
`generated experiment reconciliation only`.

Pre-approval validation also ran the changed sources' suites and full
reconciliation verification from the disposable tree. Solver generation passed
all `34` gate checks. Parsed evidence changes only at the four source/evidence
identity paths permitted above. Numerical results, frozen scenario dispositions,
event ordering, selected estimate, CAD comparisons, and runtime records are equal.
The reduced baseline was checked both in the disposable tree and after
application to the repository. Across `79` files, the updated sources have `12`
errors and `92` warnings before baseline suppression, exactly matching the `104`
retained entries by file, rule, and range shape. With that baseline, the gate
exits successfully with zero errors, warnings, and notes. All `53` generator/
Track 5 source, test, sidecar, and CAD evidence files remain byte-identical to
the original commit. Focused descriptor checks
cover successful reads, failed parent opens, and failed leaf opens without file
descriptor growth.

Repository-wide Ruff format/lint and all applicable blocking pre-commit checks
pass over tracked files and the new review artifacts. Normal package pytest is
not a substitute for these experiment commands: `testpaths = ["tests"]` excludes
`experiments/`. No package source, dependency, or test changes were made, so
package matrix/coverage reruns are not evidence for this investigation's
retained-source corrections.
Generator output regeneration is not required by the selected cleanup because
the generator bytes and its pins do not change.

## Decisions Still Open

- Retain-old-plus-successor versus replacement-in-place remains open for future
  capture/generator migrations. This cleanup replaced only
  these locally recomputable solver/reconciliation results, with the original
  committed identities still recorded in history and this inventory.
- No authoritative process for regenerating these Python sources from an
  upstream template was established. Broader source-generation ownership is open.
- An experiment-specific SciPy typing environment or result contract is open.
  The selected `25` dynamic-boundary exceptions are not a production-stack choice.
- New models, physical accuracy, metrology, general CAD adapter support,
  robustness policy, public schema compatibility, and product readiness remain
  outside these bounded experiment claims.

[approved-patch]: ../../../../.basedpyright/issue-4-cleanup.patch
[inventory]: ../../../../.basedpyright/issue-4-disposition.json
[issue]: https://github.com/altendky/scansor/issues/4
[observation]: generated-onshape-fixture.md
[readiness]: https://github.com/altendky/scansor/issues/4#issuecomment-5610758377
[solver-planning]: ../planning/solver-fixture.md
