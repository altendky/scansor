# Architecture and Integrations

## Status

**Current and provisional.** Keeping the authoritative fit local and heavy GUIs
external are current product boundaries. Canonical-flow mechanics, adapter
contracts, snapshots, and integration choices remain provisional concepts to
test, not implemented or committed interfaces.

## Canonical Flow

External tools own the heavy graphical workflows. Adapters should convert their
data into application-owned canonical inputs:

1. An observation adapter supplies observations, stable identities, and
   memberships.
2. A model-authoring adapter supplies a supported model schema, source bindings,
   relationships, and parameter mapping.
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
held-out evaluation. Broader combined adverse evidence and any reusable
implementation remain open. Synthetic evidence
can support implementation, formulation, and diagnostic correctness under
constructed scenarios, never physical accuracy, metrology suitability, or
product support.
The nominal flow includes no assembly, native constraint, blend, publication
executor, durable schema/format, or support commitment.

The canonical model, mapping, audit, and result control records are expected to
be JSON-compatible, but their schemas and versioning rules are **open**. Large
numeric observations need not be JSON-embedded: control records may reference
immutable content-hashed bulk artifacts. No durable bulk format is selected.

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
memberships, explicit units, coordinate frames and transforms, optional normals
and attributes, provenance, and source handles where available. Incoming modes
may include open files, project exports, sidecars, plugins/exporters, and direct
APIs when useful. PLY, LAS, and similar formats remain adapter formats rather
than automatically becoming canonical storage.

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
