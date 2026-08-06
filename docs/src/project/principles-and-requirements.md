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

## Product Quality Extends Beyond Optimization

The optimizer is expected to be a minority of the product work. Correspondence
UX, failure and identifiability diagnostics, geometry validity, CAD integration,
the validation corpus, physical truth, packaging, and support are likely to
dominate the effort needed for a dependable product.

## Heavy GUIs Stay External

Scansor should not initially recreate reconstruction, point-cloud editing, or
CAD authoring interfaces. It should integrate with suitable external tools while
owning a canonical, tool-neutral fitting contract and auditable results.
