# Principles and Requirements

## Status

**Current direction.** These principles constrain future design. Detailed
requirements and acceptance thresholds remain to be developed with a validation
corpus.

## User Intent Is Explicit

The product fits a user-defined generative model. It must preserve the declared
topology, element identities, correspondence, parameter roles, bounds, and
relationships rather than silently inventing a different model.

## Hard Constraints Are Exact by Construction

Exact relationships should normally be represented through shared or reduced
parameters and dependency evaluation, not approximated with large penalty
terms. Soft relationships may contribute residual factors. Diagnostic
relationships should measure and report without changing the fit unless the
user explicitly promotes them.

The internal representation naturally has views resembling constraint,
dependency, correspondence, and factor graphs. Those are implementation and
reasoning concepts, not promises of a particular storage format or public API.

## Results Are Auditable

Each run should make the inputs, schema versions, free and derived parameter
values, bounds, residual summaries, memberships, solver termination, warnings,
and publication state inspectable. Accepted CAD output must remain traceable to
the fit-result data that produced it.

Source snapshots, canonical models, derived meshes or samples, and publication
plans should remain distinct and traceable. Scansor owns canonical identities;
external identifiers are revision-scoped bindings and provenance.

## External Observation Processing Is Accountable

A future full-processing mode should inspect every source point. Every admitted
point should contribute through an explicit weight, and every excluded point
should receive an explicit disposition. A separately labeled quick mode may
downsample, but its results must not be represented as full-data processing.
Multi-million-point full processing should use streaming or otherwise
bounded-memory execution rather than requiring sample reduction.

Observation contribution should be governed by an explicit, versioned,
configurable policy rather than a permanent fixed weighting rule. Surface-area
weighting is an acceptable initial baseline for mesh-derived vertices because it
reduces raw triangulation-density bias, but it is not assumed final. Later
policies may also apply declared importance weights by target surface or element,
for example prioritizing interfaces over lower-accuracy cast surfaces. Area and
target-importance weighting may be combined; their exact normalization and
semantics remain open.

The [mesh-ingestion design](planning/full-resolution-mesh-ingestion.md) specifies
an initial area-only policy, accounting, and bounded execution contract for later
implementation. Its import eligibility is distinct from mapping, activation, and
fitting, and does not make external observations synthetic.

Future robust processing may use an auditable iterative fit, localized-deviation
identification, policy-driven exclusion or downweighting, and refit workflow.
Bumps, scratches, mold flashing, and other local deviations may be treated as
candidate defects that should not redefine nominal geometry. Every point should
retain its disposition and weight history across iterations; no point may be
silently deleted. Exclusion and downweight rules, thresholds, convergence and
iteration limits, and model-identity implications should be deterministic,
versioned, and bound into provenance and result identity.

Held-out observations must remain isolated from fitting, policy selection,
threshold tuning, and iterative defect decisions. Defect detection, geometric
fitting, and dimensional acceptance remain distinct evidence and decision roles.
No automatic-defect policy, threshold, or metrology validity is selected.

## Large Generated Inputs Are Recipe-Backed

Large deterministic test inputs should be reproduced from small versioned
recipes committed to the repository, not committed as multi-megabyte PLY
fixtures. A recipe should bind its parametric model, sampling allocation and
order, seed, noise and adverse cases, encoding, identity expectations, and the
information needed to recreate exact sample bytes. Generated large files and
temporary caches should remain outside the repository. The mesh-ingestion design
selects one bounded portable recipe and its verification gates; broader sampling
schemas, generator coverage, and product cache design remain open.

## Semantic Degradation Is Explicit

Adapters should declare capabilities and report unsupported relationships,
unavailable geometry, stale bindings, and lossy publication. They must not
silently reinterpret a native relationship or infer fit intent from current
placement or extracted geometry alone.

## Failure Must Be Legible

Convergence alone is not proof of a useful fit. Diagnostics should address at
least:

- underconstrained or weakly identifiable parameters
- insufficient or uneven observation coverage
- bound-active parameters
- sensitivity to held-out observations
- invalid or degenerate geometry
- residual structure suggesting bad correspondence or an inadequate model

## Synthetic Truth and Physical Truth Are Distinct

Deterministic generator truth and generated observations should establish the
first evaluator, formulation, implementation, recovery, and diagnostic evidence
under constructed scenarios. A disposable generated CAD fixture may test bounded
extraction and end-to-end reconciliation, but extracted agreement is not an
independent truth source. Membership, mapping, factor participation, and held-out
roles remain separate; generated held-out observations create no fit factors.

Synthetic evidence cannot establish physical accuracy, metrology suitability,
external-source behavior not directly observed, or product support. Those claims
require later evidence of the applicable kind.

## Physical Truth Requires Later Validation

Scan agreement is not automatically dimensional truth. Accuracy and metrology
claims require known references, a representative validation corpus, repeatable
procedures, and explicit uncertainty. Until then, results are geometric
estimates with diagnostics, not certified measurements.

A known dimension used to establish observation scale is a calibration input,
not an independent validation result. It must not also be reported as evidence
that the fitted output independently recovered that dimension.

## Product Quality Extends Beyond Optimization

The optimizer is expected to be a minority of the product work. Correspondence
UX, failure and identifiability diagnostics, geometry validity, CAD integration,
the validation corpus, physical truth, packaging, and support are likely to
dominate the effort needed for a dependable product.

## Heavy GUIs Stay External

Scansor should not initially recreate reconstruction, point-cloud editing, or
CAD authoring interfaces. It should integrate with suitable external tools while
owning a canonical, tool-neutral fitting contract and auditable results.
