# CAD Constraint and Geometry Extraction

## Status

**Research finding with observed evidence, updated 2026-07-27.** Official
platform documentation supports bounded extraction paths for native assembly
relationships and evaluated geometry. Preliminary v1 authoring/readback and one
bounded v2 CAD-to-frozen-truth comparison are observed, but the full Track 5
cross-run reproducibility gate, a Scansor adapter, and complete validation remain
open. Broader API availability, licensing, document state, and platform behavior
require direct validation.

Sections labeled **Provisional design implication** describe Scansor directions
derived from the research. They are not source-confirmed platform guarantees,
implemented interfaces, or product-support commitments.

## Evidence Labels

- **Source-confirmed** means representative official documentation exposes or
  states the capability.
- **Provisional design implication** means a Scansor design direction still
  requiring a controlled fixture.
- **Open evidence** means the available sources do not settle behavior needed by
  Scansor.

## Initial Generated Geometry-Only Fixture

**Observed research evidence and provisional design implication.** A public
generated geometry-only fixture from the [integrated planning
program](planning/index.md#common-provisional-generated-fixture-pair) was created
and read back inside Onshape `Agent Sandbox`, folder ID
`b788af3dad6250b9ed521e6a`. The [dated evidence
report](experiments/generated-onshape-fixture.md) records the exact document,
elements, source microversions, features, sketch constraints, solids, faces,
cylinders, datum flat, and orientation convention.

All dimensions are fixture-local and provisional:

- axis `+Z`
- cylindrical band radius `12 mm` over `z = 0..20 mm`
- cylindrical band radius `18 mm` over `z = 20..50 mm`
- cylindrical band radius `14 mm` over `z = 50..80 mm`
- axial/transverse planes at exposed ends and band transitions
- asymmetric variant with a genuine planar cut at `x = 16 mm` over
  `z = 20..50 mm`, whose outward normal defines `+X`
- matched axisymmetric variant without the cut

Creation, source-microversion consistency, bounded feature/body readback, and
the experiment-local v2 comparison with frozen generator truth passed.
Preserving a durable raw probe envelope, general canonical normalization,
another authorized CAD reproducibility run, solver work, and full end-to-end
reconciliation remain open. Extracted geometry does not infer fit-element
intent, parameter roles, mappings, factors,
held-out roles, or native design intent. Agreement would support bounded
extraction/reconciliation evidence only; it would not establish physical
accuracy or Onshape product support.

No assembly, native relationship, blend, topology-regeneration stress,
publication executor, durable schema/format, or support commitment belongs to
this fixture.

## Required Adapter Snapshot

**Provisional design implication.** A complete adapter snapshot should acquire
constraints and evaluated bounded model geometry from the same explicit source
configuration and revision. Where available, it should retain:

- occurrence hierarchy, source bindings, and occurrence transforms
- B-rep bodies, faces, edges, vertices, and topology incidence
- analytic and NURBS support geometry
- loops, coedges, trims, parameterization, and orientation
- relevant native parameters, expressions, units, datums, and configurations
- native mates, joints, rigid groups, or supported constraints linked to the
  same topology or datum records
- controlled tessellation as derived data rather than authoritative geometry

Source/evaluated geometry, the user-confirmed canonical fitting model, and
derived meshes or samples must remain distinct and traceable. Extracted geometry
does not infer fit-element intent, parameter roles, observation mappings, or the
supported canonical model.

## Platform Comparison

| Platform | Source-confirmed API surface | Provisional Scansor extraction shape | Material caveats and open evidence |
| --- | --- | --- | --- |
| Onshape | Assembly, Part, tessellation, and Part Studio evaluation REST operations | External pinned snapshot plus optional bounded FeatureScript annotations | Validate dependency closure; internal feature JSON and topology bindings can change |
| Fusion 360 | In-process B-rep, parameter, joint, as-built-joint, and rigid-group APIs | Add-in exports a versioned sidecar | Generic `AssemblyConstraint` remains preview; events are invalidation hints |
| SolidWorks | COM B-rep and native mate object model | Add-in, macro, or standalone Windows automation against installed SolidWorks | Document Manager's role beyond preflight is open; configuration, loading, installation, and licensing affect completeness |
| FreeCAD | Python `TopoShape`/OCCT and built-in Assembly records | Exporter runs inside FreeCAD or `FreeCADCmd` | Add-ons, recomputation, links, GUI assumptions, and topological naming require validation |
| Neutral STEP exchange | STEP/XDE geometry, product hierarchy, occurrence, and placement readers | Pinned writer/reader pair inventories preservation and loss | Ordinary exchange is non-authoritative for native relationships; AP242 kinematics is specialized |

## Onshape

**Source-confirmed.** Onshape's external Assembly REST APIs expose assembly
definitions, occurrence transforms, configurations, source document/element and
microversion references, mate features, and mate connectors. Native assembly
mates therefore do not require FeatureScript ([Assemblies][onshape-assemblies]).
Onshape documents each edit as a microversion and states that versions and
microversions are immutable ([Architecture][onshape-architecture]).

**Provisional design implication.** A top-level microversion alone should not be
treated as a complete dependency closure. The adapter should retain and validate
referenced documents, elements, configurations, and their pinned versions or
microversions.

**Source-confirmed.** Onshape exposes direct operations for [body
details][onshape-body-details], [face tessellation][onshape-face-tessellation],
[edge tessellation][onshape-edge-tessellation], and Part Studio
[`evalFeatureScript`][onshape-eval-feature-script]. Onshape describes
tessellation as an on-demand polygonal approximation rather than persisted
authoritative geometry and documents topology-ID change across model changes
([Architecture][onshape-architecture]).

**Provisional design implication.** Custom FeatureScript remains useful for
Scansor fit-role selections, annotations, application keys, and evaluator gaps,
but those annotations still need topology-change and ambiguity checks.

**Source-confirmed.** The feature APIs expose a native internal representation
rather than a stable cross-platform constraint schema; encoded defaults can be
omitted and details may evolve ([Features][onshape-features]).

**Provisional design implication.** Preserve raw native feature JSON with source
identity and API context before normalizing the supported subset. Treat webhooks
as invalidation hints followed by a pinned snapshot and application-owned diff,
not as authoritative full model deltas.

## Fusion 360

**Source-confirmed.** Fusion supports in-process Python and C++ add-ins.
Occurrence-context APIs expose B-rep proxies and transforms. B-rep faces expose
assembly context, edges, loops, underlying surface geometry, orientation, and a
boundary-aware evaluator ([BRepFace][fusion-brep-face]).

**Open evidence.** Complete parameter, expression, and externally referenced
design traversal still requires a fixture.

**Provisional design implication.** An add-in can export a versioned Scansor
sidecar keyed to Fusion data/version identity; this is a Scansor envelope, not a
Fusion-native sidecar contract.

**Source-confirmed.** Released component and relationship APIs expose joints,
as-built joints, rigid groups, joint origins and geometry, motion definitions,
limits, offsets, suppression, and involved occurrences
([Component][fusion-component]). The generic `AssemblyConstraint` API remains
marked preview, and Autodesk warns against delivering programs that depend on
preview APIs; it is an experiment, not a distributable dependency
([AssemblyConstraint][fusion-assembly-constraint]).

**Provisional design implication.** Fusion events and revision identifiers may
trigger snapshot/diff work but should not be treated as an exhaustive semantic
change stream.

## SolidWorks

**Source-confirmed.** The installed SolidWorks COM object model includes `IBody2`
for body-level B-rep access ([IBody2][solidworks-body]), `IMate2` for native mate
records ([IMate2][solidworks-mate]), and `IMateEntity2` for mate-operand entity
and component references ([IMateEntity2][solidworks-mate-entity]).

**Provisional design implication.** Preserve native mate kind, values,
alignment, suppression/error state, and operand references rather than reducing
every advanced or mechanical mate to generic geometry. Use an add-in, macro, or
standalone Windows automation process against installed SolidWorks for the
combined snapshot.

**Open evidence.** Document Manager is a separate API for file metadata and
preflight, but no direct official evidence reviewed here establishes it as a
replacement for the evaluated B-rep and `IMate2` object model
([Document Manager][solidworks-document-manager]). Configuration and rebuild
state, referenced component configurations, lightweight or partial loading,
SpeedPak, installation, and licensing effects on snapshot completeness also
require direct validation.

## FreeCAD

**Source-confirmed.** `Part.TopoShape` wraps OCCT topology and geometry, including
solids, shells, faces, edges, vertices, placements, and B-rep serialization
([TopoShape][freecad-toposhape]). The built-in Assembly workbench represents
joints with typed properties, linked subelements, connector placements, offsets,
limits, and values ([JointObject][freecad-joint]).

**Provisional design implication.** Run the exporter inside FreeCAD or
`FreeCADCmd` so FreeCAD loads document types, dependencies, Python proxies, and
recomputed state before one controlled traversal.

**Open evidence.** The built-in Assembly workbench and selected add-ons need
explicit adapters; there is no single native constraint schema across Assembly,
Assembly3, Assembly4, A2plus, and other add-ons. External ZIP/XML parsing of
FCStd may help inventory or preflight, but authoritative restoration can depend
on compiled types, Python proxies, add-ons, external links, and recomputation.
`FreeCADCmd` compatibility must also be tested for adapters that assume GUI
facilities. Topological naming mitigations are useful but do not justify
permanent face or edge identity across every topology-changing edit
([Assembly source][freecad-assembly]).

## Neutral Exchange

**Source-confirmed.** Ordinary STEP translation can preserve geometry,
product hierarchy, occurrence references, and placements
([OCCT STEP][occt-step], [OCCT XDE][occt-xde]). AP242 includes specialized
kinematics scope ([ISO 10303-242][iso-ap242]).

**Provisional design implication.** Treat ordinary neutral exchange as
non-authoritative for native constraints. A solved occurrence transform does not
establish the relationship semantics or remaining degrees of freedom that
produced it.

**Open evidence.** Useful AP242 kinematics exchange depends on the selected
writer and reader implementing compatible entities and semantics; validate it
per pinned product pair.

## Provisional Design Implications

### Portable Plans and Executors

Scansor should define product-neutral, pure import and publication plans, then
keep authentication, SDK objects, processes, network calls, and transactions in
thin platform executors. Incoming sources may be open documents, project
exports, sidecars, plugins/exporters, or direct APIs when the capability and
deployment tradeoff justifies them.

Every adapter should negotiate capabilities explicitly. Unsupported native
relationships, unavailable geometry, stale bindings, and lossy publication must
be rejected or reported for explicit user resolution; semantic degradation is
never silent.

### Identity and Snapshot Envelope

The [current product requirements](principles-and-requirements.md#results-are-auditable)
make Scansor authoritative for canonical identities, treat platform IDs and
topology references as revision-scoped bindings and provenance, and keep source
snapshots, canonical models, and derived data distinct and traceable.

**Provisional design implication.** A common vendor-local extractor should emit
an immutable, versioned raw snapshot or sidecar envelope containing source
revision/configuration, extractor version, units, frames, raw native records,
geometry, diagnostics, and content hashes. Normalize the supported subset
separately so source evidence survives schema and adapter changes.

Native events, webhooks, and revision counters are invalidation hints. After an
event, acquire a new immutable snapshot and compute an application-owned diff.
Do not mutate a canonical model directly from event payloads.

### Native Assembly Relationships

Native mates, joints, and assembly constraints are a useful alternative authoring
surface for a bounded subset of pose, kinematic, and exact relationships. They
complement rather than replace Scansor annotations or manifests for fit-element
intent, parameter roles, observation mappings, and unsupported semantics.

Whitelist relation kinds and parameterizations demonstrated by fixtures. Reject
ambiguity rather than guessing, preserve each platform's native decomposition in
the raw snapshot, and compile accepted hard relationships structurally into
shared/reduced parameters or explicit dependencies. Do not rely on CAD solver
tolerances or the currently solved placement as the exact fitting constraint.
Native authoring may reduce custom UI or feature work, but extraction,
annotations, identity repair, and publication can still require platform code.

Onshape with RealityScan or CloudCompare may be useful first representative
integrations. They are not native architecture assumptions or product-support
commitments. The same contracts should remain testable against Fusion 360,
FreeCAD, SolidWorks, other CAD systems, and broader scan/point-cloud sources.

## Later Broad Cross-Platform Fixture

After nominal generated end-to-end evidence and separate approval, build
equivalent small assemblies where each platform permits, containing:

- repeated and nested occurrences
- analytic and trimmed faces, loops/coedges, orientation, and datums
- mate/joint offsets, limits, suppression, and configuration variants
- fit-role annotations and explicit observation mappings
- unsupported and deliberately ambiguous native relationships
- topology regeneration with face/edge persistence, split, merge, and deletion
- extraction, raw-envelope retention, normalization, and reimport/publication
- one pinned neutral-format writer/reader pair using the same source assembly

Compare extracted relationships and evaluated geometry with the CAD UI and
documented evaluators, not merely current placement or mesh appearance. Verify
dependency capture, units/frames, source binding repair, explicit degradation,
deterministic content hashes, and application-owned diffs. Record platform/API
versions, licensing and loading state, unsupported cases, and all manual steps.
No platform should be described as supported from this fixture alone.

For the neutral pair, inventory preserved and lost geometry, topology, product
hierarchy, occurrences, placements, and native relationship semantics. Compare
the neutral result with the native snapshot; do not infer constraints from final
placement or mesh appearance.

## Open Evidence

- Which smallest native relation subset maps unambiguously to the first Scansor
  model family?
- Which topology signatures help users repair revision-scoped bindings without
  pretending to create permanent source topology IDs?
- Which platforms can provide complete snapshots headlessly, and which require
  interactive or licensed desktop sessions?
- Which publication operations are transactional, idempotent, or reconcilable?
- Which AP242 product pairs, if any, exchange usable kinematics for a bounded
  Scansor fixture?

[freecad-assembly]: https://github.com/FreeCAD/FreeCAD/tree/ae325b7f005e3f437d7095e8ca0622164798997f/src/Mod/Assembly
[freecad-joint]: https://github.com/FreeCAD/FreeCAD/blob/ae325b7f005e3f437d7095e8ca0622164798997f/src/Mod/Assembly/JointObject.py
[freecad-toposhape]: https://github.com/FreeCAD/FreeCAD/blob/ae325b7f005e3f437d7095e8ca0622164798997f/src/Mod/Part/App/TopoShape.pyi
[fusion-assembly-constraint]: https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/AssemblyConstraint.htm
[fusion-brep-face]: https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/BRepFace.htm
[fusion-component]: https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/Component.htm
[iso-ap242]: https://www.iso.org/standard/66654.html
[occt-step]: https://dev.opencascade.org/doc/overview/html/occt_user_guides__step.html
[occt-xde]: https://dev.opencascade.org/doc/overview/html/occt_user_guides__xde.html
[onshape-architecture]: https://onshape-public.github.io/docs/api-intro/architecture/
[onshape-assemblies]: https://onshape-public.github.io/docs/api-adv/assemblies/
[onshape-body-details]: https://cad.onshape.com/glassworks/explorer/#/Part/getBodyDetails
[onshape-edge-tessellation]: https://cad.onshape.com/glassworks/explorer/#/Part/getTessellatedEdges
[onshape-eval-feature-script]: https://cad.onshape.com/glassworks/explorer/#/PartStudio/evalFeatureScript
[onshape-face-tessellation]: https://cad.onshape.com/glassworks/explorer/#/Part/getTessellatedFaces
[onshape-features]: https://onshape-public.github.io/docs/api-adv/featureaccess/
[solidworks-body]: https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IBody2.html
[solidworks-document-manager]: https://help.solidworks.com/2025/English/api/swdocmgrapi/GettingStarted-swdocmgrapi.html
[solidworks-mate-entity]: https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IMateEntity2.html
[solidworks-mate]: https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IMate2.html
