# Declared Analytic Model Observation Mapping

## Status and boundary

**Provisional internal implementation design, snapshot dated 2026-09-08.** This
is a compatibility-free successor to the stepped-rotational-v0 mapping format.
It is synthetic-only, non-public, and carries no compatibility, arbitrary-cloud,
physical-validation, metrology, accuracy, or production-support claim.

Operational publication admits only exact project-generated synthetic inputs
that pass the existing read-only provenance replay. Constructed declarations are
used only for focused pure mapping tests until a second generated topology is
owned and exercised by issue #14. Generic PLY, segmentation, pose discovery,
correspondence inference, refinement, and threshold learning remain excluded.

## Model-bound request and artifacts

Each mapping request embeds one complete canonical `ModelDeclaration`, including
its content-addressed `model_id`. The request, all model-frame records and their
identities, the mapping result, and the mapping-run manifest bind that same model
ID. Mapping and manifest formats are
`scansor-declared-analytic-model-mapping-v1` and
`scansor-declared-analytic-model-mapping-manifest-v1`.

Replay reconstructs and revalidates the embedded declaration, verifies every
record binding, recomputes mapping from the referenced canonical observation
array, and requires byte-identical output. Declaration tampering, stale model
IDs, model substitution, and cross-model manifest/result mixing fail closed.

## Deterministic association

Held-out rows are removed before candidate construction. For every training row,
mapping transforms the point by the declared observation-to-model rigid transform
and traverses `declaration.elements` without parsing IDs or assuming an element
count. The shared declared-geometry evaluator supplies primitive distance,
primitive projection, bounded-domain membership, and boundary clearance at the
declaration's nominal parameter vector.

The existing deterministic policy is retained:

1. supports whose projection is outside their bounded domain do not classify;
2. in-domain supports beyond maximum support distance establish an outlier rather
   than a candidate;
3. an in-distance support inside the transition guard makes the row a transition;
4. surviving candidates are ordered by absolute distance and element ID;
5. insufficient separation between the two closest candidates is ambiguous;
6. otherwise the closest candidate supplies one primary geometric mapping.

Transition, ambiguity, gap, and outlier exclusions remain complete, identified
analyses. Any exclusion rejects the mapping while preserving diagnostics.

## Declaration-owned admission

Required-support and coverage minima come only from
`declaration.mapping_admission`; requests cannot override them. Both count only
accepted primary training mappings. Required support is reported in declaration
order. Each ordered coverage cell classifies the mapped element's primitive
projection at nominal parameters through its own declared bounded domain, and
reports its declared cell ID and observed count independently of support and rank.

Rank rows are evaluator parameter Jacobians for accepted primary training
mappings at nominal parameters, in mapping order. Columns follow the policy's
declared rank-parameter order. The dimensionless matrix is
`J * parameter_scales / residual_scale`; descending singular values and rank use
the declared strict relative threshold and required rank. Candidates,
memberships, instantiated factors, active factors, and held-out rows never
contribute support, coverage, or rank counts.

Missing required support, insufficient coverage, and rank deficiency are
separate ordered rejection reasons. Normals remain optional, untrusted
diagnostics and never affect classification or admission.

## Held-out isolation and downstream scope

Held-out coordinates and attributes cannot affect candidates, mappings,
thresholds, support, coverage, rank, tuning, or any diagnostic derived from
training. They receive only identified
`post-fit-evaluation/not-evaluated` records during mapping.

This successor does not generalize factor construction, activation/preflight,
fitting, result acceptance, CAD use, or publication. Those remain separately
sequenced work. Existing stepped synthetic inputs run through their canonical
declarations as regression evidence; replacement mapping IDs and bytes are
intentionally different.
