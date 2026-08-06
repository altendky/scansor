# Stepped Rotational v0 CLI Vertical Slice

## Status

**Provisional internal implementation design, snapshot dated 2026-08-03.** This
page bounds a local file-based command-line successor that exposes the completed
`stepped-rotational-v0` mapping and execution-run pipeline. Every command,
configuration name, artifact format, and Python interface remains internal,
provisional, non-public, compatibility-free, synthetic-only, and fixed-topology.
This slice does not modify the existing inspection, mapping, factor, execution,
held-out, or persistence formats.

## Commands And Ownership

The existing `scansor inspect` and `scansor verify` commands retain their current
inputs, outputs, configuration behavior, and inspection-run semantics. This
successor adds four commands:

- `scansor map` verifies one inspection run, constructs a strict mapping request
  from its recorded provenance plus explicit user assertions, and calls the
  existing mapping-run publisher.
- `scansor verify-mapping` read-only verifies and replays one mapping run against
  its explicitly supplied inspection run.
- `scansor fit` verifies its inspection and mapping inputs, constructs one
  explicit problem, initial vector, and active-factor selection, and calls the
  existing fixed NumPy execution-run publisher.
- `scansor verify-fit` read-only verifies and replays one execution run against
  explicitly supplied inspection and mapping runs without constructing or
  invoking a backend.

Inspection owns PLY ingestion and canonical metre conversion. Mapping owns
analytic nominal-support association and mapping diagnostics. Factor construction
owns declarations and factor identities. The fit command owns only explicit
selection and initial-vector orchestration. The existing execution-run API owns
the fixed NumPy adapter, guarded execution, sealed result, post-seal held-out
assessment, manifest, and atomic publication. Verification owns no artifacts and
performs no writes.

## Explicit Inputs

`map` requires an inspection run, a new output path, variant, source and canonical
unit assertions, observation and model frame assertions, transform direction and
scale, three rotation rows, translation in metres, translation-unit assertion,
and held-out row indices. All six effective mapping thresholds are also required
explicit inputs: maximum support distance, minimum geometric clearance, minimum
region samples, relative rank threshold, transform tolerance, and transition
guard. Although the lower-level model retains application-owned defaults for pure
API use, the CLI never supplies or infers them. The command derives row count,
report hash, canonical hash, inspection run ID, and synthetic-fixture provenance
from verified artifacts. Users never provide or calculate internal hashes or IDs.

Only `axisymmetric` and `asymmetric-datum-flat` are valid variants. Units must
state the existing metre contract, frames must match verified fixture provenance,
direction must be `observation-to-model`, scale must be exactly one, and the
held-out indices must state the designated synthetic fixture's exact predeclared
selection. No frame, unit, transform, variant, threshold tuning, pose, held-out
role, or source identity is inferred.

`verify-mapping` requires the mapping and inspection run paths. Persisted request
values are authoritative; current map settings cannot alter replay.

`fit` requires inspection, mapping, and new output paths; explicit variant and
problem; explicit initial-parameter units and ordered values; and exactly one
activation mode. The modes are:

- `all-instantiated-primary-training-v0`, an explicit request to select every
  factor instantiated from primary mapped training observations in existing
  factor-set order; or
- `exact-factor-ids`, with an explicit ordered factor-ID list, including an
  explicitly empty list when the caller intends an analyzable ineligible run.

Omission never means all factors. Supplying IDs with the all-instantiated policy,
or omitting the list for the exact-ID policy, is invalid. Availability,
membership, candidates, mappings, and held-out observations never activate a
factor. The only problems are `fixed-pose-shape` and
`fixed-geometry-pose-correction`. Initial vectors have no automatic or CAD-derived
value and must state `metre` or `metre/radian` consistently with the problem.
The backend is fixed to
`scansor.numpy-gauss-newton.stepped-rotational-v0` revision `provisional-1`; there
is no backend name, loader, registry, import path, or fallback option.

`verify-fit` requires execution, inspection, and mapping run paths. Persisted
selection, initial values, adapter descriptor, and limits are authoritative.

## Configuration And Precedence

Cyclopts resolves command values into strict frozen Pydantic models. Sources have
the exact precedence:

1. explicit command-line value
2. recognized `SCANSOR_*` environment variable
3. one TOML file selected with explicit `--config`
4. validated Pydantic default, only for bounded operational settings

There is no implicit configuration-file search and no second settings loader.
The TOML document contains exactly one flat `[scansor]` table. Unknown TOML
fields and unknown `SCANSOR_*` variables fail. Mutating `inspect`, `map`, and
`fit` commands also reject recognized settings that belong to another command so
ambient state cannot silently affect or obscure publication intent. Known fields
unrelated to the selected read-only verifier are ignored, even when malformed,
and cannot alter persisted semantics. Relative paths are resolved from the
invocation directory, preserving existing behavior.

Collections replace lower-precedence collections rather than merging. TOML uses
native arrays so high-entropy values remain usable: each rotation row,
translation, held-out indices, initial vector, and exact factor IDs is an array.
Collection-valued environment variables use JSON arrays. CLI collections use
repeated values as accepted by the pinned Cyclopts integration. Malformed,
nonfinite, wrong-dimensional, duplicate, reordered, inconsistent, or extra
values fail before publication.

## Publication And Failures

`map` and `fit` route through `create_mapping_run` and `create_execution_run`.
They do not duplicate serialization, hashing, path safety, input replay, staging,
no-replace rename, rollback, or identity checks. Existing output paths are never
overwritten. Outputs inside input trees and unsafe or replaced roots fail closed.
Configuration, malformed input, provenance, integrity, path, or publication
failure leaves the requested output unpublished; existing APIs may preserve
foreign or changed staging material rather than deleting content they no longer
own.

A completely analyzed mapping rejection or execution non-success is a valid
published artifact, not a configuration failure. Completed execution alone
permits the existing post-seal held-out assessment. Held-out observations never
enter mapping candidates, factors, activation, initialization, callbacks,
thresholds, bounds, loss, or fitting.

Both verifier commands require exact bounded inventories, canonical encodings,
hashes, identities, provenance, and deterministic replay. They modify neither
their run trees nor referenced source artifacts. `verify-fit` is adapter-free and
cannot invoke the NumPy backend.

## Human Status And Exit Semantics

Standard output is concise and deterministic and includes relevant run IDs and
the requested output or verified run path. Runtime logs remain non-authoritative
standard-error diagnostics. Fit output reports four concepts separately:

- `execution`: `completed`, `ineligible`, `failed`, or `invalid-backend-output`
- `termination`: the normalized backend/application termination category
- `artifact validity`: `valid-published` or `valid-verified`
- `quality assessment`: `not configured` for completed execution, or
  `not performed` otherwise

The persisted disposition name `completed-not-assessed` is rendered as
`execution: completed`; it is not shown as a bare user-facing status. “Quality
assessment: not configured” means no automated fit-quality or acceptance policy
exists. Neither convergence, completion, artifact validity, nor raw held-out
evidence is called an accepted fit.

Exit codes are:

- `0`: accepted mapping publication; converged completed execution publication;
  or successful verification of any internally valid recorded disposition
- `2`: configuration, input, schema, integrity, provenance, path, or publication
  error with no requested publication
- `3`: valid published mapping rejection, ineligible execution, execution
  failure, invalid backend output, or completed execution with limit, stopped,
  failure, or unknown backend termination

Verification returns `0` for a valid rejected or non-successful artifact because
it verifies integrity and replay, not the recorded outcome. Parser and validation
errors are normalized to exit `2` without tracebacks.

Publication is authoritative once the existing atomic run-level API returns. If
writing the post-publication human status raises an output `OSError`, the command
best-effort warns on standard error and retains the already determined publication
exit: `0` for an accepted/converged publication or `3` for a valid analyzed
adverse publication. It never relabels a new published artifact as exit `2`.

## Claim Boundary

This CLI accepts only the designated deterministic synthetic
`stepped-rotational-v0` fixture revision and the two existing fixed mathematical
problems. It adds no arbitrary-cloud behavior, automatic correspondence,
automatic initialization, remapping, trusted-normal path, robust loss, quality
threshold, expert acceptance record, CAD input, CAD reconciliation, CAD
publication, external observation adapter, or physical reference.

Passing tests supplies implementation evidence for constructed synthetic inputs
only. It establishes no physical validation, accuracy, metrology suitability,
automated quality acceptance, production backend, product support, public schema,
public CLI, compatibility promise, packaging support, release support, or CAD
publication capability. Frozen `stepped-rotational-v1` solver, Track 5,
reconciliation, evidence, sidecars, and tolerances remain unchanged read-only
context.
