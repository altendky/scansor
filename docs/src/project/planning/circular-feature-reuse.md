# Managed circular feature reuse

This bounded experiment extends managed Feature reuse with an explicit circular
placement mode. One authored feature can supply selections, Fits and evaluated
surfaces for other occurrences on the same active scan. Circular placement uses
a supplied axis and count; it performs no repetition discovery, registration or
per-target correspondence painting. The existing arbitrary-location matching
mode remains available separately.

## Inputs and count convention

The shared contract accepts one or more cylinder, cone or plane Fits, an existing
supported axis, and an integer total count from **2 through 32**. A compound source
can include a hole cylinder and its chamfer cone. Every selected Fit input must
have exactly one explicit reference to an accepted volume owned by that source
Selection entry. Missing or ambiguous volume setup requires an authored choice.

Occurrence **0** is the original authored feature. A total count of six creates
five generated occurrences at 60, 120, 180, 240 and 300 degrees. The source
feature supplies the starting phase. There is no separate starting-angle or
partial-sweep setting in this slice.

The placement axis can be an existing Axis or a cylinder/cone Fit's resolved
axis. It must be an upstream graph dependency with a supported evaluation
context. Placement uses the axis's spatial point and directed orientation, so a
translated or tilted axis does not become a rotation about the world origin or
local Z. Invalid references and dependency cycles are rejected. Unavailable axis
evaluation remains an ordinary failure or blocked dependency; another axis or
an independent fit is not substituted.

## Accepted footprint and actual observations

Circular selection uses the accepted
[selection-volume geometry](volume-oriented-selections.md), including its patch
footprint, holes, disconnected support, independent inward/outward thickness,
edge padding and normal-orientation tolerance. It does not reconstruct the older
min/max reuse envelope. Padding can still close a narrow hole or join nearby
patches, as documented for volume construction generally.

Each placement queries actual positive-weight observations from the active scan.
The core inverse-transforms bounded point and normal chunks into the saved
volume's authoring frame. The accepted support is shared rather than copied into
one large geometry payload per occurrence. Transformed normal filtering excludes
an oppositely facing sheet even when it occupies the same spatial region.

Generated memberships contain target scan row IDs resolved by that query. Source
vertex IDs are not rotated or reused as target IDs. Original and reduced scans
therefore require fresh selection and evaluation under their own source binding;
the [active-scan workflow](virtual-scans.md) does not change this rule.

Each generated Fit evaluates its target observations. A transformed source
surface may initialize that calculation, but is not published as a measured
target result. Independent target fitting is the default, permitting bounded
departures from ideal placement. Existing explicit support-authority rules still
apply to generated Fits.

The optional equal-dimension control currently creates visible managed
**equal-radius relationships for corresponding cylinders**. It does not silently
constrain target centers, directions or angular poses to perfect manufactured
symmetry. Circular selection propagation is not limited to the numerical solver's
historical three-member rotational-symmetry factor. This feature adds no general
arbitrary-count exact-pose constraint.

## Ownership and supported lineage

One Feature reuse action owns the generated occurrence children. Stable ownership
keys combine occurrence ordinal and source role, covering each generated input
selection, Fit, supported datum and enclosed relationship. Occurrence groups are
inspectable groupings of those owned roles. Original source features remain
ordinary referenced actions; they are not adopted or duplicated as occurrence 0.

The implementation reuses existing lineage discovery and internal-reference
remapping. Cylinder, cone and plane Fits, supported free/fit-derived axes,
reference planes and the existing supported enclosed reuse relationships can be
copied. A Fit required by that closure must be explicitly included among the
selected source Fits. Earlier occurrence providers and source scan/selection
references retain their graph dependencies and provenance.

This is not an unrestricted graph-cloning operation. Point datums, sphere Fits,
point-pair axes inside the cloned source feature, and unsupported closure
operations are rejected with diagnostics. A point-derived axis used only as the
upstream placement axis is distinct from cloning that construction. CAD Face and
Body actions are outside the cloned feature boundary. Unsupported external
references are rejected rather than silently detached or remapped to another
feature.

## Authoring and reconciliation boundaries

Preview and apply share a request token and allocation seed. Preview reports
placements, explicit volume references, occurrence children and proposed
removals. Applying that same intent preserves preview allocation identity.
Surviving occurrence/role pairs retain their child IDs when count, axis or source
definitions change. Unaffected roles retain their identities when a compound
source is expanded or reduced.

`FeatureGraph.replace_authored` is an explicit authored-edit boundary. It checks
the cursor, reconciles circular owners, validates the resulting graph and
publishes under the same lock. Imports, reopen, recovery and numerical evaluation
use ordinary validation/replacement; they do not invent or reconcile missing
managed children. A saved circular owner with an incomplete child inventory is
rejected.

Source Fit edits that change input roles also need explicit volume mappings for
the new roles. The source definition and mapping updates must form one valid
authored transaction. They cannot be published separately while leaving the
pattern's generated definitions inconsistent. No volume is chosen implicitly to
make such an intermediate edit pass.

Volume edits invalidate the affected placements and Fits. The accepted volume's
authoring fingerprint must be current before it selects target observations.
Refreshing its source context follows the existing volume-authoring rules;
downstream target Fits cannot redefine the volume that selects their own inputs.
Unavailable authoring inputs remain a refresh/setup problem, with no fallback to
the active target geometry.

Shrinking or restructuring a pattern checks references from outside its owned
children. An edit that removes an output used downstream is rejected before
publication. Deletion remains subject to the ordinary graph dependency rules.
Failed, invalid or stale authored transactions preserve the accepted graph.

## Diagnostics and surface scope

An empty occurrence remains an inspectable generated selection with zero rows.
Insufficient observations follow ordinary Fit validity rules: the Fit fails or
blocks, records its error, and does not present an earlier result as current.
The evaluator does not search for a different physical occurrence to hide the
failure.

Selection diagnostics include source and target vertex counts, overlap with the
original source selection, and overlap counts with other ready generated
selections. These are observation counts, **not physical area coverage**.
Different tessellation densities alone can change the counts. The circular core
does not provide a new area-coverage estimate; inspect the placed support and
actual observations when coverage is partial. Existing fit-validity rules still
determine whether those observations support a usable result.

Required outputs are evaluated fitted surfaces, available as ordinary operands
for downstream geometry and CAD workflows. A displayed fitted surface is not a
trimmed CAD Face. This slice does not generate managed Face templates or Bodies.
Existing downstream Face references and geometry-change re-review rules remain
in force.

## Initial bounds and evidence

| Resource | Bound |
| --- | ---: |
| Total circular occurrences, including the source | 2–32 |
| Selected source Fits | 32 |
| Source lineage actions | 99 |
| Explicit Fit-input volume mappings | 99 |
| Recipe actions, checked before generating child IDs | 10,000 |
| One generated selection's membership | 25,000 rows |
| Ready generated memberships per circular owner | 250,000 rows |
| Point/normal query chunk | 1,024 rows |

Existing portable-actions, volume archive, scan and numerical limits also apply.
The action preflight includes the planned generated selections, Fits, datums,
relationships and optional equal-radius actions. Membership limits fail with an
inspectable error before accumulating beyond their budget. These component caps
do not establish a whole-process or browser-memory limit.

The focused core evidence includes five physical cylinder/chamfer occurrences
around a translated, tilted axis, small radius/position departures, a coincident
oppositely facing distractor, independently recovered target radii and transferred
coaxial relationships. It also covers transformed holes/disconnected footprints,
missing and overlapping occurrences, source/volume/axis/count edits, stable
identities, protected removals, optional equal radii, resource admission, saved
inventory validation and regression of arbitrary-location reuse. Native and
packaged-worker UI acceptance are separate integration evidence.

The shared implementations are
[feature reuse authoring](../../../../experiments/feature_reuse_authoring.py),
[graph evaluation](../../../../experiments/feature_graph.py),
[placement geometry](../../../../experiments/feature_reuse.py) and the
[selection-volume kernel](../../../../experiments/selection_volume.py).
