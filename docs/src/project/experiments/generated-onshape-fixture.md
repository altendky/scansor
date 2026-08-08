# Generated Onshape Fixture Observation

## Status

**Experiment-local generated and CAD evidence, dated 2026-07-30.** The internal
provisional `stepped-rotational-v1` truth contract, deterministic generated
observations, and a regenerated public v2 Onshape fixture pass their bounded
generator checks. Retained evidence from two authorized immutable CAD versions
consists of MCP-returned JSON response bodies plus assembler-generated
request/response metadata and checksums. Offline normalization, truth
reconciliation, and cross-run comparison now pass this experiment's bounded
Track 5 generated-CAD reproducibility gate.
The official response-shape mismatch that blocked the first run-2 attempt was
corrected and covered by trust-boundary tests before capture. Separate retained
[Track 4 evidence][solver-evidence] now executes exactly all `15` frozen
solver/evaluator scenarios, including recovery, derivatives, held-out exclusion,
balanced noise, coverage, mapping, bounds, invalid geometry, fixed outliers, and
model mismatch. Runner-local policy derives passed, review-required, and failed
dispositions from measured evidence; the fixed-outlier linear control remains
explicitly unclassified because no robustness policy or product threshold is
approved. A complete adapter, external sources, and physical validation remain
open. These passing
bounded experiments are not product-support or general Onshape-adapter claims.
The preliminary v1 observation is preserved below and remains preliminary
evidence only.

## Frozen Generator Contract

[`generate_stepped_rotational_v1.py`][generator], revision `1.0.5`, is a
standalone experiment supporting Python `>=3.12,<3.14` with NumPy `2.3.1` pinned
for the reference-environment evidence below. Python 3.14 is excluded because it
is not an officially supported target of that NumPy release. The generator
analytically produces the two variants in SI `float64`; it does not sample CAD
tessellation. Its emitted
`truth-manifest.json`, `records.json`, `scenarios.json`, and `observations.npy`
are internal experiment artifacts, not a durable or public schema or selected
bulk format. Large generated output is not checked in.

The frozen contract records:

- local `+Z` rotational axis and right-handed frame; the asymmetric plane
  defines `+X`
- radii `12`, `18`, and `14 mm` over stations `0..20`, `20..50`, and
  `50..80 mm`
- the four axial faces and the asymmetric bounded plane at `x = 16 mm`,
  `z = 20..50 mm`, and
  `y = +/-sqrt(18^2 - 16^2) mm = +/-8.246211251235319 mm`
- the datum half-width frozen directly as decimal
  `0.008246211251235319 m`; `sqrt(18^2 - 16^2)` remains descriptive derivation
  metadata checked against that value within `1e-15 m`
- axial radial domains `0..12`, `12..18`, `14..18`, and `0..14 mm`; the
  asymmetric station-20 and station-50 patches also retain the `x <= 16 mm` trim
- the middle cylinder's analytic support separately from its asymmetric bounded
  face trim `x <= 16 mm`
- deterministic analytic grids, lexicographic stable IDs, explicit metre and
  radian units, model-to-observation pose, and edge guards of `2 mm` axial,
  `1 mm` radial, `1 mm` flat-boundary, and `5 degrees` angular
- a circular-distance assertion from both asymmetric middle-cylinder trim
  intersections at `+/-acos(16/18)`; the nearest generated sample is
  `6.483955549267173 degrees` from a seam
- a nonidentity pose truth with rotation vector `(0.08, -0.05, 0.12) rad` and
  translation `(4, -3, 6) mm`
- observation records carrying distinct membership, mapping-ID, factor-ID, and
  role fields; this experiment does not emit separate mapping or factor record
  collections, and memberships may overlap but imply neither mappings nor factors
- explicit `active_factor_ids` in every scenario, independent of available
  observations, mappings, and memberships
- no more than one primary raw signed point-to-support factor in metres per
  training observation; weights and future robustification remain separate
- the axisymmetric axial-roll equivalence class and expected pose rank `5`, the
  asymmetric expected rank `6`, and flat-factor-ablation rank `5`
- fixed/free/shared/derived parameter roles, exact axis/station/incidence/datum
  relationships, positive radii, strictly ordered stations, a datum inside the
  middle radius, and nonempty guarded domains
- preregistered CAD comparison tolerances of `1e-9 m` for linear values, radii,
  and stations and `1e-9 rad` for axes and face normals

The generated corpus contains `621` observations: `454` training observations
and factors, `167` generated held-out observations, and `1,888` membership
entries. Held-out observations occur in no factor or scenario active-factor
selection.
The 15 preregistered scenarios are evaluator oracle, noiseless fixed pose,
axisymmetric free roll, asymmetric full pose, flat-factor ablation, coherent
held-out strips/sectors, adequate/uneven/inadequate coverage, balanced normal
noise paired within declared same-local-normal cells across all `454` training
factors, including `16` datum-flat factors, with zero scalar and vector sums per
applicable cell, fixed outliers, one corrupted
mapping, one legal active bound, one invalid-geometry declaration, and one
out-of-contract elliptical mismatch declaration.

## Generator Verification

The command below was run twice into separate temporary directories, followed
by `--compare`:

```console
uv run --python 3.12.12 \
  experiments/generate_stepped_rotational_v1.py --output <new-directory>
uv run --python 3.12.12 \
  experiments/generate_stepped_rotational_v1.py --compare <run-a> <run-b>
```

All `37` self-checks passed. They cover bounded membership, unit oriented
normals, exact supports, datum bounds, absence of datum observations in the
axisymmetric variant, flat-factor deactivation while its factors remain
instantiated, held-out leakage, overlap
factor independence, deterministic factor IDs/counts, explicit scenario factor
activation, edge guards, the circular asymmetric middle-cylinder seam guard,
oriented residual signs, computed expected pose ranks, valid geometry/topology,
axisymmetric roll invariance, nonzero datum leverage, finite generated arrays,
exact row-to-array ID/order correspondence, role vocabulary, held-out null factor
fields, mapping/factor/membership/scenario-factor referential integrity, scenario
input eligibility, scenario-specific field semantics, unique scenario inputs and
active-factor lists, complete balanced-noise element/count coverage, the isolated
invalid declaration, scenario completeness, source/runtime provenance,
unconditional frozen source/contract/records/scenarios/counts/ID evidence, and
reference-only numerical hashes.
Before either fixture was compared, all four files in each directory were
independently loaded and validated. Each directory had to contain exactly the
four expected regular, non-symlink artifacts. NPY loading used
`allow_pickle=False`; JSON loading rejected invalid UTF-8, duplicate keys,
non-finite values, unexpected top-level types, and noncanonical serialization.
The loader recomputed artifact byte hashes and logical hashes rather than trusting
the manifest, validated the supported-runtime provenance envelope, reconstructed
the complete expected manifest, and reran all semantic checks over the loaded
array, records, and scenarios. Only then did comparison test artifact bytes and
logical hashes. All four files were byte-identical across the two Python 3.12.12
runs, and their logical content was equal. No solver output was used to select or
tune expectations.
Independent verification also inspected active factor IDs, flat ablation,
held-out leakage, seam distance, balanced-noise coverage, `5/6/5` ranks, roll
invariance, datum leverage, finite values, ordering, IDs, and every logical and
artifact hash without using the generator's check functions.

A supported-runtime generation under Python 3.13 also passed all `37` checks and
was independently loaded under the same recorded Python 3.13 runtime. Fixture
loading requires provenance to match the executing validation environment, so a
coordinated provenance rewrite cannot select a weaker validation context. Its
platform-independent hashes matched; reference numerical hashes correctly
reported `NOT APPLICABLE` because its recorded runtime was not the exact frozen
reference environment. Negative
tests established that a bad source sidecar fails before an existing `--replace`
target is touched; a forced in-memory invariant failure preserves an existing
target and creates no new partial target; two identically corrupted directories
cannot pass comparison; independent NPY, records, scenarios, and manifest
corruptions are each detected; and a valid existing output without `--replace`
remains byte-for-byte untouched.

| Artifact or content | SHA-256 |
| --- | --- |
| Generator source | `995a067d7f4bd247defd092a7e8501224ce7393e9e9e09dc47726f13b610a1e8` |
| Contract logical content | `2c77ce6c586a5f5ebc29f1dfe93f6f19264a8849a8dd62ccb22ef3b5338ca175` |
| Dataset logical content | `2b93c6da9a615618aae639faaea22991c0fe4bf7b1fb43294c172f9c62132b87` |
| `observations.npy` logical content | `06f2424839d049c4c2205394d1e3739b0ad2fd6402d9fa4c8f28009f88d244c9` |
| `observations.npy` bytes | `2166b22397fc07ee3e87f39ba406db667a0a190a898f476f1efee34d733b41ef` |
| `records.json` logical content and reference-environment bytes | `ee20662f27374cd9e80fc509acdce64cab613c4b97a9303678eb8a00d97300b6` |
| `scenarios.json` logical content and reference-environment bytes | `6ff6c7a5987d667563b4453d848c1ef2e4e29d763d9233924ff1d5d7edffa6c3` |
| `truth-manifest.json` reference-environment bytes | `68aa49b7a8736f9041ae4d551a33aac80a424040a14d34c863774bb22e19cb89` |

The reference environment was CPython `3.12.12` built with Clang `21.1.4`, NumPy
`2.3.1`, Linux `7.0.0-27-generic` (`#27-Ubuntu SMP PREEMPT_DYNAMIC Thu Jun 18
19:13:49 UTC 2026`), `x86_64`/64-bit, glibc `2.43`, and little-endian native byte
order. Python used radix-2 binary64 with a 53-bit mantissa and short float
representation; NumPy reported binary64 epsilon `2.220446049250313e-16` and
native (`=`) float64 byte order. The manifest also records reference evaluations
of `acos(16/18)` and the datum-bound square root. Source hash, contract logical
hash, dataset logical hash, and reference-environment artifact byte hashes are
separate. Exact artifact byte hashes are evidence for this environment only and
are not claimed to reproduce across platforms or math/runtime implementations.
The generator verifies its exact source checksum from the adjacent checksum
sidecar before compare handling, output deletion, directory creation, or any
output mutation. Generation then prepares the array, records, scenarios,
manifest, and exact serialized bytes in memory and runs contract, logical-hash,
byte-hash, frozen-evidence, count, ID, activation/leakage, seam, noise, rank, and
all other semantic checks. Only a fully validated preparation may replace or
create an output directory. The generator enforces the contract logical hash,
records hash, scenarios hash, exact counts and IDs, and semantic invariants on
every supported runtime. Dataset, observation logical, and NPY byte hashes are
numerical reference evidence and are enforced only in the exact environment
above. Output reports numerical reference hashes as `MATCHED` there or
`NOT APPLICABLE` on another supported runtime; platform-independent drift always
fails generation.

## Preregistered v2 Fixture

An exact-name lookup under Agent Sandbox found no v2 before creation and one
v2 afterward. The public document is [Scansor Generated Fixture - Stepped Datum
Flat v2][v2-document]:

- document ID `1b68f4b8f4a69c6b59d7616e`
- workspace ID `6e2cc94501f05a40302c95bc`
- parent `Agent Sandbox` folder ID `b788af3dad6250b9ed521e6a`
- final document microversion `35908255c9c46816eae3e602`
- immutable version `stepped-rotational-v1 CAD evidence`, version ID
  `69ef588c1fee61aa2e65745c`, at the same microversion
- `public: true`; `anonymousAccessAllowed: false`, so anonymous visibility was
  not established

The document contains exactly two independent Part Studios:

- [Axisymmetric v2][v2-axisymmetric], element ID
  `3b0748b60dddef666359609b`, element microversion
  `290201542dd5139f7ba3d7f8`, with profile sketch
  `FU49Qx6G9lNBErx_0` and revolve `Fioodd7h0SAoZtG_0`
- [Asymmetric Datum Flat v2][v2-asymmetric], element ID
  `9cd727f2ffb7eb665c99ed7f`, element microversion
  `0b9f44691232e176af69569f`, with profile sketch
  `F2pd1P3xDItU6qL_0`, revolve `Fu3DKOmK59ncI0y_0`, datum-removal sketch
  `F4hJlyOhv8YlNII_1`, and remove extrude `F6aTlcV9knda8rM_1`

All features were active, unsuppressed, and `OK`. Both 23-constraint profile
sketches and the 17-constraint removal sketch reported `WELL_DEFINED` from the
sketch-info endpoint. The driving dimensions are `12 mm`, `20 mm`, `6 mm`,
`30 mm`, `4 mm`, and `30 mm` in each stepped profile, which resolve the frozen
radii and stations, and `16 mm`, `20 mm`, `4 mm`, and `30 mm` for the bounded
datum removal. Authoring requests used official persistent document, Part Studio,
sketch, revolve, and extrude-remove operations with requested FeatureScript
library `3029`, request serialization `1.2.21`, dependent source microversions,
and skew rejection. Those request fields are authoring metadata, not readback
confirmation or part of the frozen generated-observation contract.

## CAD-to-Truth Comparison

Body details and bounded FeatureScript were requested at immutable microversion
`35908255c9c46816eae3e602`. The recorded readback payload metadata identified that
source microversion and no observed skew; it is distinct from the authoring-request
metadata above. Endpoint response serialization metadata is variable and
non-contractual. Each studio contains one solid. The final solid face inventories
are:

- axisymmetric: three cylindrical faces and four axial planar faces, seven
  total; no bounded `X`-normal body plane
- asymmetric: the same three underlying cylindrical supports, four axial
  planar faces, and one datum plane, eight total

Both variants have coincident `Z` axes; body details choose analytic cylinder
axis direction `-Z`, which is the same unoriented axis as the contract's `+Z`.
The radii are `0.012`, `0.018000000000000002`, and `0.014 m`; bounded axial
domains resolve to `0..0.02`, `0.02..0.05`, and `0.05..0.08 m`. The asymmetric
datum face is at `x = 0.016 m`, over `z = 0.02..0.05 m` and
`y = -0.008246211251235326..0.008246211251235327 m`.

Axial-face loops and tight boxes resolve the contract's disk/annulus domains:
station 0 has outer radius `12 mm`; station 20 has inner/outer radii
`12/18 mm`; station 50 has inner/outer radii `14/18 mm`; and station 80 has
outer radius `14 mm`. In the asymmetric studio, the station-20 and station-50
outer loops terminate at `x = 16 mm`; their tight boxes have maximum
`x = 0.016 m`. Oriented body normals are `-Z` at stations 0 and 20 and `+Z` at
stations 50 and 80.

The maximum observed radius or station deviation was
`3.469446951953614e-18 m`; the maximum datum-bound deviation was
`8.673617379884035e-18 m`. Axis and oriented datum-normal angular deviations
were zero to the reported precision. All are below the frozen `1e-9 m` and
`1e-9 rad` tolerances, so the bounded CAD-to-truth comparison passes.

Body details report the source analytic datum-plane normal as `(-1, 0, 0)` and
face orientation `false`; bounded oriented FeatureScript reports the outward
face normal as `(+1, 0, 0)`. Both are preserved rather than conflated. This
comparison concerns supported analytic geometry only and makes no topology-ID,
general adapter, solver, physical-accuracy, metrology, or product-support claim.

This v2 fixture satisfies this experiment's preregistered geometry, authoring,
pinning, and one bounded CAD-to-truth readback requirements. At this point in the
chronology it did not yet pass the cross-run gate; the later run-2 and replay
evidence below closes that experiment-local gap. The verified run used generator
source content identified by its SHA-256; that source was uncommitted at
observation time, so repository history was not its identity.

## Track 5 CAD Reproducibility Run 2

Authenticated preflight on 2026-07-30 confirmed that this public synthetic
document remained directly under Agent Sandbox; Start version
`3aab803f12cc63b659ecb1ab` resolved to empty microversion
`3d03486b79ccfd0db79f5172`; the target workspace and version names were absent;
and the dedicated current-microversion read for Main remained
`35908255c9c46816eae3e602`. The run made no Main write and did not merge or
modify the run-01 evidence version.

A fresh workspace named `Track 5 CAD reproducibility run 2`, ID
`ea4a9557ef5bb6e110e45f7f`, was created from Start. It contains exactly two
independent Part Studios:

- [Axisymmetric v2 run 2][run2-axisymmetric], element ID
  `32cee5c10f33711f9778c52c`, with fixed-coordinate profile sketch
  `FSTvZEGGsLXUCzw_0` and full revolve `FRx390IC9wYFUf4_0`
- [Asymmetric Datum Flat v2 run 2][run2-asymmetric], element ID
  `834df2af620335b532c68169`, with fixed-coordinate profile sketch
  `FR9Nz04FpzL9jkQ_0`, full revolve `F9D1oQqz7BEmuDn_0`, fixed-coordinate
  removal sketch `FCHvMsJsqEOruIV_1`, and through-all remove
  `FvF4n87RSIwNFTi_1`

All six authored features were active and reported `OK`. Authoring used
FeatureScript library `3029`, serialization `1.2.21`, dependent document
microversions, and skew rejection. Unlike run 1, the run-2 sketches freeze each
exact line directly with `FIX` constraints instead of reproducing the earlier
dimensional-constraint presentation. This is an experiment-local authoring
difference, not a claim that either style is a selected product contract.

The immutable [run-2 evidence version][run2-document] is
`3edab4a4e7521986ca01b160` at microversion
`4b4d24b3a5716d2a58481ae9`, directly parented by Start. Direct body-details
readback found one solid and seven/eight faces, radii `0.012`,
`0.018000000000000002`, and `0.014 m`, stations `0`, `0.02`, `0.05`, and
`0.08 m`, and the asymmetric datum at `x = 0.016 m`,
`y = -0.008246211251235326..0.008246211251235327 m`, and
`z = 0.02..0.05 m`. Cylinder axes were `-Z`, the same unoriented axis as frozen
`+Z`; the datum source normal was `-X` with face orientation `false`. These
values match the frozen truth and the recorded run-01 values within `1e-9 m`
and `1e-9 rad`.

The first capture attempt failed because live `getPartsWMVE` returned the
official top-level array and transient part ID `JHD`, while the reviewed verifier
expected a synthetic envelope and 24-hex ID. Follow-up inspection also found
official top-level element arrays, `microversionId.theId` body pins, full parent
objects, version metadata-workspace IDs that do not identify the originating
workspace, run-specific tab names, and typed `BTFSValue` serialization.

The corrected [Track 5 verifier][track5-verifier], source SHA-256
`8d165b5ea88b6c48ba25d0e351a60835d486a23e3fce4882ee725ba020dca3d7`,
accepts those bounded official shapes without relaxing exact immutable run,
element, transient part, body, request, source-microversion, ancestry, unit,
orientation, and raw/derived hash checks. Its bounded FeatureScript evaluates
body-owned solid faces, tight boxes, analytic surfaces, tangent planes, and
transient face IDs. All four immutable-version evaluations returned one solid,
the expected 7/8 faces, no notices, and no microversion skew. The offline suite's
`39` tests passed, including official-array, transient-ID, typed-value, pairing,
path/query/body, gzip, hash, file-tree, secret, and replay boundaries.

Durable evidence is retained in [run-01 backfill][run1-evidence], [run 2][run2-evidence],
and the [cross-run suite][suite-evidence]. The run manifest SHA-256 values are
`aa257d2707a4fba283aaa2c810e48c4793d1d64a5475cdcd24bf8ae7e65ed1e3`
and `373907a5c1a3d8ec7cd9cc2a878a71afc57ea6722132180e71ff8e23cd8e34ae`;
the suite-manifest SHA-256 is
`f6e373536b6fa2f76b349624fa8652418191608e1434319cbc64ead9a7a0242a`.
The run-01-versus-truth and run-02-versus-truth reports both pass as `tolerated
numerical`, with maximum deviations still below the frozen tolerances. Cross-run
normalized geometry is exactly equal and the suite outcome is `semantically
equivalent`. Revision IDs and variable source metadata differ as expected. For
each changed raw response, the assembler assigns a hash-pinned classification by
operation and category; any stated change cause is therefore an assigned
classification, not a structurally proven causal explanation derived from
differing JSON paths. The bounded Track 5 generated-CAD
reproducibility gate therefore passes. Capture used authenticated read-only MCP
calls followed by the pinned offline assembler. Retained evidence consists of the
JSON response bodies returned by MCP plus request/response metadata and checksums
generated by the assembler from the successful calls and frozen operation
contract. Authenticated HTTP transport envelopes were not retained. No
credential, authorization header, or independent authenticated transcript is
retained. The evidence integrity statement explicitly does not claim that
SHA-256 authenticates Onshape response origin or request/response pairing;
authorization and no-Main-write statements remain observations from the
separately recorded preflight and run history rather than properties proved by
these response directories.

## Fixture Identity and Access

The public document is [Scansor Generated Fixture - Stepped Datum Flat
v1][fixture-document]:

- document ID `abe74b2a00c7303f2356788d`
- workspace ID `65079d9282e4379901da22e5`
- parent `Agent Sandbox` folder ID `b788af3dad6250b9ed521e6a`
- current document microversion `39794f9b977f321a8a665d98`
- this microversion was the immutable v1 readback pin; no named immutable Onshape
  version at this evidence pin was created or recorded for v1

The account returned HTTP `409` when private document creation was attempted.
The user then decided that all Onshape documents created for this work are
public. The observed public access-control response allowed Onshape users but
reported `anonymousAccessAllowed` as `false`; "public" must not be interpreted as
verified anonymous access.

## Part Studios and Features

The document contained exactly two Part Studios:

- [Axisymmetric][axisymmetric] had element ID
  `544c0b40e30344e3475caf3a` and current element microversion
  `f39d903e0e1f3a7dfbb222c6`. Its two active, unsuppressed features reported
  `OK`. Its profile sketch had 23 explicit driving or geometric constraints.
- [Asymmetric Datum Flat][asymmetric] had element ID
  `fd2155c812c01bcadc82f202` and current element microversion
  `fdd433ded83f8eaf3700b15b`. Its four active, unsuppressed features reported
  `OK`. Its main profile had 23 constraints. Its removal profile had 17
  constraints, including locators at `x = 16 mm` and `z = 20 mm`.

The initial sketches were unconstrained. After user review, explicit driving and
geometric constraints were added, and the downstream feature and body geometry
was read back again. The reverified geometry was unchanged.

## Geometry Readback

Each Part Studio contained one solid. Body-detail and FeatureScript readback
observed seven faces on the axisymmetric solid and eight faces on the asymmetric
solid. Both had coincident `Z` axes and the same three analytic cylindrical
supports:

- radius `12 mm` over `z = 0..20 mm`
- radius `18 mm` over `z = 20..50 mm`
- radius `14 mm` over `z = 50..80 mm`

The axisymmetric body had no `X`-normal body plane. The asymmetric body had a
bounded `X`-normal plane at `x = 16 mm`, over `z = 20..50 mm`, with
`y` approximately `-8.246211..8.246211 mm`.

The body analytic representation reported the plane normal as `(-1, 0, 0)`,
while oriented FeatureScript face evaluation reported `(+1, 0, 0)`. This report
therefore treats the face as an `X`-normal plane and preserves the source and
orientation semantics rather than assuming that either sign is a universal
normal convention.

The feature and body responses identified source microversion
`39794f9b977f321a8a665d98`, matching the dedicated current-document-microversion
response, with no observed skew. A general document response exposed an older
microversion embedded for the default workspace. Verification therefore used the
dedicated current-microversion endpoint and each feature or body response's
source microversion, not that older embedded value.

## Method Boundary

Generation used official persistent document, Part Studio, and feature REST
operations. Authoring requests specified FeatureScript library `3029`, request
serialization `1.2.21`, dependent source microversions, and skew rejection.
Readback used body details and FeatureScript at the recorded microversion pin.
Retained evidence consists of MCP-returned JSON response bodies plus
assembler-generated request/response metadata and checksums, not authenticated
HTTP transport envelopes. Variable endpoint serialization metadata is not a
contract claim.

## Limitations

This observation establishes only that this public synthetic document was
created and that its bounded feature/body geometry was read back as described.
It makes no claim about:

- stable topology or durable face identifiers
- Onshape or other CAD product support
- generated observations, evaluator behavior, solver fitting, or end-to-end fit
- canonical normalization outside this bounded experiment
- general loop or topology equivalence; normalization retains only the supported
  cylinder/plane parameters and preregistered radial and Cartesian bounds needed
  by this fixture
- native constraints, assemblies, or publication of accepted results
- physical dimensions, manufacturing accuracy, metrology, or physical accuracy

## Failures and Manual Steps

- Initial OAuth validation returned HTTP `401`; the user refreshed configured
  OAuth, after which validation succeeded. This was the only manual step.
- A documented delete-element call against the still-empty asymmetric studio
  returned HTTP `403 Invalid API key state` and made no change. No deletion was
  needed: the required studio was authored independently in place.
- The first bounded FeatureScript script used reserved local name `box` and
  failed parsing without a result or mutation. Renaming it to `bounds` passed at
  the same v2 immutable microversion pin.
- The public document contains only synthetic dimensions and explanatory text,
  but public evidence cannot be retracted by later cleanup. No cleanup date is
  selected; the named v2 immutable version is intentionally retained for this
  evidence.

## Generated Fit to Retained CAD Reconciliation

The separate [Phase 2 reconciliation source][reconciliation-source], SHA-256
`b352b483e869741cbf78c7ac4575835e6bd0c95f604129def0f1acb850f37908`,
contains no optimizer, NumPy/SciPy import, or solver-module import. It invokes the
pinned Phase 1 verifier from a fresh temporary source/evidence snapshot as an
isolated exact-runtime offline subprocess. It executes Track 5 directly from its
verified source bytes rather than a path-loader bytecode cache, and its generation
API returns bytes while the CLI writes only to standard output; neither accepts a
filesystem output path. Only after generator source, contract, solver source,
solver evidence sidecars, and semantic recomputation passed did it derive and hash
canonical immutable prediction bytes from
the verified `noiseless-fixed-pose` seven-shape estimate. Its event record places
solver verification at index `3`, prediction freezing at index `5`, and first CAD
path access at index `7`.

The tool then verified Track 5 source SHA-256
`8d165b5ea88b6c48ba25d0e351a60835d486a23e3fce4882ee725ba020dca3d7`,
the two run-manifest hashes and suite-manifest hash, transitively recomputed the
suite, and independently replayed run 01 and run 02. It compared the same fitted
prediction bytes with each run through a fresh independent deserialization,
without averaging or closer-run selection. The retained
[reconciliation evidence][reconciliation-evidence], SHA-256
`9fff881fbaa245423d91d0f6bb79fdcb8d4245598e46d5702cd0115cbc5bf0c5`,
reports these bounded results:

- fitted estimate versus generated truth: maximum linear difference
  `1.5612511283791264e-17 m`, maximum angular difference zero
- fitted prediction versus run 01: maximum linear difference
  `1.5612511283791264e-17 m`, maximum angular difference zero across `87` fields
- fitted prediction versus run 02: the same maxima independently across `87`
  fields
- existing run-to-truth outcomes remain `tolerated numerical`; cross-run geometry
  remains exact and the outcome remains `semantically equivalent`
- revision-ID changes and variable metadata remain separate classifications
- raw-change classifications are additionally bound by notes SHA-256
  `1c853d7cc013a3144950afece3f0e3ce2ff55a80de9290d5ec4299133fcfabf7`
- all CAD-influence lists for residuals, initialization, bounds, weights, loss,
  scales, tuning, and held-out evaluation are empty

Each disposable prediction copy preserves the 7/8 face counts, variant semantics,
right-handed metre frame, unoriented cylinder axis, oriented axial and datum
normals, middle trim and station bounds, and the distinction among source normal,
orientation, and outward normal. Before/after fingerprints confirm that the `21`,
`21`, and `6`
files in the two runs and suite were unchanged at those checks. This closes
only the generated experiment's nominal post-fit reconciliation. CAD is not
independent or fit truth, and this evidence makes no reusable-schema, adapter,
physical-accuracy, metrology, production, or product-support claim.

## Next Evidence

The bounded evaluator/solver execution gate now verifies exactly all `15` frozen
scenarios using this generator evidence, including the adverse cases. Measured
policy assigns passed, review-required, or failed dispositions where a policy is
preregistered; fixed outliers remain unclassified. Execution-gate success does
not mean every scenario has a passing disposition. The bounded nominal generated
reconciliation now passes. These artifacts still
do not define every setting of a complete canonical solver invocation or support
product claims. The preliminary v1 report remains preliminary and its immutable
microversion pin, which has no named version at that pin, cannot substitute for
the named v2 version evidence.

[asymmetric]: https://cad.onshape.com/documents/abe74b2a00c7303f2356788d/m/39794f9b977f321a8a665d98/e/fd2155c812c01bcadc82f202
[axisymmetric]: https://cad.onshape.com/documents/abe74b2a00c7303f2356788d/m/39794f9b977f321a8a665d98/e/544c0b40e30344e3475caf3a
[fixture-document]: https://cad.onshape.com/documents/abe74b2a00c7303f2356788d/m/39794f9b977f321a8a665d98
[generator]: ../../../../experiments/generate_stepped_rotational_v1.py
[v2-asymmetric]: https://cad.onshape.com/documents/1b68f4b8f4a69c6b59d7616e/v/69ef588c1fee61aa2e65745c/e/9cd727f2ffb7eb665c99ed7f
[v2-axisymmetric]: https://cad.onshape.com/documents/1b68f4b8f4a69c6b59d7616e/v/69ef588c1fee61aa2e65745c/e/3b0748b60dddef666359609b
[v2-document]: https://cad.onshape.com/documents/1b68f4b8f4a69c6b59d7616e/v/69ef588c1fee61aa2e65745c
[run2-asymmetric]: https://cad.onshape.com/documents/1b68f4b8f4a69c6b59d7616e/v/3edab4a4e7521986ca01b160/e/834df2af620335b532c68169
[run2-axisymmetric]: https://cad.onshape.com/documents/1b68f4b8f4a69c6b59d7616e/v/3edab4a4e7521986ca01b160/e/32cee5c10f33711f9778c52c
[run2-document]: https://cad.onshape.com/documents/1b68f4b8f4a69c6b59d7616e/v/3edab4a4e7521986ca01b160
[run1-evidence]: ../../../../experiments/track5-cad-evidence-run-01-backfill
[run2-evidence]: ../../../../experiments/track5-cad-evidence-run-02
[suite-evidence]: ../../../../experiments/track5-cad-evidence-suite
[track5-verifier]: ../../../../experiments/track5_onshape_cad_repro.py
[solver-evidence]: ../../../../experiments/generated-solver-evaluator-v1-evidence.json
[reconciliation-source]: ../../../../experiments/generated_track5_cad_reconciliation_v1.py
[reconciliation-evidence]: ../../../../experiments/generated-track5-cad-reconciliation-v1-evidence.json
