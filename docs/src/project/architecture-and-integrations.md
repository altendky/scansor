# Architecture and Integrations

## Status

**Current and provisional.** Scansor remains documentation-first and concept-stage.
Keeping the authoritative fit local and heavy GUIs external are current product
boundaries. Product-level canonical-flow mechanics, adapter contracts, snapshots,
and integration choices remain provisional, not committed interfaces.

The [nozzle browser selection experiment](../../../experiments/browser_viewer/README.md)
provisionally explores a Scansor-owned selection/inspection frontend alongside
those external tools. Its local Python fitting and source-bound session records
remain independent of browser code. Browser-first is an experiment sequence, not
a browser-only product commitment; a native frontend and broader GUI boundaries
remain open.

## Canonical Flow

External tools own the heavy graphical workflows. Adapters should convert their
data into application-owned canonical inputs:

1. An observation adapter supplies observations, stable identities, and
   memberships.
2. A model-authoring adapter supplies a strict internal declared analytic model
   plus separate source bindings. The declaration owns supported elements,
   bounded domains, relationships, parameter mapping, policy, and a canonical
   content identity.
3. A small manifest maps observation groups to model elements; some explicit
   mapping remains unavoidable.
4. The local solver consumes the canonical model, canonical observations,
   explicit mappings, instantiated factors, and a separate explicit selection of
   active factor IDs. Memberships remain independent selection/classification
   metadata and do not instantiate or activate mappings or factors.
5. The solver emits auditable, versioned fit-result data.
6. After user acceptance, adapters publish parameters or resulting geometry
   back to CAD and related tools.

The local solver is authoritative. External files and CAD documents are sources
or publication targets, not the sole record of what was fitted.

## Generated Evidence Flow

The initial critical path precedes external-source and physical integration:

1. A deterministic generator defines fixture-local geometry, poses, memberships,
   mappings, factors, held-out roles, and expected evaluator/diagnostic evidence.
2. It produces generated training and coherent held-out observations; generated
   held-out observations create no fit factors.
3. The local evaluator and solver are checked first against the independent
   generator oracle and nominal recovery scenarios.
4. A disposable equivalent Onshape geometry-only fixture is generated in the
   designated Agent Sandbox and read back under an explicit pin.
5. Pure normalization reconciles extracted geometry with generator-defined
   geometry without inferring canonical intent from the CAD.
6. Adverse generated scenarios and non-normative generated examples follow before
   later external-source probes and physical validation.

This flow remains provisional. Its internal generated-observation step and one
bounded generated-CAD comparison have experiment-local evidence; CAD cross-run
reproducibility and a bounded nominal evaluator/solver gate now also have retained
experiment-local evidence. A separate bounded reconciliation now verifies the
solver evidence first, freezes geometry derived only from its nominal seven-shape
estimate, and compares it independently with both replay-verified CAD runs. CAD is
post-fit source evidence in that experiment and has no path into fitting or
held-out evaluation. Broader combined adverse evidence remains open.

A separate provisional internal, synthetic-only, fixed-pose
[declared analytic model workflow](planning/declared-analytic-model-generated-workflow.md)
now implements shared declaration-driven generation, mapping, factors, preflight,
bounded NumPy execution, held-out assessment, internal artifact publication,
replay, and read-only verification for the asymmetric stepped comparator and
coaxial tube. Observation-to-model pose is declared input; only supported shape
parameters are fitted. Its oriented planes, coaxial cylinders, and bounded domains
provide two-topology implementation evidence, not a general geometry solution.

Synthetic evidence can support implementation, formulation, and diagnostic
correctness under constructed scenarios, never physical accuracy, metrology
suitability, production readiness, or product support. The shared workflow adds no
arbitrary-cloud admission, pose discovery, joint pose-and-shape fitting, or CAD
integration. The nominal flow includes no assembly, native constraint, blend, CAD
publication executor, durable schema/format, or support commitment.

The provisional internal
[declared analytic model contract](planning/declared-analytic-model-contract.md)
supplies the semantics and content identity consumed by this shared fixed-pose
implementation. It does not settle a public authoring schema, durable
`model.json`, compatibility policy, or adapter protocol. Durable schemas and
versioning rules for model, mapping, audit, and result control records remain
**open**. Large numeric observations need not be JSON-embedded: control records
may reference immutable content-hashed bulk artifacts. No durable bulk format is
selected.

Large deterministic stress inputs should be reconstructed from small versioned
repository recipes rather than retained as large PLY fixtures. Those recipes
should bind the parametric model, sampling allocation and order, seed, noise and
adverse cases, encoding, identity expectations, and exact-byte reproduction.
Generated bulk files and temporary caches remain outside the repository. The
[mesh-ingestion design](planning/full-resolution-mesh-ingestion.md) specifies a
bounded internal recipe, arithmetic, and bulk-column encoding for its later
implementation. It does not change the existing generators' repeatability claims
or select a durable product schema or cache implementation.

Scansor owns canonical identities. Source/platform IDs and topology references
are revision-scoped bindings and provenance. Source snapshots, user-confirmed
canonical fitting models, derived samples or meshes, and publication plans
should remain distinct and traceable.

## Later Observation Selection

**Research finding:** RealityScan can reconstruct and scale a model and can
classify model vertices, then export classification in PLY, XYZ, or LAS.
However, stable overlapping point groups and durable IDs appear limited. A
classification-export round trip must be tested before relying on it.

**Provisional:** After nominal generated end-to-end evidence, CloudCompare may be
a stronger first external observation-selection
adapter because point-cloud selection and grouping are central to its workflow.
This is a hypothesis, not a completed comparison or commitment.

An observation input should provide stable observation IDs, overlapping
memberships, explicit unit status, coordinate frames and transforms, optional normals
and attributes, provenance, and source handles where available. Incoming modes
may include open files, project exports, sidecars, plugins/exporters, and direct
APIs when useful. PLY, LAS, and similar formats remain adapter formats rather
than automatically becoming canonical storage.

A future external-source import should preserve and hash the original bytes,
identify the importer implementation and configuration, and deterministically
produce a canonical observation stream or artifact. Relevant source sidecars
should remain attached as provenance; for a RealityScan PLY probe, this includes
transform or scale information such as `.rsInfo` when present. The
[full-resolution mesh contract](planning/full-resolution-mesh-ingestion.md) now
specifies the first RealityScan-derived PLY profile, unknown physical units,
source bindings, isolated reader, failure/accounting semantics, and CloudCompare
visual audit. Its [library evaluation](planning/full-resolution-mesh-library-evaluation.md)
records the initial storage and generation choices and bounded local probes.
The isolated PLY I/O/profile and deterministic numeric/recipe slices are implemented
with bounded range, extraction, independent rounding-oracle, and platform CI gates.
Complete ingestion, contribution processing and external-source fitting
remain later work; see the contract's implementation evidence and staged gates.
E57 remains a later scanner-data candidate. Neither format becomes canonical
application semantics or supersedes the separate observation-selection hypothesis.

Full-processing import and mapping should inspect every source point, give every
admitted point an explicit weighted contribution, and record an explicit
disposition for every excluded point. An explicitly named quick mode may
downsample, but must remain distinguishable in inputs, results, and claims.
Multi-million-point full processing should be streaming or otherwise
bounded-memory rather than dependent on sample reduction. Chunking must not
change canonical identities, order, admission, weighting, or dispositions.

### Future Contribution and Robust-Refit Policy

**Provisional:** Observation contribution should be a versioned policy input,
not a fixed property of the solver. The mesh-ingestion contract specifies the
initial area-allocation and normalization policy for later implementation.
Separate declared target-element importance may prioritize interfaces over
lower-accuracy cast surfaces. Their composition with area weights remains open.
Import-time mesh weights cannot be reused in fitting without the separately gated
training/held-out isolation and objective-weight semantics.

A later robust workflow may alternate fitting with explicit identification of
localized deviations, policy-driven exclusion or downweighting, and refitting.
Candidate deviations include bumps, scratches, mold flashing, and other local
features that should not redefine nominal geometry. Defect identification should
remain a distinct stage from fitting and from dimensional acceptance.

The canonical observation record should support a per-point, per-iteration
history of disposition, component weights, effective weight, and applicable rule
or threshold identity. Policy configuration, thresholds, convergence and
iteration limits, and implications for model identity should be deterministic,
versioned, and included in provenance and result identity. Held-out observations
must not enter fitting, policy selection, threshold tuning, or defect decisions.
No automatic-defect algorithm, threshold, or metrology claim is selected.

## Future Calibration and Registration Constraints

**Provisional:** Constraints may be imported from upstream tools such as
RealityScan or declared from user-selected parametric geometry. Candidate
constraints include a known diameter or distance establishing scale, a declared
vertical axis, a horizontal plane, and a selected point or origin. Each
constraint should retain its provenance and uncertainty and distinguish exact
hard semantics from explicitly weighted semantics.

Partial constraints may leave pose degrees of freedom unresolved; the system
should report those freedoms rather than silently choose a complete pose. A
dimension used to establish scale is calibration input and cannot also serve as
an independently validated output.

Support should be staged. First derive scale and the constrained portion of pose,
then pass those results as declared inputs to the existing fixed-pose shape
workflow. Joint pose/scale/shape fitting may be considered only as a later,
separately gated extension. Cone geometry is one illustrative future case, not a
currently supported model family.

## CAD Model Authoring

**Provisional:** Native mates, joints, and assembly constraints can provide an
alternative authoring surface for a whitelisted subset of pose, kinematic, and
exact relationships. Preserve their native decomposition, reject ambiguity, and
compile accepted hard relationships structurally rather than relying on CAD
solver tolerances or current solved placement.

Native relationships complement rather than replace Scansor annotations or
manifests for fit-element intent, parameter roles, observation mappings, and
unsupported semantics. Platform integration may still be needed for geometry
and relationship extraction, annotations, identity repair, and publication.

Onshape with RealityScan or CloudCompare may be useful first representative
integrations, not native architecture assumptions or product-support
commitments. Fusion 360, FreeCAD, SolidWorks, other CAD systems, and broader
scan/point-cloud sources must remain possible through the same boundaries. The
[CAD extraction research](cad-constraint-and-geometry-extraction.md) records
source-confirmed platform paths and the required fixture.

## Adapter Boundary

Observation selection, model authoring, optimization, result review, and CAD
publication should remain separable. Future support should permit other tools
at both the observation and CAD/model-authoring ends without changing the
fitting semantics. The exact adapter protocol, deployment boundary, and error
contract remain open.

Prefer product-neutral pure import and publication plans with thin platform
executors around SDK, process, authentication, transport, and transaction
effects. Adapters should negotiate capabilities explicitly; semantic degradation
must never be silent. Native events are invalidation hints followed by an
immutable snapshot and application-owned diff, not authoritative model deltas.
