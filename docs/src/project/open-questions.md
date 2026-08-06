# Open Questions and First Experiments

## Status

**Open with observed evidence dated 2026-07-30.** The internal provisional
generated-observation contract, offline generated-data repeatability, public v2
fixture, CAD cross-run reproducibility, and bounded evaluator/solver gate for all
`15` frozen scenarios are [observed](experiments/generated-onshape-fixture.md).
The gate verifies each expected execution path; assigned dispositions include
pass, review, and fail, while fixed outliers remain unclassified without an
approved robustness policy. The bounded nominal generated-experiment
reconciliation is now observed; broader adapters, external-source probes,
additional combined adverse evidence, and physical experiments remain open.
Results should update the architecture and decisions rather than be inferred in
advance.

## Immediate Planning Decisions

- What retention duration and cleanup timing should each disposable public
  generated-fixture evidence record specify?
- Which additional combined or non-finite adverse cases, if any, are justified
  after the isolated frozen scenarios passed their bounded gate?
- Should later CAD fixtures retain the frozen `stepped-rotational-v1` contract's
  fixture-local `1e-9 m` and `1e-9 rad` comparison tolerances or preregister
  different fixture-local values?

The [integrated planning program](planning/index.md) owns the detailed sequence
and track-specific choices. The user has decided that every Onshape document for
this work is public; generated-fixture evidence retention and cleanup/disposition
remain an initial gate. Real-artifact, captured/physical/third-party data
retention, measurement-equipment, metrology-evidence, physical-pose,
CloudCompare, and RealityScan decisions are later and do not block the generated
path.

## Immediate Quick Checks

- Verify the Scansor and Agent Sandbox organization facts separately from
  account/API, document, immutable revision, export, and publication access.
- Verify public visibility and exclusion of sensitive, proprietary, external,
  captured, credential, and physical-reference data from generated documents.
- Verify the exact generated cylinders, planes, asymmetric cut, matched
  axisymmetric variant, units, and fixture-local status.
- Check exact evaluator outputs against deterministic generator truth.
- Verify bounds preserve positive dimensions, station ordering, adjacency, and
  nonempty topology.
- Verify the axisymmetric scenario exposes axial-roll gauge and datum-flat
  factors supply the intended roll evidence; ablation must restore the null.
- Assert that generated held-out observations create no fit factor and occur in
  no solver callback.

## First Experiments

### Generated solver and evaluator fixture

The exact evaluator oracle and all `15` frozen scenarios described by the
[solver fixture](planning/solver-fixture.md) now pass their executable predicates
in an experiment-local SciPy runner. This verifies expected execution and
fail-closed behavior rather than assigning every scenario a passing disposition;
the fixed-outlier control has no approved classification. Keep membership,
mapping, factor, active-factor, and held-out semantics unchanged during later
reconciliation or additional adverse work.

### CAD geometry-only probe

The bounded public single-part fixture generation and analytic readback described
by the [read-only adapter track](planning/read-only-adapters.md) are observed for
two immutable versions of one Onshape document in Agent Sandbox. The [evidence
report](experiments/generated-onshape-fixture.md) retains MCP-returned response
bodies plus generated metadata and checksums; bounded normalization, comparison
with frozen truth, and cross-run semantic equivalence are observed. Authenticated
HTTP transport envelopes, general normalization beyond the supported subset, a
complete adapter snapshot, and any product-support commitment remain open.

The full cross-platform geometry-and-native-relationship fixture described by
the [CAD extraction research](cad-constraint-and-geometry-extraction.md#later-broad-cross-platform-fixture)
is a later experiment. It remains deferred with assemblies, repeated/nested
occurrences, native relationships, topology-change stress, and publication.

### End-to-end validation seed

The separate reconciliation gate now verifies deterministic source and solver
evidence before opening CAD, freezes the nominal fitted geometry, and compares it
independently with both retained normalized Onshape runs. Generated truth,
generated training, generated held-out, CAD, future captured observations, and
future physical reference remain separate evidence classes. Next, decide whether
additional combined adverse cases are justified and author non-normative generated
examples. This evidence supports constructed-scenario correctness only, not
physical accuracy.

### Later external observation probes

After nominal generated end-to-end success and Track 2 Phase B, test CloudCompare
overlap, point identity/order, units, frames, and attributes. Test RealityScan
classification through PLY, XYZ, and LAS if authorized. Neither candidate is a
support commitment.

### Later physical validation seed

Choose a real object and protocol only after generated and external-source gates.
Collect captured observations with held-out regions and compare against properly
reserved independent physical references. Use that evidence to develop bounded
physical claims, not to reinterpret synthetic evidence retroactively.

### Later solver comparison fixture

After the deterministic fixture is stable, compare a challenger against the same
semantics, generator truth, observations, factors, and scenarios. Record
correctness and diagnostic sufficiency before comparing speed.

If that evidence supports a need for Ceres, implement the same fixture with
pyceres and direct C++ Ceres. Compare solution and Jacobian correctness,
diagnostics, packaging and reproducibility, callback overhead, and
representative performance. Binding-specific probes are detailed in the
[Ceres Python and Rust binding research](ceres-python-rust-bindings.md).

### Python foundation integration fixture

Exercise the provisional Cyclopts, Pydantic, and basedpyright foundation before
relying on it. Test CLI, environment, explicit-file, standard-file, and default
precedence; nested and collection overrides; Pydantic field and model
validation; malformed and unknown values; secret redaction; source-aware
errors; resolved-value provenance; schema and serialization behavior; and
representative NumPy, SciPy callback, and adapter-protocol typing. Include the
known risks identified in the [foundation library
decision](python-foundation-libraries.md#required-integration-evidence).

### Python support integration fixture

Exercise the provisional diagnostics, terminal, path, HTTP, test, environment,
and code-quality directions through one small CLI and adapter flow. Use scripted
failures and representative data to test redaction and correlation, TTY and
machine-output separation, platform paths, timeout and replay-safe retry policy,
property-test reproduction, clean environment recreation, documentation checks,
and bulk numeric round trips if JSON is unsuitable. Evaluation should include
the applicable category-specific requirements as well as the representative
combined fixture in the [support library
evaluation](python-support-libraries.md#combined-validation-fixture).

### Repository tooling fixture

Before relying on future repository automation, exercise clean mise/uv bootstrap,
pre-commit, Markdown and internal-link failures, Ruff format and lint checks,
basedpyright, the pytest runner with Hypothesis properties and pytest-cov
reporting, minimum/latest Python dependency resolution, stable CI aggregates and
cancellation, SHA pinning, and a Renovate dry run. Keep external-link policy,
exact versions/platforms, and release machinery open. See the [repository
tooling evidence](repository-and-development-tooling.md#required-validation-evidence).

## Product and Technical Questions

- Which evidence would justify promoting or replacing the internal
  `stepped-rotational-v0` synthetic mapping thresholds, region coverage minima,
  and shape-incidence rank policy? They are not public defaults or Track 5 CAD
  tolerances.
- What evidence would justify promoting or replacing the implemented internal
  synthetic-only CLI/settings boundary without changing inspection-run semantics
  or implying a public compatibility promise?
- Which production backend and backend-specific initialization/scaling policy,
  if any, should later implement the provisional solver-independent
  `stepped-rotational-v0` execution boundary? The bounded NumPy adapter is not
  that selection.
- What evidence and expert workflow should define a future acceptance record
  without conflating normalized termination, execution disposition, or raw
  post-fit held-out summaries with acceptance?
- What evidence, migration policy, authentication, and durability requirements
  would justify promoting or replacing the internal provisional execution-run
  format? It has no public compatibility promise.
- What later real artifact and protocol can validly test the provisional bounded
  family without treating synthetic truth as physical truth?
- What are the canonical, versioned schemas for models, observations,
  memberships, mapping manifests, raw CAD snapshots/sidecars, and fit results?
- Which eventual production language and nonlinear least-squares solver best
  support reduced parameterizations, robust losses, derivatives, diagnostics,
  packaging, and CAD adapters? Selecting any production stack, whether retaining
  or moving beyond the Python/SciPy baseline, requires fixture evidence about
  correctness, diagnostics, deployment, safety, and representative performance.
- What is the smallest useful CLI shape without prematurely fixing the eventual
  interaction model?
- Where exactly do adapter responsibilities end, and how are identity,
  capability negotiation, errors, and publication transactions represented?
- Which native mate/joint kinds can be normalized without ambiguity, and which
  source topology changes require user-assisted binding repair?
- Which one or few observation and CAD adapters should the first supported
  product include?
- What validation corpus, reference measurements, repeatability tests, and
  acceptance thresholds are required before making accuracy claims?
- Is a documentation generator useful, and if so which one? What navigation,
  publication, versioning, search, or API-reference requirement justifies it?
- Which adapter operations are safe to retry, what idempotency or reconciliation
  mechanisms do their remote APIs provide, and does the resulting policy justify
  Tenacity, backoff, or a small local implementation?
- At what payload size and access pattern should observation arrays move out of
  JSON-compatible control records, and do Parquet or Arrow, Zarr, HDF5, or an
  adapter-native format meet the required schema, audit, and interoperability
  boundary?
- What package shape, supported platforms, native components, and release
  channel must exist before selecting a build backend and deployment mechanism?
- Which exact Python versions and development/CI platforms should the first
  repository fixture cover?
- Does the future repository remain GitHub-hosted, as the provisional CI and
  Renovate directions assume?
- Can `Scansor` be cleared as the public name given the unrelated existing
  SAP-monitoring software, or is another name required?
- How should the product distinguish solver failure, invalid geometry, weak
  identifiability, inadequate coverage, bad correspondence, and model mismatch
  in language useful to experts?
