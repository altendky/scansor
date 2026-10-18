# Volume-oriented selections

**Agreed direction, 2026-10-18.** Keep initial node painting and add reusable
volumes downstream. The intended workflow transfers selections between already
aligned scans without repainting. Alignment is a separate workflow; membership
transfer does not establish physical accuracy or correspondence between vertex
indices.

## Ownership and fit inputs

A Selection is a group. Creating it inherently creates its first scan-specific
entry containing the painted node membership. Each group has exactly one entry
per scan. Entries own zero or more explicitly created volume definitions; adding
a Selection does not automatically add a volume.

A Fit remains one top-level feature with one shared definition and no visible
per-scan sub-items. It references Selection groups and automatically resolves
their entries for the active scan. Neither the scan nor the Fit has a separate
entry-choice setting. Fit results belong to their evaluation scan; simultaneous
fitting across scans is deferred.

Loading a replacement scan automatically creates its entry under each Selection.
The entry requires an explicit choice of one source volume before generating
nodes, even when there is only one candidate. Its needs-input state is presented
with a question-mark indicator and **Choose source volume** action. Dependent
fits wait for setup instead of reporting a numerical fitting failure or showing
old geometry as current.

Generated entries retain a live link to the chosen volume and regenerate when it
changes. They are generated-only for now: painting additions/removals onto a
generated entry and directly combining multiple volumes are deferred.

## Volume construction and controls

Both mesh-derived and fitted analytic constructions produce the same reusable
volume/membership capability. Analytic construction initially supports planes,
cylinders, and cones. Multiple definitions may belong to one entry, but conversion
uses one chosen definition at a time.

Construction must preserve the painted footprint, including holes and disconnected
patches. Min/max rectangles, angular bands, and convex hulls cannot satisfy that
contract. The discrete footprint is constructed from mesh support around selected
vertices; a fitted construction projects that support onto the analytic surface.
Mesh-derived construction follows the observed mesh surface directly.

These four controls use automatic defaults with independent manual overrides:

- inward thickness;
- outward thickness;
- padding around patch edges;
- surface-orientation tolerance.

Show the resolved automatic values and allow a manual value to return to Auto.
Thickness is measured relative to the source surface's oriented normal, including
inward-facing surfaces. Filtering by orientation excludes differently facing
surfaces even when they lie inside the spatial volume.

Preserving the footprint before padding does not guarantee that every padded
volume has the original topology: enough padding can close a small hole or join
separate patches. The definition and generated membership must be inspectable.
Automatic defaults need a deterministic, versioned, scale-aware algorithm with
fixture evidence; physical accuracy is not inferred from that algorithm.

## Authoring, replacement, and updates

Edits to the original authoring selection or fit automatically update its volume.
Replacing scan data preserves the original entry, painted membership, and accepted
volume geometry. It must not silently redefine that volume from the replacement
scan's fit.

Adopting geometry from another scan is explicit. Offer a clear **Create new
volume** versus **Replace existing volume** choice. Retain the accepted geometry,
source identity, and authoring provenance independently of whether the authoring
scan's buffers remain loaded.

The initial implementation keeps one active scan's buffers. Switching among
multiple retained loaded scans, simultaneous fitting, and registration remain
future work. A simplified scan, full scan, or rescan may provide separate fit
results using the shared definition.

Partial target coverage produces a warning and permits fitting when existing
validity and ambiguity checks pass. Coverage must describe supported source
surface area, not a ratio of selected vertex counts: a change in tessellation or
scan density alone must not appear as lost coverage. Empty/insufficient membership
does not bypass fit validity requirements.

## Evaluation boundaries

Tree containment is distinct from evaluation dependencies. A Selection group
must not depend on all its entries merely because it owns them. In particular,
the original entry's authoring fit context is fixed to that scan; it cannot use
the shared Fit's active-scan result to rebuild a volume that selects observations
for that very result.

The intended evaluation direction is original painted membership, source-context
fit where needed, accepted volume, generated target membership, target-context
fit. Preserve that direction when authoring inputs change. Reject genuine cycles
and stale results; do not silently fall back to a different entry or volume.

Recomputing an original constrained fit requires its observations. If a change
edits that volume's captured authoring inputs while the original source is
unavailable, retain the accepted geometry but mark it as requiring refresh from
the authoring source. Do not regenerate target observations from a stale
definition or substitute the active target's fit. Loading the exact authoring
source permits automatic refresh. A target entry added during replacement must
not itself count as an edit to the original authoring inputs.

Saved actions need exact source-byte and interpretation identities. Original
vertex IDs remain meaningful only against that source. Transactional replacement
and reopen must preserve authored work on failure, cancellation, stale acceptance,
and missing source references across native and worker adapters.

## Implementation slices

### Shared geometry foundation

The versioned `scansor-selection-volume-v2` kernel in
`experiments/selection_volume.py` captures the barycentric dual cells of selected
vertices in usable incident mesh triangles. Each selected corner contributes its
portion up to edge midpoints and the triangle centroid. This gives boundary
vertices finite support without filling holes or requiring every corner of an
incident triangle to be selected.

Mesh construction retains these cells and interpolated observed normals.
Analytic construction projects them onto the fitted surface and represents their
footprints with triangles in a developable plane/cylinder/cone chart. Angular
seams are handled per cell. This is a discrete footprint definition; it does not
claim an exact curved-boundary CAD offset. Mesh membership is a union of local
surface slabs, including at sharp folds, rather than a watertight Boolean solid.

The initial `source-spacing-v1` defaults use the median selected incident-edge
length as spacing. Each thickness is the corresponding oriented source residual
envelope plus one quarter of that spacing. Automatic edge padding is zero to
retain the discrete footprint. Automatic orientation tolerance is the maximum
source deviation plus five degrees, bounded to five through ninety degrees.
The saved record includes nullable overrides, the source-derived automatic
defaults, resolved values, algorithm version, accepted support geometry, and
provenance. Retaining the defaults permits returning a control to Auto after
unloading the authoring scan. These defaults are provisional
engineering choices with generated-fixture evidence, not physical tolerances.

Membership queries use a bounding-box hierarchy with bounded leaves and chunked
target observations. Construction rejects more than 25,000 selected vertices or
600,000 saved support triangles. These caps bound admitted representation size;
they are not a measured whole-browser memory guarantee.

Coverage uses three fixed area-weighted quadrature samples per saved triangle
against usable target triangles when the support fits within the sample budget.
Larger support uses deterministic strata of source area, with a default budget of
4,096 samples. The diagnostic records the strategy and sample count. Small missing
patches can be missed by the bounded estimate; it is an approximate warning
diagnostic, not an exact area intersection or an independent fit acceptance rule.
The numerical runtime probe exercises an annular mesh/plane selection, density
changes, serialization/replay, and normal-side rejection in native Python and
workers.

### Portable accepted geometry

The `scansor-volume-archive-v1` record stores one validated v2 volume as
zlib-compressed canonical JSON encoded with base64. Its content digest identifies
the uncompressed canonical geometry, independently of compressor versions.
Ordinary record validation and snapshots do not decompress it. Explicit adoption
checks the encoded size, exact expanded length, digest, canonical JSON, and volume
geometry; it rejects extra compressed streams and trailing data.

The shared actions profile admits up to 32 MiB of actions JSON, 16 MiB of encoded
data per volume, and 128 MiB of expanded geometry per volume. Graph acceptance
also reserves the normalized saved file's formatting, so generated children
cannot silently make accepted work too large to reopen. Recipe-bearing requests
have a separate 1,000,000-byte controls allowance; other controls and scan-buffer
admission limits remain independent. These are admission bounds, not a measured
browser memory guarantee or a promise that every maximum-size selection fits.

Conditional graph polling includes the known recipe token. Evaluation-only
revisions can omit that recipe while publishing new states and results. Both
transports retain the exact matching definition; a different token requires the
complete recipe. Initial load, explicit save/export, and worker recovery retain
the full portable actions. Older worker protocols keep their original polling
requests.

### Selection entries and evaluation

Nested Selection records retain the existing feature ID and contain source-bound
entries. A painted entry owns its vertex IDs and depth; a generated entry owns a
reference to exactly one accepted volume. The source binding includes both byte
identity and interpretation, including the captured vertex count. Display names
do not select an entry. Legacy flat selections remain readable and editable;
conversion is explicit, while newly created browser selections have a first
painted entry when the backend advertises the entry capability.

Owned volumes store their authoring construction, optional fitted-surface
reference, controls, accepted archive, and an authoring fingerprint. The
fingerprint covers the actual source-context solve closure, including connected
constraints, while excluding labels, owned caches, and unrelated scan entries.
Authoring uses the ordinary evaluator with that scan's observations and complete
constraint context. It does not independently seed a hidden unconstrained fit.

Explicitly adopting a generated scan entry captures its generated observation
membership for volume authoring. Other generated inputs in the same solve
closure are captured too. These immutable authoring observations break a feedback
loop through the shared active-scan Fit; ordinary painted authoring inputs remain
live. Generated entries themselves remain linked to the chosen accepted volume.

Evaluation refreshes changed volumes only when their exact authoring scan is
loaded. It stages updated archives and matching results privately, then publishes
both behind the current recipe/operation fence. Cancellation or an intervening
edit discards that publication. A refresh can change the recipe token; its
completion carries the originating token so native and worker callers can follow
that specific publication. Ordinary edits do not carry this receipt.

Missing configuration and unavailable or failed authoring refreshes settle as
`needs_input`, remove stale dependent geometry, and retain an actionable diagnostic.
A failed refresh does not automatically repeat until its authoring inputs change.
Cancel recovery preserves this evidence. An otherwise valid refreshed volume can
still produce an ordinary target fit failure under the existing fitting rules.
Unavailable selections outside a requested solve do not block that solve.

### Delivery slices

- [Shared volume geometry foundation](https://github.com/altendky/scansor/issues/332):
  versioned mesh/analytic support, automatic defaults and independent overrides,
  membership, serialization, coverage, and numerical runtime evidence.
- [Portable volume archives](https://github.com/altendky/scansor/issues/337):
  bounded archive adoption, shared actions admission, and recipe-preserving
  evaluation polling.
- [Selection groups and scan entries](https://github.com/altendky/scansor/issues/333):
  ownership, source-context provenance, active-scan resolution, needs-input state,
  and old-recipe behavior.
- [Transactional scan replacement](https://github.com/altendky/scansor/issues/334):
  retaining definitions, explicit conversion, invalidation, native/worker parity,
  and source-aware reopen.
- [Browser authoring workflow](https://github.com/altendky/scansor/issues/335):
  tree/editor/preview controls and the complete replacement-to-fit workflow.

The existing Region and Feature reuse operations retain their v1 envelope
semantics. They are not evidence of footprint-preserving cross-scan transfer.
The broader [multiple-scan discussion](https://github.com/altendky/scansor/issues/237)
and [source replacement/settings discussion](https://github.com/altendky/scansor/issues/248)
also contain work outside this bounded design.
