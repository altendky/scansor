# Track 1: First Trial and Model Family

## Status

**Provisional, snapshot dated 2026-07-30.** The internal provisional
`stepped-rotational-v1` contract, deterministic generated observations, and v2
CAD-to-truth comparison exist. The bounded solver/evaluator gate now has retained
experiment-local recovery, derivative, gauge, balanced-noise, held-out, and
executable outcomes for exactly all `15` frozen scenarios, including the bounded
adverse suite. The execution gate verifies expected pass, rejection, review, and
no-policy paths; it does not assign a passing disposition to every scenario. The
fixed-outlier linear control remains explicitly unclassified because no robust
loss or threshold is approved. Complete nominal end-to-end reconciliation,
additional combined adverse cases, and Track 1 product disposition remain open.
This page defines the model and factor semantics that later tracks must
instantiate without redefining.

## Objective

Establish a solver-independent generated contract for a matched pair of bounded,
single-part stepped rotational fixtures. Deterministic generator truth, generated
observations, and a disposable generated Onshape fixture pair govern the initial
path.
External-source and physical trials are later gates, not initial prerequisites.

## Provisional Model Family

All dimensions are fixture-local and provisional. The core model contains:

- an axis along local `+Z`
- a cylindrical band of radius `12 mm` over `z = 0..20 mm`
- a cylindrical band of radius `18 mm` over `z = 20..50 mm`
- a cylindrical band of radius `14 mm` over `z = 50..80 mm`
- finite axial/transverse planar patches at exposed ends and band transitions
- for the asymmetric variant, a genuine planar cut at `x = 16 mm` over
  `z = 20..50 mm`, with outward normal defining `+X`
- a matched axisymmetric variant without that cut
- one global proper rigid transform from model frame to observation frame, with
  no scale

The model frame uses the rotational axis as local `+Z`, a declared transverse
datum plane as `Z = 0`, and the asymmetric feature to define `+X`; `+Y` completes
a right-handed frame. Track 1 defines transform direction and frame semantics but
does not prescribe Euler angles, quaternions, a solver manifold, or a reduced
coordinate vector.

A cone may follow core success. Fillets, circular-arc profiles, toroidal/spherical
patches, general trims, edge/curve fitting, free-form surfaces, assemblies,
native mates/joints, multiple component poses, a publication executor, and any
durable schema or format are outside the nominal generated path.

## Parameters and Relationships

- **Fixed:** units, topology, element sequence and identities, datum definitions,
  relationship kinds, mapping policy, and values explicitly known for model
  definition rather than validation.
- **Free:** global pose and selected cylinder radii, axial stations, and
  datum-flat offset/depth.
- **Shared:** the rotational axis and radii/stations reused by elements.
- **Derived:** segment lengths, local element frames, trim limits, intersections,
  flat width, and reported dimensions.

Coaxiality, shared stations, ordered axial topology, shared radii, declared
incidence, and datum intersections are hard relationships enforced through
shared/reduced parameters and dependencies. They create no penalty factors.
Soft relationships require an explicit meaning, unit, scale, and provenance; the
baseline should have none absent evidence. Diagnostic relationships may measure
without changing the fit.

## Observation and Factor Semantics

Membership, mapping, and factors are distinct:

- membership records selection/classification and may overlap
- mapping records explicit user-controlled fit intent
- a factor is an explicitly instantiated fitting or diagnostic contribution

Initially each training observation has at most one primary geometric fit factor.
A secondary membership is diagnostic unless an additional factor is explicitly
declared with rationale and weight. Held-out observations create no fit factors
and cannot influence tuning. Edge/seam observations require a declared guard
region or explicit assignment; they must not be silently counted on adjacent
elements.

Each scenario must list its active factor IDs independently of available
observations, memberships, and mappings. Observation presence does not activate a
factor. An ablation changes the explicit active-factor set while retaining the
observations and their independent metadata unless the scenario says otherwise.

The core geometric factor is point-to-oriented-analytic-support distance when
the projected point lies inside the element's admissible interior domain.
Out-of-domain projection is a mapping/domain failure, not an edge clamp. Records
must distinguish raw geometric residuals in declared units, deterministic
scales/weights, robustified contributions, and aggregates. Normals may be retained
for diagnostics; normal factors are deferred unless point-only evidence is
inadequate.

## Observability Cases

The generated asymmetric full-pose scenario explicitly activates geometric
factors for observations on the genuine cut. The paired axisymmetric free-roll
scenario should
report an axial-roll gauge/equivalence class. A flat-factor ablation retains the
asymmetric geometry, observations, memberships, and mappings but removes fit
factors on the flat; it must restore the roll null. Numerical convergence in a
null case is not evidence of full observability.

## Evidence and Leakage

Designate generator truth, generated training observations, generated held-out
observations, extracted CAD geometry, later captured observations, CAD nominal
values, and independent physical references separately. Generated held-out
observations use coherent axial strips and angular sectors, create no fit factors,
and remain unavailable to robust-scale or threshold tuning. Later physical
reference values require the same leakage barrier but are not needed initially.

Generated evidence may support evaluator, implementation, formulation, recovery,
and diagnostic correctness under its constructed scenarios. It cannot support
physical accuracy, metrology suitability, external-platform behavior not directly
observed, or product support.

## Ordered Gates

1. **Generated contract:** freeze the fixture pair, elements, domains, frame/pose
   direction, roles, relationships, mappings, instantiated factors, scenario
   factor activation, validity, and evidence separation.
2. **Generator oracle:** freeze exact evaluator expectations, deterministic
   observation construction, truth, held-out regions, and expected diagnostics
   before solver output.
3. **Nominal fit:** pass the exact evaluator oracle, noiseless fixed-pose recovery,
   axisymmetric free-roll gauge, asymmetric full-pose recovery, flat-factor
   ablation, and deterministic balanced-noise scenarios.
4. **Generated CAD extraction:** require Track 5 to generate and pin the geometry
   in Agent Sandbox, then preserve or explicitly reject extracted geometry without
   inferred intent.
5. **Nominal end-to-end:** reconcile generator-defined geometry, generated
   observations, nominal fit, and extracted CAD geometry.
6. **Adverse evidence:** pass coherent held-out strips/sectors; adequate, uneven,
   and inadequate coverage; outliers; corrupted mapping; active bounds; invalid
   geometry; and model mismatch scenarios.
7. **Examples:** hand generated evidence to Track 3 for non-normative examples and
   a synthetic-fixture disposition.
8. **External-source phase:** later authorize and probe CloudCompare, RealityScan,
   or other captured-data routes without changing the contract silently.
9. **Physical-validation phase:** later select and preregister a real artifact,
   captured observations, physical pose policy, independent measurements,
   leakage controls, review, and bounded acceptance.
10. **Publication planning/extensions:** only after applicable acceptance,
    separately gate effect-free planning, any executor/reconciliation experiment,
    cones, blends, assemblies, and native constraints.

## Acceptance Evidence

The nominal generated path passes only when:

- valid bounds preserve topology and nonempty domains
- invalid states receive specific diagnostics
- exact relationships remain exact throughout admissible coordinates
- every factor traces to an observation, target element, mapping, residual
  definition, and evidence role
- overlaps do not alter the objective without an explicit extra factor
- generated held-out observations have no training path
- the axisymmetric case exposes roll gauge and the datum-flat case supplies
  adequate roll evidence
- deterministic balanced noise, coherent held-out regions, deliberate outliers,
  active bounds, all three coverage classes, corrupted mappings, invalid geometry,
  and model mismatch produce distinguishable evidence
- synthetic-fixture disposition considers training, held-out, identifiability,
  coverage, stability, and validity rather than convergence alone

Success would show only implementation, formulation, and diagnostic correctness
for declared constructed scenarios and one generated CAD extraction path. It
would not establish product support, production readiness, physical or
dimensional accuracy, metrology suitability, automatic recognition, external
source behavior beyond observed evidence, or guaranteed optimization success.

## User Decisions and Quick Checks

Initial work requires no real-artifact, captured/physical/third-party data
retention, measurement-equipment, metrology-evidence, or physical-pose decision.
The public generated-fixture evidence-retention and cleanup/disposition record
remains a Track 2 Phase A gate. Check the fixture-local dimensions,
station/radius bounds, cut domain and normal, guard regions, factor counts versus
membership counts, generated holdout leakage, and the expected gauge change when
flat factors are removed and restored. Physical choices are Track 2 Phase B
inputs after nominal generated end-to-end success.
