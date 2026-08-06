# Track 3: Canonical Example Records

## Status

**Provisional, snapshot dated 2026-07-26.** No concrete record shape, schema,
serialization, canonical hash, migration policy, or adapter contract is selected.
Examples must wait for sufficient Track 1 semantics, Track 4 evidence needs, and
Track 5 generated CAD extraction evidence.

## Objective

Produce hand-authored, non-normative pseudo-records that illustrate how approved
concepts relate. Examples follow semantics; they do not define them. Layout,
labels, ordering, punctuation, apparent fields, and example values carry no
compatibility meaning.

## Prerequisites

- **Track 1 semantic gate:** model elements/roles, membership, mapping, factor,
  holdout, gauge, units/frame, relationship, and residual semantics are stable
  enough to instantiate.
- **Track 2 Phase A gate:** Agent Sandbox authorization, auth boundaries, and
  generated-evidence disposition are established.
- **Track 4 evidence gate:** termination, observability, diagnostics, leakage,
  disposition evidence, and audit needs are demonstrated by stabilized fixture
  evidence.
- **Track 5 generated-CAD gate:** require one generated Onshape pin/probe
  envelope, binding, units/frame, provenance, replayable normalization,
  degradation flow, and cross-run reproducibility evidence. One bounded v2
  readback exists, but this full prerequisite remains open.
- **Vocabulary gate:** the integrated planning glossary is used without local
  synonyms or competing lifecycle states.

Before these gates, Track 3 maintains only a coverage checklist. Initial concrete
pseudo-records begin after nominal generated end-to-end evidence and adverse
generated evidence stabilize. Physical acceptance and publication executor
outcome examples wait for their later evidence; neither blocks generated examples.

## Shared Scenario

Use the provisional fixture-local pair defined by Track 1: axis `+Z`; bands of
radius `12 mm` over `z = 0..20 mm`, `18 mm` over `z = 20..50 mm`, and
`14 mm` over `z = 50..80 mm`; axial/transverse planes; and the asymmetric
`x = 16 mm` cut over `z = 20..50 mm` with outward normal `+X`. Include the
matched axisymmetric variant. The examples contain no assembly relationships,
native constraints, blends, executor outcomes, or durable schema/format.

The example bundle should make visible:

- an axisymmetric attempt with explicit axial-roll gauge status
- an asymmetric attempt with datum-flat factors and Track 4 evidence, if
  sufficient, for full local observability
- flat-factor ablation restoring the axial-roll null
- separate solver termination, evidence interpretation, and synthetic-fixture
  disposition without mutating the underlying fit result

## Required Semantic Separation

Examples must show memberships, mappings, and factor participation separately.
Many-to-many overlap creates no factor automatically. Held-out observations have
zero fit-factor participation. Any additional factor requires explicit identity,
target, role, weight, and provenance.

Generator truth, generated training observations, generated held-out
observations, extracted CAD geometry, later captured observations, CAD nominal
values, and independent physical references require separate provenance and
permitted-use statements. Generated held-out observations create no fit factors.
Example construction must make leakage barriers auditable.

Application identities remain distinct from source IDs, revisions, source
bindings, and content hashes. Snapshot/binding/invalidation language must reflect
Track 5 observations rather than invented platform behavior.

## Planned Walkthroughs

1. **Non-normative preface:** identify owning semantics/evidence and reject
   schema interpretation.
2. **Scenario/evidence inventory:** describe the pair and permitted information
   flow among generator truth, generated training, generated holdout, and
   extracted CAD geometry.
3. **Identity/provenance:** distinguish canonical IDs, source probe envelopes,
   revision-scoped bindings, and content-hashed artifact references.
4. **Model intent:** instantiate approved elements, parameters, bounds,
   dependencies, and datum meaning without redefining mathematics.
5. **Observation association:** illustrate memberships, mappings, factors, and
   held-out exclusion independently.
6. **Fit/observability:** illustrate axisymmetric gauge and asymmetric evidence
   using Track 4-owned diagnostics.
7. **Disposition:** show pass/review/fail synthetic-fixture dispositions traceable
   to immutable fit evidence without implying physical acceptance.
8. **Later evidence placeholders:** identify external-source, physical acceptance,
   publication-plan, executor, and reconciliation examples as deferred rather
   than inventing their outcomes.
9. **Artifact references:** use format-neutral immutable references while marking
   byte canonicalization, digest algorithm, media format, and storage unresolved.
10. **Traceability commentary:** identify each owning track and deferred schema,
    migration, serialization, and compatibility question.

## Review Gates

- every modeled/factor concept traces to Track 1
- every result, diagnostic, observability statement, and disposition basis traces
  to Track 4
- every source-envelope/binding/invalidation statement traces to Track 5
- generator-truth values, generated holdout values, and later independent
  references do not enter fit factors
- memberships, mappings, and factors are visibly separate
- pseudo-records do not imply exhaustive fields, required types, closed enums,
  validators, or canonical serialization
- initial lifecycle examples end at synthetic-fixture disposition
- track owners confirm that examples instantiate rather than duplicate outputs

## Remaining User Choices

Later choices include the permitted role of CAD nominal values, which adverse
dispositions to illustrate first, and when external-source, physical acceptance,
publication-plan, executor, or cone examples gain sufficient evidence. Generator
truth must appear in the initial walkthrough. None selects a durable schema or
format.
