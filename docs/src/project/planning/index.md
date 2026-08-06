# Integrated Planning Program

## Status

**Provisional, snapshot dated 2026-07-30.** This program organizes the first
evidence-generating work. Its governing invariant is deterministic generator
truth, generated observations, and a disposable generated Onshape fixture pair
first; external-source and physical validation follow only after a nominal
generated end-to-end path works. Public generated fixture creation and bounded
feature/body readback are [observed](../experiments/generated-onshape-fixture.md).
The internal provisional generated-observation contract, offline generated-data
repeatability, and one v2 CAD-to-truth readback are now observed. CAD cross-run
reproducibility now passes its bounded gate: run-01 and run-02 each match frozen
truth within preregistered tolerances, and cross-run normalized geometry is
semantically equivalent. The bounded mathematical solver/evaluator execution gate
now verifies exactly all `15` frozen scenarios, including expected failed and
review-required dispositions and one unclassified fixed-outlier control with no
approved policy. This is not a claim that all scenario dispositions pass. Full
nominal generated-experiment reconciliation now passes a separate read-only gate:
the verified `noiseless-fixed-pose` estimate was frozen before CAD access and
matched both independently replayed CAD runs within the existing tolerances, with
no CAD influence on fitting or held-out evaluation. Any additional combined
adverse evidence and reusable implementation remain pending. The program
does not implement
Scansor, validate an integration, or establish a production stack. Current
product boundaries and research findings remain authoritative in the parent
[project documentation](../index.md).

## Tracks

- [Stepped rotational v0 observation mapping](stepped-rotational-v0-observation-mapping.md)
  - provisional internal synthetic-only contracts, pure analytic mapping,
  separate publication/replay, and fail-closed diagnostics; no fit or CLI command
- [Stepped rotational v0 factor contract](stepped-rotational-v0-factor-contract.md)
  - provisional application-owned factor declarations, explicit activation,
  analytic shape/pose evaluation, and deterministic preflight; no joint solver,
  fit result, publication format, or CLI command
- [Stepped rotational v0 execution and result contract](stepped-rotational-v0-execution-result.md)
  - provisional solver-independent execution, strict result/replay semantics,
  and separately invoked held-out assessment; no acceptance policy or CLI command
- [Stepped rotational v0 NumPy backend and execution run](stepped-rotational-v0-numpy-execution-run.md)
  - provisional deterministic bounded backend and internal execution-run
  publication/read-only verification; synthetic-only, non-public, no production
  backend or compatibility claim, and no CLI command
- [First CLI vertical slice](first-cli-vertical-slice.md) - provisional local PLY
  inspection and read-only replay fixture; all interfaces and formats are
  internal, provisional, and non-public, and fitting remains explicitly deferred
- [Stepped rotational v0 CLI vertical slice](stepped-rotational-v0-cli-vertical-slice.md)
  - provisional successor exposing the completed synthetic-only mapping and fixed
  NumPy execution-run pipeline without changing its persisted formats or defining
  physical validation, automated quality acceptance, or a public interface
- [Track 1: first trial and model family](first-trial.md)
- [Track 2: access and evidence preflight](access-preflight.md)
- [Track 3: canonical example records](example-records.md)
- [Track 4: solver evidence fixture](solver-fixture.md)
- [Track 5: generated CAD and read-only adapters](read-only-adapters.md)

## Shared Terminology

These meanings govern the planning tracks:

| Term | Planning meaning |
| --- | --- |
| Canonical model | User-confirmed, application-owned fitting intent: topology, elements, parameter roles, bounds, and relationships. Extracted CAD geometry does not infer it. |
| Generator truth | Deterministic fixture geometry, pose, observation construction, and scenario expectations fixed before evaluator or solver output. |
| Observation | An identified generated or captured sample with units, frame, provenance, optional attributes, and explicit memberships. |
| Membership | A many-to-many selection or classification label. Membership alone creates no mapping or factor. |
| Mapping | Explicit fit intent associating observations or groups with model elements and roles. |
| Factor/residual | One explicitly declared contribution to fitting or diagnostics. Scenario activation is an explicit factor-ID selection independent of observation availability, mappings, and memberships. Overlapping memberships do not imply duplicate factors. |
| Hard relationship | A relationship enforced structurally through shared/reduced parameters or dependencies, not a large penalty. |
| Soft relationship | An explicitly scaled objective contribution. |
| Diagnostic relationship | Evidence that is reported without affecting fitting. |
| Held-out observation | An observation excluded from fit factors, robust-scale selection, and tuning, then evaluated after fitting. |
| Generated Onshape fixture pair | Matched disposable axisymmetric/asymmetric CAD realizations of generator-defined geometry in the designated Agent Sandbox; they are extraction evidence, not independent truth. |
| Independent physical reference | A separately obtained physical measurement with method, instrument, repetition, and uncertainty. It is not CAD nominal geometry or held-out scan data. |
| Raw source snapshot | Immutable source evidence tied to an explicit revision/configuration and acquisition context. |
| Source binding | A revision-scoped association between canonical identity and a source/platform identifier, not permanent identity. |
| Fit result | The authoritative local record of resolved inputs, estimates, diagnostics, termination, and evidence. |
| Acceptance | Explicit expert judgment separate from solver termination. |
| Publication plan | A pure, inspectable description of intended effects against pinned targets. It performs no writes. |
| Publication outcome | Effectful execution plus fresh read-back and reconciliation evidence. It is outside the initial program. |
| Example record | A non-normative illustration of agreed semantics. It must not define a schema by accident. |

## Common Provisional Generated Fixture Pair

The first contract uses matched single-part, fixture-local generated variants.
All dimensions below are provisional and do not define product support:

- model axis `+Z`
- cylindrical band radius `12 mm` over `z = 0..20 mm`
- cylindrical band radius `18 mm` over `z = 20..50 mm`
- cylindrical band radius `14 mm` over `z = 50..80 mm`
- axial/transverse planes at the exposed ends and band transitions
- an asymmetric variant with a genuine planar cut at `x = 16 mm` over
  `z = 20..50 mm`, whose outward normal defines `+X`
- a matched axisymmetric variant without the cut

The generator defines truth, observations, memberships, mappings, factors,
per-scenario active factor IDs, held-out roles, and expected evidence independently
of CAD extraction. The
asymmetric cut must change generated geometry, be sampled by generated
observations, and participate in geometric factors when full axial roll is
estimated. Removing its factors while retaining the observations is a flat-factor
ablation that must restore the roll null. The matched axisymmetric variant must
report an axial-roll gauge/equivalence class rather than full pose observability.

The equivalent Onshape fixture pair was generated in one public document in the
designated disposable `Agent Sandbox`, folder ID
`b788af3dad6250b9ed521e6a`; its creation and bounded analytic readback are
[observed research evidence](../experiments/generated-onshape-fixture.md), not
generator-oracle or end-to-end evidence. No assembly, native constraint, blend,
publication executor, durable schema/format, or support commitment belongs to
the nominal path. A cone and all broader geometry or integration cases are
later, separately gated extensions.

## Evidence Separation

Keep these sources distinct and traceable:

- deterministic generator truth supports implementation, formulation, and
  diagnostic correctness under constructed scenarios
- generated observations are derived evidence, not independent truth
- only training observations may participate in explicitly declared fit factors
- generated held-out strips and sectors create no fit factors and test behavior
  outside the fitted evidence
- CAD nominal values describe source design intent or an explicitly approved
  initialization role
- independent physical references support limited dimensional comparison only
  after fitting

Synthetic evidence can never establish physical accuracy, metrology suitability,
external-platform behavior not directly observed, or product support. External
source evidence and physical truth remain separate later evidence classes.

For a later physical trial, independent reference values must be designated and
reserved before physical fitting. They must not enter factor construction,
bounds, priors, initialization tuning, robust-scale choice, or
acceptance-threshold tuning unless explicitly reclassified as fit inputs before
that trial.

## Track Ownership

| Track | Primary ownership |
| --- | --- |
| Track 1 | Generated model/factor meaning, bounded domains, frames, parameter roles, exact relationships, gauge meaning, mappings, later physical-trial semantics, and claim boundary |
| Track 2 | Phase A sandbox/auth/evidence-disposition preflight; later Phase B external-source, physical-artifact, retention, and independent-reference preflight |
| Track 3 | Hand-authored non-normative generated examples after owning semantics and nominal evidence stabilize; later physical/executor examples |
| Track 4 | Deterministic generator oracle, numerical instantiation, reduced coordinates, derivatives, scenarios, diagnostics, runner evidence, and challenger comparison |
| Track 5 | Generated Onshape CAD extraction first; later external observation probes, retained response bodies and generated metadata, normalization, degradation/capability findings, and publication-plan design |

The separate `stepped-rotational-v0` application slices exercise bounded
synthetic observation/mapping construction, factor/preflight behavior,
solver-independent execution records, one bounded NumPy adapter, internal run
publication/replay, and isolated post-fit held-out assessment.
They are not a new evidence track and do not alter the frozen Track 4 or Track 5
records or select product acceptance policy.

Track 3 follows Track 1 semantics, Track 4 evidence needs, and Track 5 observed
source behavior. It does not own their contracts or artifacts. Track 5 consumes
Track 2 authorization references rather than duplicating access, licensing,
credential, or data-governance registers.

## Dependency Graph

```text
Track 1 generated contract + Track 2 Phase A sandbox preflight
    |
    v
Track 4 generator/evaluator oracle -> nominal generated fit
    |
    v
Track 5 generated CAD extraction -> nominal end-to-end evidence
    |
    v
Adverse generated evidence -> Track 3 generated examples
    |
    v
Later external sources -> later physical validation
    |
    v
Publication planning and extensions
```

The initial path has no dependency on a real artifact, captured scans,
CloudCompare or RealityScan files, independent measurements, metrology evidence,
physical pose policy, or physical-data retention decisions. Those enter Track 2
Phase B only after nominal generated end-to-end evidence exists.

## Program Stages

1. **Generated contract:** freeze the fixture pair, generator truth, observation
   roles, mappings, instantiated factors, per-scenario factor activation,
   validity, gauge, and evidence separation.
2. **Generator oracle:** establish exact evaluator expectations and deterministic
   generated observations before solver output.
3. **Nominal fit:** pass noiseless fixed-pose, axisymmetric free-roll, asymmetric
   full-pose, flat-factor ablation, and deterministic balanced-noise scenarios.
4. **Generated CAD extraction:** creation, bounded feature/body readback,
   experiment-local normalization, and comparison of the v2 matched fixture
   against frozen truth are observed in Agent Sandbox.
5. **Nominal generated-experiment reconciliation:** observed for the verified
    nominal fit and both retained CAD runs without inferring intent or feeding CAD
    evidence back into fitting. This is not physical or product validation.
6. **Adverse evidence:** exercise coherent held-out strips/sectors; adequate,
   uneven, and inadequate coverage; outliers; corrupted mapping; active bounds;
   invalid geometry; and model mismatch.
7. **Examples:** hand-author non-normative generated-evidence records with a
   synthetic-fixture disposition.
8. **Later external sources:** perform deferred CloudCompare/RealityScan and other
   authorized source probes under Track 2 Phase B.
9. **Later physical validation:** select an artifact and protocol, acquire scans,
   reserve independent measurements, and assess only bounded physical claims.
10. **Publication planning and extensions:** separately consider effect-free
    planning, execution/reconciliation, cones, blends, assemblies, native
    relationships, challengers, and broader adapters.

## Shared Gates

- **Phase A access gate:** designated sandbox scope, Onshape auth/read capability,
  disposable-fixture authorization, evidence disposition, source pin, and secret
  handling are documented.
- **Model gate:** topology, bounded domains, units, frames, parameter roles,
  relationships, gauges, mappings, and factors are explicit.
- **Mathematical gate:** hard relationships hold structurally, Jacobians pass
  declared checks, and invalid geometry differs from non-convergence.
- **Identifiability gate:** expected null directions, rank/conditioning, bounds,
  and coverage are reported using declared scaling.
- **Generator gate:** exact evaluator expectations, deterministic seeds or
  constructions, truth, generated observations, and expected equivalence classes
  are fixed independently of solver output.
- **Observation gate:** IDs, memberships, mappings, factors, per-scenario active
  factor IDs, train/held-out roles, attributes, units, and frames are explicit;
  held-out IDs reach no fit factor.
- **CAD gate:** one explicit pinned source context covers both generated variants;
  raw geometry and normalization are bounded, traceable, and loss-accounted.
- **Validation gate:** generator truth, generated training, generated held-out,
  extracted CAD, later captured observations, CAD nominal, and independent
  physical references remain separate.
- **Audit gate:** immutable inputs, hashes, versions, settings, raw/robust
  residual summaries, diagnostics, termination, and acceptance are traceable.
- **Publication gate:** an accepted fit, fresh target preconditions, capability
  match, valid bindings, and a pure plan precede any future executor.
- **Evidence gate:** ambiguity, unsupported behavior, nondeterminism, and manual
  steps are retained rather than silently normalized away.

## Current User Decisions

Initial work needs only confirmation of the generated contract, Track 2 Phase A
public evidence-retention and cleanup disposition, and authorization to create and
later clean up the matched public generated fixture pair inside the designated
Agent Sandbox under one explicit pinned source context. Cleanup cannot retract
public evidence already copied. Physical-artifact selection,
captured/physical/third-party data access and retention, measurement equipment,
independent-reference custody, and physical pose policy are deferred and do not
block initial work.

No decision on a production solver, language, schema, durable bulk format,
supported adapter, assembly, native constraint, blend, publication executor, or
support commitment is required now.

## Quick Bounded Checks

- verify Agent Sandbox visibility and ID, account/tenant context, auth/API
  eligibility, disposable-document scope, and immutable revision access
- verify exact evaluator outputs against deterministic generator truth
- verify explicitly active asymmetric flat factors remove the expected axial-roll
  null direction
- verify flat-factor ablation restores that null without changing available
  observations, mappings, or memberships
- probe bounds for positive dimensions, station ordering, adjacency, and nonempty
  topology
- verify held-out IDs occur in no fit factor or active-factor selection
- verify generated CAD extraction matches generator-defined cylinders and planes
  without inferring model intent

## Organization Fact

Track 2 records the [Onshape organization facts](access-preflight.md#onshape-organization-facts)
and exact identifiers for the coordination folder and designated Agent Sandbox.
Those facts are not access, entitlement, sample, integration-support, export, or
publication evidence.

## Prohibited Claims

Do not claim:

- that a real fixture has been selected or is needed for the generated path
- sample, document, dependency, API, export, or publication access from folder
  creation
- product support for Onshape, CloudCompare, RealityScan, or another source
- automatic correspondence, topology recognition, or permanent source identity
- full pose observability from axisymmetric geometry
- exact constraints from CAD solved placement or tolerance-based CAD solving
- physical accuracy from CAD nominal values, synthetic truth, generated or scan
  residuals, extracted agreement, or one artifact
- fit acceptance from solver termination alone
- a unique cause from residual patterns; use evidence-qualified language
- stable schemas, APIs, storage formats, compatibility contracts, or a production
  language/solver
- publication success without separately approved execution, fresh read-back,
  and semantic reconciliation
- assembly, native-constraint, blend, or multi-component capability before its
  later fixture passes
