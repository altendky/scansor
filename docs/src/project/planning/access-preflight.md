# Track 2: Access and Evidence Preflight

## Status

**Provisional with observed evidence, snapshot dated 2026-07-26.** Creation and
readback of one public generated fixture inside Agent Sandbox, including its
authorization/visibility check, are [observed](../experiments/generated-onshape-fixture.md).
Evidence disposition and broader Phase A closure remain open. This track
authorizes and inventories access; it does not implement or validate adapters.

## Objective and Ownership

Separate only the access decisions needed for the generated path from later
external-source and physical prerequisites.

**Phase A: sandbox/auth/evidence disposition** owns:

- Onshape account/tenant context, API eligibility, and license evidence
- designated Agent Sandbox scope and disposable-document authorization
- public generated-evidence retention, cleanup, deletion, redistribution, and
  redaction disposition
- least-privilege secrets controls and redaction requirements
- explicit generated-fixture authorization and stop-condition handoff to Track 5

**Phase B: deferred external/physical preflight** later owns:

- external asset ownership, confidentiality, use, retention, and redistribution
- installed CloudCompare/RealityScan versions and candidate-data inventory
- physical-artifact, captured-data, physical-pose, and independent-reference readiness
- external and physical authorization handoffs

Track 5 owns raw adapter evidence, round-trip measurements, normalization,
degradation/capability reports, and reproducibility. Track 2 does not duplicate
those artifacts.

## Onshape Organization Facts

One top-level personal Onshape folder named `Scansor` exists at the [recorded
folder URL][onshape-folder], with folder ID `d262d0122052ddf2b4851035`. Its
designated disposable `Agent Sandbox` is at the [recorded sandbox URL][agent-sandbox],
with folder ID `b788af3dad6250b9ed521e6a` directly under `Scansor`.

These are organization facts only. Agents may operate through the designated
Agent Sandbox under the repository policy, but must not create, modify, move,
rename, or delete other direct contents of `Scansor` without explicit user
agreement. The facts establish no account access, API entitlement, export right,
accepted-result publication authority, or product support.

## Observed Access and User Decision

- **Observed, 2026-07-26:** an attempt to create a private Onshape document on
  the free account returned HTTP `409`. This observation records the attempted
  operation and response; it does not establish broader API or product behavior.
- **User decision, 2026-07-26:** every Onshape document created for this work will
  be public. Initial authorization is limited to disposable synthetic Scansor
  fixture documents created inside the designated Agent Sandbox.
- **Observed, 2026-07-26:** the authorized fixture was created in Agent Sandbox
  and read back through official REST operations. Its public ACL allowed Onshape
  users while `anonymousAccessAllowed` was `false`; see the [fixture evidence
  report](../experiments/generated-onshape-fixture.md).

Public fixture documents must contain no sensitive, proprietary, external,
captured, credential, or physical-reference data. Each generated document needs
an explicit evidence-retention record and an explicit cleanup/disposition record.
Even if a document is deleted, deletion cannot retract public screenshots, hashes,
URLs, exports, or copies already made.

## Initial Scope

Phase A targets public generation and read-only extraction of the provisional
single-part fixture pair inside Agent Sandbox. Authorization covers only
disposable synthetic Scansor fixture documents. It needs and permits no real
artifact, captured scan, external source file, independent measurement,
metrology evidence, credential content, or physical pose policy. A cone, blends,
native mates, assemblies, repeated/nested occurrences, topology stress, and all
accepted-result publication tests are deferred.

Onshape is a representative generated CAD candidate, not a support commitment.
CloudCompare and RealityScan are deferred Phase B observation candidates.

## Access Matrix

| Surface | Track 2 must establish | Initial allowed operation | Initial status |
| --- | --- | --- | --- |
| Scansor/Agent Sandbox folders | Names, URLs, IDs, and repository policy only | Organization reference | Organization fact |
| Onshape account/API | Account/tenant context, role, entitlement, auth path, limits, and license constraints | Phase A public auth/read inspection | Private creation HTTP 409; public creation/read observed for fixture |
| Generated Onshape fixture pair | Public disposable-document scope, pin visibility, evidence retention, and cleanup/disposition | Generate publicly in Agent Sandbox, then pinned read/extract | Sandbox creation and visibility observed; disposition open |
| CloudCompare | Installation, license, authorized source copy, and retention permission | Phase B bounded read/export probe | Deferred |
| RealityScan | Installation, license, project ownership, source-copy and export permission | Phase B bounded classification export | Deferred |
| Physical artifact | Custody, suitability, scan access, and datum-flat visibility | Phase B inspect/measure without alteration | Deferred |
| Reference equipment | Availability, operator, resolution, calibration status, and permitted records | Phase B independent repeated measurement | Deferred |
| Accepted-result CAD publication access | Not assessed initially | None | Deferred |

## Planned Outputs

- a Phase A access/licensing record containing the dated HTTP `409`, public-only
  decision, auth evidence, and sandbox evidence
- an explicit generated-evidence retention and cleanup/disposition record covering
  deletion, redistribution, screenshots, hashes, URLs, copies, and derived evidence
- a secrets-control checklist containing references/provenance but no secret values
- a Track 5 handoff listing the authorized Agent Sandbox operations, prohibited
  operations, evidence constraints, generated geometry probe, and stop conditions
- a deferred Phase B checklist for external assets/tools, physical fixture,
  captured data, retention, reference equipment, and measurement-value custody

## Ordered Gates and Stop Conditions

### Phase A

1. **Sandbox boundary:** verify the Agent Sandbox URL/ID and that all generated
   documents remain public, disposable, synthetic Scansor fixtures inside it.
   Stop before touching other `Scansor` contents.
2. **Onshape auth/read:** verify account/tenant, API eligibility, auth options,
   license constraints, disposable-document creation scope, immutable pin
   visibility, and geometry-read rights.
3. **Public-content boundary:** prohibit sensitive, proprietary, external,
   captured, credential, and physical-reference data in every public Onshape
   document for this work.
4. **Generated evidence disposition:** record retention, cleanup, deletion,
   redistribution, screenshot, hash, URL, copy, and redaction handling for each
   generated document, acknowledging that deletion cannot retract prior copies.
5. **Secrets controls:** require approved injection, least privilege where
   available, and redaction before persistence. Stop before authentication if
   safe handling is unavailable.
6. **Generated probe authorization:** authorize only public disposable synthetic
   Scansor fixture generation in Agent Sandbox and bounded pinned geometry
   extraction; authorize no publication of accepted fitting results back into CAD.
7. **Disposition:** mark each requirement source-confirmed, observed, open,
   blocked, or deferred and hand the bounded scope to Track 5.

### Phase B

After nominal generated end-to-end success, separately establish external asset
ownership, source-copy/tool permission, retention, physical-fixture suitability,
captured-data protocol, physical pose policy, independent instruments, repeats,
uncertainty approach, and blinded reference-value custody. None blocks Phase A.

## Acceptance and Dispositions

Phase A passes only when public sandbox generation and one pinned fixture-pair CAD
read have sufficient authorization, content exclusions and generated-evidence
retention/cleanup dispositions are explicit, secrets can be handled safely, and
Track 5 receives a bounded handoff. Unknowns remain explicit.

- **Pass:** all Phase A prerequisites for generated fixture-pair creation and one
  pinned read are established.
- **Conditional pass:** bounded generated work can proceed while a nonessential
  API path or evidence detail remains open.
- **Blocked:** sandbox/public scope, authentication, secret handling, content
  exclusion, generated-evidence retention/cleanup disposition, or geometry-read
  access is unresolved.
- **Deferred:** Phase B external/physical work, publication, and extensions remain
  outside initial preflight.

## Immediate User Inputs and Quick Checks

The public-document decision and one sandbox fixture creation/visibility/readback
check are complete. Initial remaining input is the retention and
cleanup/disposition for each disposable public fixture document. Remaining quick
checks target broader account/API and license scope, prohibited-content
exclusion, secrets controls, and reproducibility under another authorized run.
Physical candidate assets, captured/physical/third-party data retention,
independent equipment, and CloudCompare/RealityScan installations are later
Phase B inputs.

[onshape-folder]: https://cad.onshape.com/documents?nodeId=d262d0122052ddf2b4851035&resourceType=folder
[agent-sandbox]: https://cad.onshape.com/documents?nodeId=b788af3dad6250b9ed521e6a&resourceType=folder
