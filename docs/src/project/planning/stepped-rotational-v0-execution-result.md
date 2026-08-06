# Stepped Rotational v0 Execution and Result Contract

## Status

**Provisional internal implementation design, snapshot dated 2026-08-01.** This
page bounds a synthetic-only, fixed-topology, non-public execution/result slice
for `stepped-rotational-v0`. It has no compatibility promise and makes no
production, solver-quality, fit-accuracy, physical-accuracy, metrology, or
product-support claim. It does not select a production solver backend, define a
product acceptance policy, add persistence, or add any CLI, including `fit`.

The slice does not import, modify, regenerate, or reinterpret the frozen
experiment-local solver, Track 5, reconciliation, evidence, or tolerance
materials. Those remain mathematical and status-separation context only.

## Separated Records and Evidence

The following stages remain explicit and independent:

1. **Mapping** assigns training observations to nominal analytic supports and
   reserves declared held-out rows.
2. **Instantiated factors** bind accepted training mappings to exact residual
   declarations and mapped points.
3. **Active factors** are a separate ordered selection; availability never
   activates a factor.
4. **Execution** supplies only declared numerical metadata and a guarded
   residual/Jacobian callback to a structural backend adapter.
5. **Normalized termination** translates bounded backend claims and
   application-observed failures into stable execution categories.
6. **Disposition** states only whether execution was ineligible, failed, had
   invalid backend output, or completed without assessment.
7. **Post-fit held-out assessment** is separately invoked only after a sealed
   `completed-not-assessed` result and cannot alter that result.
8. **Future acceptance** remains an unselected expert/product policy and is not
   implied by convergence, completion, or held-out residuals.
9. **CAD evidence** remains separate post-fit source evidence and never enters
   this execution or held-out path.
10. **Physical reference** remains later independent validation evidence and is
    absent here.

## Solver-Independent Execution Boundary

An execution request content-binds the exact factor contract, instantiated
factor set, active-factor selection, variant, problem, initial parameter vector,
effective parameter order, inclusive bounds, diagnostic scales, callback
protocol revision and limit, and exact adapter descriptor. Effective numerical
policy is copied unchanged from the revalidated factor contract. The request has
no path, timestamp, randomness, mapping object, held-out observation, normal,
CAD value, physical reference, or acceptance threshold.

The application exposes only a structural backend adapter protocol. A runtime
adapter must expose a strict content-addressed descriptor; execution revalidates
it and requires exact equality with the request descriptor before preflight or
invocation. The adapter receives immutable invocation metadata, dimensions,
initial values, bounds, scales, provenance IDs, and one residual/Jacobian
callback. It never receives a `MappingResult`, canonical array, held-out row,
normal, CAD object, file, or application acceptance decision. No backend
implementation is selected or provided by this contract. The separate
[bounded successor](stepped-rotational-v0-numpy-execution-run.md) now provides one
synthetic-only provisional NumPy implementation without selecting a production
backend.

Every public boundary reconstructs supplied Pydantic objects from their Python
representation before reading them. This rejects stale or forged
`model_copy(update=...)` values. Execution recomputes preflight from the bound
initial vector. An ineligible request returns `ineligible` without constructing
an invocation or calling the adapter.

The `stepped-rotational-callback-v2` wrapper is sequential and non-reentrant.
It atomically reserves each sequence slot, then either commits that slot exactly
once or seals it as incomplete. Closing the wrapper atomically freezes an
immutable trace; late completion can neither mutate the trace nor return
successfully. A request may explicitly choose a lower callback cap, but the
application-owned maximum is `10,000`. A cap of `N` permits exactly `N` retained
attempts; attempt `N+1` is represented by bounded terminal evidence rather than
an extra trace entry.

The request also binds an effective incremental retained-trace byte cap, at most
the application-owned `16 MiB` maximum. Every prospective canonical trace entry
is checked before retention. A single-entry or cumulative overflow discards the
prospective entry and fails closed with bounded evidence. Lower per-request caps
exist to make conservative resource limits explicit and testable; an adapter
cannot choose or raise either cap. Both caps and the protocol revision participate
in request and invocation identity.

For every retained call the wrapper validates exact dimension, finiteness,
inclusive bounds, fixed structural shape domain, and factor evaluation domain. It
never clamps, remaps, reweights, filters, changes active factors, or changes
factor order. Successful and rejected retained calls receive deterministic trace
records and stable content IDs.

## Untrusted Backend Response

The adapter may return any Python object. A usable response must pass a strict,
extra-forbidding, content-addressed schema and bind the exact invocation,
request, adapter, dimensions, and provenance. The proposed final vector must be
finite, in inclusive bounds, structurally valid, and evaluable. Raw backend code
and message fields are length-bounded. Tracebacks, platform exception text,
backend objective values, backend residuals/Jacobians, backend bound masks, and
backend acceptance claims are never authoritative semantic inputs.

The application independently recomputes final residuals, Jacobian, objective
`0.5 * math.fsum(r * r for r in active-factor order)`, and exact inclusive bound
activity. The final vector is response-owned and remains independent from the
last callback query.

## Termination, Disposition, and Failures

Normalized termination is separate from disposition and future acceptance.
Stable termination categories are `not-invoked`, `backend-converged`,
`backend-limit-reached`, `backend-stopped`, `backend-reported-failure`,
`backend-unknown`, `adapter-raised`, `callback-rejected`, and
`invalid-response`. A backend convergence claim means only that the backend made
that bounded claim; it never means accepted.

Stable dispositions are `ineligible`, `execution-failed`,
`invalid-backend-output`, and `completed-not-assessed`. Canonically ordered
failure codes distinguish preflight, adapter, callback, response, and final
application-evaluation failures. Exception class, traceback, platform text, and
wall-clock behavior do not enter semantic IDs.

For adapter exceptions and malformed external responses that cannot be retained
as validated responses, replay checks a bounded wrapper-owned failure-evidence
record. Reentrant calls, sealed incomplete slots, sanitized nonfinite inputs,
trace-budget exhaustion, and over-limit observations similarly use explicit
bounded consistency evidence when original occurrence cannot be reproduced from
retained finite inputs. An evaluation failure is consistency-only only when its
bounded evidence names the same-thread reentrant call that interrupted it.
Terminal evidence must account for the retained reservation prefix and each
sequence can have only one outcome. These records prove internal consistency
only. Content hashes detect record changes; they do not authenticate occurrence,
authorship, a runtime adapter, or another external event.

## Result and Replay

A strict result binds the request, invocation and adapter provenance, factor and
preflight IDs, initial application evaluation, wrapper-owned callback trace and
count, validated raw response when available, normalized termination, optional
final vector/evaluation/objective/bound activity, disposition, ordered failures,
and result ID. It contains no timestamp, path, random value, filesystem run,
manifest, settings record, command record, CAD value, or acceptance field.

Canonical serialization is deterministic ASCII JSON with an explicit byte
limit. Parsing rejects duplicate keys, nonfinite JSON tokens, non-ASCII bytes,
oversized records, omitted canonical defaults, and noncanonical bytes. Replay
revalidates the complete graph and all content IDs, recomputes preflight and
initial/final application evaluations, reproduces every callback outcome whose
bounded finite input evidence permits reproduction, checks strict allowed
combinations for consistency-only observations, recomputes retained trace-byte
accounting, objective and bound activity, and verifies each disposition from the
facts it permits. It never turns adjacent labels into proof of an undefined
evaluation. Replay never invokes an adapter or reads mapping or held-out data.

## Separately Invoked Held-Out Assessment

Held-out assessment accepts only a sealed `completed-not-assessed` execution
result and the exact mapping supplied in a separate call. It revalidates the
request, result, factor set, selection, and mapping, then reconstructs factor
instantiation and activation to prove exact provenance. Execution canonical
bytes and ID are captured before reading held-out rows and asserted unchanged
afterward.

Support assignment uses only the frozen v0 nominal mapping geometry, variant,
thresholds, transition guards, and element order. Fitted parameters never
choose, clamp, or change support. The record retains nominal support candidates
and distinct `assigned`, `ambiguous`, `transition`, `gap`, and `outlier` counts.
It creates no training `CandidateRecord`, `MappingRecord`, `MembershipRecord`,
factor, or active factor. Normals do not participate.

Only after nominal assignment does the assessment evaluate the assigned support
using final shape parameters or final pose correction, as applicable. Undefined
or nonfinite evaluation is an evaluation error. The deterministic output contains
counts, row records, raw metre residuals, and raw finite summaries only. It has no
threshold, weighting, tuning, pass/fail, acceptance, CAD comparison, or effect on
the execution result, ID, bytes, termination, or disposition.
Held-out model validation independently recomputes count, minimum, maximum, mean,
and root-mean-square values from finite assigned-row residuals, including the
all-`None` summary required when no row is assigned.

## Explicit Deferrals

Production backend selection, production backend-specific policy,
production initialization policy, robust loss, product acceptance,
settings, public schemas, all execution/factor/mapping CLI commands, `fit`, CAD
reconciliation, CAD publication, physical reference, external observations,
accuracy claims, metrology claims, packaging, and release work remain separately
gated.

The [successor backend and execution-run slice](stepped-rotational-v0-numpy-execution-run.md)
adds one bounded deterministic NumPy adapter and one internal provisional
filesystem format. It does not change the remaining deferrals above.
