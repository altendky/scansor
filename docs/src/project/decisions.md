# Decisions

This log distinguishes product directions from provisional implementation
choices. Items marked provisional may change after experiments.

## Current Directions

### Build a reusable bounded expert product

Scansor is intended to become a reusable product rather than a one-off model
fitting tool. Its first boundary is deliberately narrow; universal automatic
scan-to-CAD is not the goal.

### Require declared topology and correspondence

Users provide supported model structure and map observations to elements.
Automatic topology recognition is outside the initial contract.

### Represent exact constraints structurally

Hard constraints should normally use shared or reduced parameters and explicit
dependencies rather than penalty residuals. Soft and diagnostic relationships
remain distinct.

### Keep the authoritative fit local and auditable

The solver should consume canonical inputs and emit a complete fit-result
record. CAD publication follows review and acceptance rather than replacing the
fit record.

### Integrate instead of rebuilding heavy GUIs

Use external reconstruction, point-cloud selection, and CAD authoring tools
through bounded adapters. Keep both observation-side and CAD-side integrations
replaceable.

### Dual-license under MIT or Apache 2.0

Scansor is licensed under either the MIT License or the Apache License, Version
2.0, at the user's option. Use `MIT OR Apache-2.0` as the SPDX license expression
in package metadata when an implementation stack is selected.

## Provisional Directions

### Sequence the first evidence program through explicit gates

Start with a generated contract, deterministic generator/evaluator oracle,
nominal fit, generated Onshape geometry extraction in Agent Sandbox, nominal
end-to-end evidence, adverse generated evidence, and generated examples. Only
then gate external-source probes, physical validation, and publication planning
or extensions. Real artifacts, captured data, retention, independent
measurements, metrology evidence, and physical pose policy do not block the
initial path. See the [integrated planning program](planning/index.md).

### Start with a bounded stepped rotational model family

Provisionally begin with deterministic matched axisymmetric/asymmetric generated
single-part fixtures using the bounded stepped cylinders, axial planes, and
genuine asymmetric cut summarized in the [first-trial plan](planning/first-trial.md).
Use the pair to expose the roll gauge, full-pose evidence, and flat-factor
ablation. All dimensions are fixture-local and provisional. This direction does
not select a real artifact or establish supported geometry, physical accuracy,
or product support. A cone, blends, assemblies, native constraints, and
multi-component work remain deferred.

### Stage generated CAD extraction before broad snapshots

First generate and extract the geometry-only fixture in the designated Onshape
Agent Sandbox under an immutable pin, then compare it with deterministic
generator truth without inferring intent. The broad cross-platform snapshot that
combines evaluated geometry and native relationships is a later fixture. Scansor
owns canonical identities; platform IDs and topology references remain
revision-scoped bindings and provenance.

In that later work, native mates, joints, and constraints may author a
whitelisted subset of pose, kinematic, and exact relationships. They complement
Scansor fit-role annotations and manifests, and accepted hard relationships
should compile structurally rather than rely on CAD tolerances or solved
placement. Onshape, Fusion 360, SolidWorks, and FreeCAD paths and the required
fixture are recorded in the [CAD extraction
research](cad-constraint-and-geometry-extraction.md).

### Use Python and SciPy for the first evidence-generating prototype

Use Python with `scipy.optimize.least_squares` to implement the first solver
fixture. The prototype should discover residual and Jacobian conventions,
reduced parameterizations, bound and robust-loss behavior, rank and conditioning
diagnostic needs, termination semantics, packaging effort, and the evidence
needed to judge held-out results.

This is a narrow experiment choice, not a production stack decision. It does not
select the eventual production language or solver, decide whether production
should use Ceres, or claim that SciPy satisfies the complete product contract.
An [experiment-local evidence runner](planning/solver-fixture.md) now exists for
the frozen generated scenarios, but no reusable Scansor solver implementation,
product solver, or supported integration exists. The internal inspection/replay
CLI fixture has no fitting path. See the [binding research
finding](ceres-python-rust-bindings.md).

### Use a focused Python foundation for the prototype

Prefer quality third-party libraries when they provide materially better APIs,
typing, validation, diagnostics, or testability than limited standard-library
alternatives. For the first Python prototype, provisionally use Cyclopts for the
CLI and settings-source resolution, Pydantic v2 for boundary models, validation,
provisional schema generation, and serialization, and basedpyright as the
required static type checker.

Cyclopts should resolve CLI, environment, configuration-file, and default
values into a plain Pydantic `BaseModel`; `pydantic-settings` should not load a
second set of sources in the initial architecture. Ty remains a future
challenger rather than a parallel required checker. These choices require an
integration fixture; the first bounded internal CLI fixture now exercises a
subset without settling a production stack or public CLI contract. See the
[foundation library decision](python-foundation-libraries.md).

### Bound the first application mapping contract to one synthetic fixture variant

Use an internal, non-public `stepped-rotational-v0` contract to exercise one
explicit axisymmetric or asymmetric synthetic fixture variant per run. Require an
explicit observation-to-model transform, application-owned thresholds, and a
predeclared canonical-row held-out list. Mapping is analytic and pointwise with no
data-derived pose, threshold, or refinement; present normals are diagnostics only.
Publish complete rejected analyses but publish nothing for malformed, integrity,
transform, or nonfinite failures. Keep its mapping-run format separate from the
existing four-file inspection run. This is a provisional implementation fixture,
not compatibility, production, accuracy, metrology, generic-cloud, or product
support. See the [bounded design](planning/stepped-rotational-v0-observation-mapping.md).

### Keep the first application factor problems separate and explicit

For the internal, non-public `stepped-rotational-v0` synthetic fixture, treat
fixed-pose shape and fixed-geometry pose correction as separate mathematical
problems rather than a joint solver. Instantiate factors only from accepted mapped
training rows, require a separate ordered active-factor selection, use explicit
identity normalization, unit weight, linear loss, and application-owned bounds,
scales, and relative rank policy. Provide pure analytic evaluation and
optimizer-independent preflight only. Held-out evaluation, solver execution, fit
results, factor publication, CAD evidence, physical references, and any `fit` CLI
remain absent. See the [factor contract](planning/stepped-rotational-v0-factor-contract.md).

### Keep execution, termination, disposition, and held-out assessment separate

For the internal synthetic-only `stepped-rotational-v0` fixture, use a pure
solver-independent adapter boundary with application-owned callback validation,
untrusted backend responses, independent final recomputation, and deterministic
result replay. Keep normalized termination distinct from execution disposition
and any future acceptance. Invoke held-out assessment only after a sealed
completed result, assign support from nominal mapping rules before using fitted
parameters, and report raw summaries without policy. This provisional slice
selects no production backend, product acceptance policy, or CLI. The bounded
NumPy implementation and internal execution-run successor do not alter that
separation. See
the [execution/result contract](planning/stepped-rotational-v0-execution-result.md).

### Bound one NumPy backend and execution-run format to the synthetic fixture

Implement one deterministic scaled Gauss-Newton adapter with NumPy SVD for the
two separate `stepped-rotational-v0` problems, including the axisymmetric roll
gauge through full-coordinate local minimum-norm nullspace-orthogonal
unconstrained increments, not a frozen rotation-vector coordinate; active box
bounds remain a distinct feasibility constraint. Publish all valid
execution dispositions through one strict content-addressed internal run format,
with held-out assessment only for completed results and adapter-free read-only
verification. Backend convergence remains distinct from execution completion and
future acceptance. This is synthetic-only, fixed-topology, provisional,
non-public, and compatibility-free; it selects no production solver and adds no
CLI, including `fit`. See the [bounded successor](planning/stepped-rotational-v0-numpy-execution-run.md).

### Expose the completed v0 pipeline through a bounded internal CLI

Add one local file-based successor to the inspection CLI that delegates mapping,
execution publication, and read-only replay to the existing run-level APIs.
Require explicit synthetic variant, units, frames, transform, held-out rows,
problem, initial vector, and activation. The exact
`all-instantiated-primary-training-v0` policy is an explicit choice rather than a
default; exact factor IDs remain the alternative. Use only the fixed provisional
NumPy backend and keep execution, normalized termination, artifact validity, raw
held-out evidence, and absent quality acceptance separate in human output and exit
status. This remains synthetic-only, internal, provisional, non-public, and
compatibility-free. It adds no physical validation, automated acceptance, generic
backend loader, CAD path, or product fitting claim. See the [bounded CLI
successor](planning/stepped-rotational-v0-cli-vertical-slice.md).

### Add one bounded generated noisy-cloud CLI workflow

Preserve the exact revision-1 fixture while adding one application-owned
revision for the asymmetric datum-flat, fixed-pose shape problem. Generate a
guarded analytic float64 XYZ PLY with explicit seed and sigma, bounded
deterministic normal noise, stable fixture identities, coherent held-out roles,
and no outliers. Admit only exact replayed generated provenance to mapping and
retain independent nominal-support association. Add immutable generation-run
publication, read-only replay, and human-only raw truth comparison without
acceptance. CAD-derived sampling, physical evidence, generic clouds, robust loss,
and CAD publication remain deferred. See the [bounded generated slice](planning/stepped-rotational-v0-generated-noise-vertical-slice.md).

### Use a bounded Python support layer for the prototype

Provisionally use structlog for structured runtime diagnostics, Rich for human
terminal presentation, pathlib with platformdirs for paths, HTTPX for HTTP
adapters, pytest with Hypothesis and pytest-cov for tests and coverage reporting,
uv for Python environments and dependencies, and Ruff for formatting and
linting. Each direction remains contingent on evidence from its applicable
detailed requirements and the representative combined fixture in the [support
library evaluation](python-support-libraries.md#combined-validation-fixture).

Runtime logs do not replace authoritative application records, Rich output does
not define machine-readable output, and HTTP retries must be explicit,
operation-specific, bounded, and safe to replay. A retry library, production
build backend, documentation generator, and durable large numeric payload format
remain unselected pending representative requirements and experiments. The
internal fixture's `uv_build` use is not that production selection.

### Use a layered future repository toolchain

Provisionally use mise as the outer tool-version manager, uv as the Python
environment/dependency/command owner, and pre-commit for local gates including
markdownlint-cli2 and Lychee. Internal documentation-link failures should block;
external-link failure policy remains open. Follow the Hamster CI shape with a
thin orchestrator, local reusable workflows, stable aggregate checks, PR
concurrency cancellation, SHA-pinned actions, separate pre-commit and Python
gates, and a future minimum/latest Python matrix using basedpyright.

The observed immutable precedent is recorded separately from Scansor's adaptation
in the [repository tooling page](repository-and-development-tooling.md). Use
Renovate in that shape when repository automation exists, without inferring
automerge or version-coupling rules. These are future configuration directions,
not existing repository automation. The internal fixture's `uv_build` use and
source-package layout do not select a production build backend or public package
shape. Release/deployment tooling, documentation generator, tox/Nox, supported
Python versions, and supported platforms remain open.

## Rejected

### `FitGraph` as the public name

Rejected after collision screening. Graph views remain useful internally, but
the term is not the product name.

## Not Yet Decided

The eventual production language, production solver, and product acceptance
policy remain open, including
whether to use Ceres. No public CLI contract, documentation generator, durable
bulk numeric format, production build backend, public package shape,
release/deployment mechanism, exact Python/platform support matrix, schema format
beyond a JSON-compatible control-record direction, or final public name has been
selected. The
provisional Python choices do not select a production stack; retry and packaging
libraries remain unselected for production. The internal fixture's `uv_build`
configuration and console entry point are development mechanisms, not those
public or production decisions.
