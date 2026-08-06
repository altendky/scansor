# Track 5: Generated CAD and Read-Only Adapters

## Status

**Provisional with observed evidence, snapshot dated 2026-07-30.** The public v1
Onshape fixture is an [observed preliminary authoring and analytic-readback
probe](../experiments/generated-onshape-fixture.md), created before a frozen
deterministic generator contract. It does not pass the generated-CAD gate. No
Scansor adapter, external-source probe, broad degradation result, or publication
plan exists.
The public v2 fixture satisfies this experiment's preregistered authoring and
one bounded CAD-to-frozen-truth readback. A fresh branch from the frozen Start
version produced a second immutable CAD version whose direct analytic body
readback also matched the frozen geometry. After correcting the verifier for the
observed official response arrays, transient IDs, typed FeatureScript values, and
other bounded response shapes, retained MCP-returned JSON response bodies plus
assembler-generated request/response metadata and checksums support offline
normalization showing that each run matches frozen truth and that normalized
geometry is semantically equivalent across runs. Authenticated HTTP transport
envelopes were not retained. The bounded generated-CAD reproducibility gate
passes. A separate read-only [generated-fit reconciliation][reconciliation]
verified the retained solver evidence before opening either CAD run, froze local
geometry from the `noiseless-fixed-pose` estimate, and compared that same
prediction independently with both normalized runs. The maximum linear difference
was `1.5612511283791264e-17 m` and the maximum angular difference was zero. CAD
remained post-fit source-geometry evidence and influenced no solver setting or
held-out evaluation. This does not select or establish a general adapter or
supported integration. Onshape,
CloudCompare, and RealityScan are representative candidates, not product-support
commitments.

## Objective and Boundary

First validate one bounded generated-CAD path:

- generate one public Onshape document containing the Track 1 geometry-only
  variants as two independent Part Studios, `Axisymmetric` and
  `Asymmetric Datum Flat`, in the designated `Agent Sandbox`, folder ID
  `b788af3dad6250b9ed521e6a`
- pin and extract both Part Studios as the model/CAD-side probe
- compare extracted geometry with deterministic generator truth without inferring
  canonical intent from the CAD

CloudCompare and RealityScan observation-source probes follow only after the
nominal generated end-to-end path works and Track 2 Phase B authorizes source
copies. No external file or captured scan is required initially.

The generated fixture document is public under the Track 2 user decision. Calling
its creation "not publication" means only that the setup does not publish an
accepted fitting result back into CAD; it does not mean private document
visibility. The nominal path includes no result-publication plan or executor. Any
later publication planning remains pure and effect-free; any executor requires
separate approval, fresh target preconditions, and fresh pinned
read-back/reconciliation.

## Ownership

Track 2 owns access, licensing, data classification/retention, credentials, and
probe authorization. Track 5 references those records and owns:

- retained response bodies, assembler-generated metadata, and hashes
- generated CAD extraction and geometry reconciliation evidence
- later observation round-trip measurements
- import/normalization reports
- capability and degradation findings
- reproducibility evidence
- later pure publication-plan design

Track 3 owns illustrative example records. Track 5 reports observed gaps rather
than creating competing canonical examples or schemas.

## Initial Scope and Deferrals

The initial scope generates one public Onshape document containing independent
`Axisymmetric` and `Asymmetric Datum Flat` Part Studios with the matched
single-part geometry defined by Track 1: axis `+Z`; bands
`r = 12 mm, z = 0..20 mm`, `r = 18 mm, z = 20..50 mm`, and
`r = 14 mm, z = 50..80 mm`; axial/transverse planes; and the asymmetric
`x = 16 mm` cut over `z = 20..50 mm` with outward normal `+X`. All dimensions are
fixture-local and provisional.

Acquire one explicit source pin/configuration covering both variants in a
geometry-only evidence package with evaluated core geometry, units/frame,
revision-scoped bindings, and controlled derived tessellation. This is not the
complete geometry-and-native-relationship adapter snapshot defined by the CAD
extraction research.

Later external observation probes measure coordinates, ordering, source identity,
duplicates, overlapping memberships, units, frames/transforms, normals, selected
attributes, invalid values, and precision/loss.

Native relationships, assemblies, repeated/nested occurrences, blends,
topology-change stress/repair, cone normalization, automatic correspondence,
arbitrary feature trees, durable schemas/formats, publication planning, writes
beyond disposable fixture setup, retries, and executor behavior are outside the
nominal path.

## Semantic Boundary

Adapter import may produce observations, source handles, memberships, provenance,
and degradation findings. It must not silently create mappings, factors,
held-out roles, units, frames, fit-element intent, or parameter roles. Geometry
and current CAD placement do not establish those semantics. Source IDs and row
positions remain measured revision-scoped provenance, not canonical identity.

## Ordered Stages and Gates

1. **Phase A authorization:** reference Track 2's approved Agent Sandbox scope,
   public-document decision, auth path, generated-evidence retention and cleanup
   disposition, prohibited-content boundary, and stop conditions.
2. **Generated contract:** pin Track 1 geometry and Track 4 generator truth before
   CAD output; extracted geometry must never become the truth oracle.
3. **Disposable generation:** create one public document containing the two
   matched geometry-only variants as independent Part Studios inside Agent
   Sandbox; use no sensitive, proprietary, external, captured, credential, or
   physical-reference data and no assembly, native constraint, blend, or
   accepted-result publication path.
4. **Onshape geometry extraction:** acquire raw evaluated single-part geometry
   under an explicit immutable pin/configuration and preserve the returned body
   before transformation; treat tessellation as derived.
5. **Pure normalization:** normalize only the fixture cylinders and planes,
   preserving orientation, handedness, transforms, analytic parameters, and
   source bindings without inferring intent.
6. **Nominal reconciliation:** compare extraction with generator-defined geometry
   and the nominal fit path; classify differences without claiming physical truth
   or product support.
7. **Semantic audit:** demonstrate membership does not imply mapping and mapping
   does not imply a factor; generated held-out roles remain separately declared,
   generated held-out observations create no fit factors, and diagnostic
   evaluation occurs only after fitting.
8. **Capability/degradation report:** distinguish unavailable source data,
   unverified behavior, stale binding, ambiguity, unsupported Scansor semantics,
   and lossy conversion with explicit stop/warn/user-resolution behavior.
9. **Reproducibility:** verify pins, raw/derived hashes, settings, deterministic
    normalization, manual steps, and measured source/tool nondeterminism.
10. **Later external-source probes:** after Track 2 Phase B, measure CloudCompare
    and bounded RealityScan exports without selecting a supported winner.
11. **Later pure publication plan:** after applicable evidence and acceptance,
    describe intended effects and preconditions without effects.
12. **Future executor boundary:** require new approval, a fresh target pin, no
    unsafe replay, and a fresh read-back classified as exact match, tolerated
    difference, conflict, partial application, or unresolved.

## Planned Evidence

- fixture creation/readback observation and Phase A authorization references
- remaining Phase A stop conditions and evidence disposition
- public-document evidence-retention and cleanup/disposition records
- Onshape pin manifest and raw geometry-only response bodies
- pure normalization and generator-geometry reconciliation report
- semantic-boundary audit
- capability/degradation and reproducibility reports
- later observation probe matrix, raw exports, and round-trip reports
- later pure publication-plan design
- extension backlog

## Acceptance

Track 5 initially passes only when public disposable generation and extraction
are authorized inside Agent Sandbox, required retention and cleanup/disposition
records exist, prohibited content is absent, generator truth predates CAD output,
and one exact Onshape source pin/configuration covers both traceable variants.
Normalized core geometry must match the evaluated source and generator definition
within fixture-local tolerances, no intent may be inferred, all losses and
ambiguities must be visible, and another authorized run must reproduce results or
explain nondeterminism. This is generated CAD extraction evidence only, not
physical accuracy or product support.

The observed v1 fixture remains preliminary authoring and analytic-readback
evidence only. The v2 fixture was regenerated after freezing the deterministic
contract and passes one bounded experiment-local geometry comparison. The
second authorized CAD branch and immutable version, retained run-01 and run-02
MCP-returned JSON response bodies, assembler-generated request/response metadata
and checksums, replayable normalization, and the verified cross-run suite now
pass the bounded generated-CAD reproducibility gate. Run-01 versus frozen truth
and run-02 versus frozen truth pass within preregistered tolerances, and
cross-run normalized geometry is semantically equivalent and exactly equal.
Raw-response change causes are assembler-assigned, hash-pinned classifications
by operation and category, not structurally proven causal explanations derived
from differing JSON paths. Authenticated HTTP transport envelopes were not
retained.
A general adapter, external-source behavior, and physical evidence remain open,
and Track 5 has not become a supported integration.

The separate reconciliation source and evidence are SHA-256 pinned and replay the
existing Track 5 verifier rather than introducing another normalizer. They retain
the 7/8 face inventories, right-handed metre frame, unoriented cylinder axis,
oriented axial/datum normals, and distinct source-normal, orientation, and
outward-normal fields. Both runs are required; their values are neither averaged
nor selected by closeness. This remains generated experiment reconciliation only.

## Extension Path and Stage-Entry Choices

Later, separately gate CloudCompare/RealityScan source probes, physical evidence,
cone geometry, blends, topology regeneration/repair, native relationships,
assemblies/dependency closure, publication planning, an executor experiment,
fresh read-back/reconciliation, and other CAD/observation candidates.

Before their applicable later stages begin, user choices include the first
CloudCompare export route, required identity survival under reorder/filter,
RealityScan's expected parity or degraded role, and the first future publication
class. Initial choices are limited to fixture-local normalization tolerances and
the Track 2 Phase A evidence disposition.

[reconciliation]: ../../../../experiments/generated-track5-cad-reconciliation-v1-evidence.json
