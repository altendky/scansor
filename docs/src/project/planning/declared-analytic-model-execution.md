# Declared Analytic Model Execution

## Status and boundary

**Provisional internal implementation, snapshot dated 2026-09-08.** This is a
compatibility-free, model-bound successor to the stepped execution/result and
execution-run records. It consumes the
[declared factor and preflight records](declared-analytic-model-factor-preflight.md)
for project-generated synthetic observations, known observation-to-model pose,
and fixed-pose shape parameters only.

The implementation comprises strict records in
`src/scansor/declared_execution_models.py`, guarded execution and replay in
`src/scansor/declared_execution.py`, the fixed adapter in
`src/scansor/declared_numpy_backend.py`, and internal publication and verification
in `src/scansor/declared_execution_run_models.py` and
`src/scansor/declared_execution_runs.py`.

These internal entry points establish bounded synthetic evidence. They do not
define a public format, compatibility contract, general solver/plugin API,
production backend selection, initialization policy, robust loss, pose fitting,
acceptance policy, physical validity, accuracy, CAD publication, or product
support. The complete generated-cloud workflow for a materially different
second topology remains separately sequenced in issue #14.

## Model-bound execution boundary

A request binds the exact canonical declaration and model ID, factor set,
explicit active selection, initial model-bound vector, adapter descriptor,
parameter order, inclusive bounds, coordinate scales, and resource limits.
Requests and invocations independently validate their derived policy against
the embedded declaration. Recomputing an artifact hash cannot make an
inconsistent order, dimension, scale, bound, or model binding valid.

The declaration supplies every parameter and element count, structural-validity
predicate, required-support and nominal-coverage policy, and minimum rank
requirement. Element IDs remain opaque. The successor does not derive policy
from variants, element names, fixed parameter positions, or inventories.

Preflight remains optimizer-independent. Empty selection, missing support or
coverage, invalid bounds or structure, evaluation failure, and insufficient
declared rank prevent invocation. Malformed or mixed model graphs fail at the
boundary before diagnostics.

The callback wrapper independently checks dimensions, finite values, inclusive
bounds, structural validity, and evaluator results. It records canonical
successful or rejected attempts and bounded finite-safe consistency evidence
for nonfinite input, reentrancy, incomplete callbacks, and budget exhaustion.
Sealing closes the callback before final result construction.

The application treats the adapter response as untrusted. It validates schema,
content identity, provenance, final vector, and structural validity, then
independently recomputes final residuals and full Jacobians through
`declared_factors.evaluate_factors` and the shared geometry evaluator. Final
objective is the raw linear-loss value `0.5 * sum(residual_m ** 2)`. Bound
activity is independently derived from declaration bounds.
Aggregation preserves finite representable objectives even when an intermediate
sum of squares would overflow. An unrepresentable final objective produces a
sealed, replayable `final-objective-nonfinite` execution failure.

## Scaling, rank, and bounded NumPy steps

The fixed adapter is `scansor.numpy-gauss-newton.declared-analytic-model`, revision
`provisional-1`. Declaration revision `scansor-declared-analytic-model-v2`
explicitly assigns the existing ordered positive `diagnostic_scale` values the
additional role of full-vector execution-coordinate normalization for this
adapter. This semantic revision changes model IDs and all dependent identities.

For physical Jacobian `J`, ordered coordinate scales `S`, and residual scale
`s_r` from `optimization_preflight.relative_rank`, the adapter forms
`J * S / s_r` and `residual_m / s_r` for its step calculation. This internal
normalization does not change the raw final objective or factor loss.

Rank eligibility is a separate calculation. At the initial and accepted trial
vectors, the adapter selects exactly the rank policy's ordered parameter subset,
uses that policy's parameter scales and residual scale, and counts singular
values strictly greater than the largest singular value times its declared
relative threshold. Empty or all-zero rank matrices have rank zero. Only rank
below `required_rank` produces `rank-deficient`; excess rank is permitted.

The optimizer computes an SVD minimum-norm increment in all declared scaled
coordinates. Its fixed step truncation cutoff is `1e-10` relative to the largest
singular value. This numerical step policy does not impose a full-rank or
exact-rank eligibility condition. Bound-active coordinates pointing outward are
removed and the feasible tangent-space step is recomputed. A non-descent
increment may use the projected negative gradient. Trials must pass declared
structural validity before a callback is attempted.

The deterministic limits and stopping rules retain the bounded implementation
posture:

- at most 64 accepted iterations and 256 callbacks, further limited by the
  request's callback budget;
- at most 64 declared parameters per execution request, a topology-independent
  resource cap;
- at most 13 line-search trials per iteration, multiplying by `0.5` with Armijo
  coefficient `1e-4`;
- residual infinity tolerance `1e-12` metres and dimensionless projected-gradient
  infinity tolerance `1e-12`;
- relative scaled-step threshold `1e-12` and relative objective change `1e-15`
  for three successive accepted steps;
- callback traces bounded by the requested byte limit and the 16 MiB hard cap,
  with execution result serialization bounded by 64 MiB.

Residual or projected-gradient convergence precedes step/objective stagnation.
Numerical and SVD failures remain explicit responses. Limits and stopping rules
are adapter implementation semantics bound by its identity, not fit acceptance.

## Held-out assessment

Assessment is separately invoked after a sealed `completed-not-assessed` result
has passed replay. It reconstructs the factor set and selection from the exact
mapping and checks their identity.

For each predeclared held-out row, `observation_mapping.assess_nominal_support`
classifies support using the mapping request's nominal declaration geometry,
bounded-domain policy, and geometric thresholds. Fitted parameters never alter
assignment. Only an assigned element is evaluated at the final parameter vector
through `DeclaredGeometryEvaluator.evaluate_fixed_pose_shape`; the residual is
raw and oriented. Ambiguous, transition, gap, outlier, and evaluation-error
outcomes remain separately visible.

Rows and assessments are model-bound. Summary values describe assigned raw
residuals only, using overflow-safe mean and RMS aggregation when needed.
Assessment cannot change the sealed execution result or its
identity. Held-out values do not enter factors, activation, support/coverage
counts, rank diagnostics, optimizer steps, or policy. A changed source/mapping
revision can change provenance identities without changing training numerics.

## Publication and read-only verification

The successor run path uses new declared-analytic-model format identifiers.
Completed runs contain `selection.json`, `result.json`, `held-out.json`,
`manifest.json`, and `manifest.sha256`. Noncompleted runs omit `held-out.json`.
The manifest binds the exact model declaration, model ID, mapping and inspection
references, execution selection, adapter, result, disposition, held-out state,
and artifact inventory. The run ID addresses its canonical semantic content.

Publication requires independently replay-verified synthetic inspection and
mapping inputs. Descriptor-anchored reads, artifact size limits, hashes,
no-overwrite staging, input-tree exclusion, fresh input verification, and
identity-aware cleanup preserve the existing filesystem boundary. The format
can represent varying declared model sizes; generated admission for a second
topology remains part of the separately sequenced generated workflow.

Verification is read-only and never invokes the optimizer. It verifies canonical
bytes and inventories, reconstructs declared factors, activation, requests,
preflight, every replayable callback evaluation, final evidence, held-out
assessment, and the manifest. It rejects model substitution, declaration
tampering, inconsistent dimensions/policy, and mixed model artifacts, including
records whose hashes were recomputed after tampering.

Normalized termination, execution disposition, and raw held-out assessment
remain separate. `completed-not-assessed` describes a validated execution
response with independently recomputed final evidence; even a backend-reported
limit, stop, or failure can have that disposition. It never means fit acceptance.

## Verification evidence

`tests/test_declared_execution.py`, `tests/test_declared_numpy_backend.py`, and
`tests/test_declared_execution_runs.py` exercise guarded execution, variable
declarations, minimum/subsequence rank policy, model and artifact tampering,
nominal held-out assignment, deterministic execution/replay, and backend-free
publication verification. Both stepped declarations run through the shared
declared machinery with legacy numerical regression comparisons. Constructed
shell declarations vary parameter and element counts, identifiers, scales,
orientation, and scalar relationships.

These constructed cases test application boundaries; they are not evidence of
admission for external clouds or the complete second-topology generated workflow.
Retained experiment evidence has its own frozen identities and is not
reinterpreted as successor-format evidence.
