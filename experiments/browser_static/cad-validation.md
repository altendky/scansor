# Browser CAD integration validation

This records the bounded #207 integration, not general scan ingestion or a
promise of downstream CAD compatibility. Native HTTP operation remains available.
The implementation baseline is `ec4cf0119281b5d72ce7b73307bb75c7ec23ddd3`
plus the #207 changes. Provisional artifacts report dirty inputs explicitly.

## Runtime and implementation

The browser uses Chromium 156.0.8078.4 on Linux, Pyodide 314.0.7, Python 3.14.2,
NumPy 2.4.6, Pydantic 2.12.5 and the investigated OCP.wasm 8.0.1.1 wheel.
Native verification uses Python 3.12.14, NumPy 2.5.1, Pydantic 2.13.4 and
the pinned native OCP 8.0.1.1. No alternative binding or new dependency is added.

Numerical startup retains the 30-action nozzle recipe and does not request or
import OCP. A CAD operation verifies/extracts the pinned wheel and checks its
version before enabling CAD in the Python workspace. A failed CAD load remains
failed until reload, while numerical operations remain available. Requests stay
serialized; no native shapes cross the worker boundary.

| Capability | Existing implementation reused | Validation boundary |
| --- | --- | --- |
| Intersections and manual faces | Feature graph, surface extents, OCP and general face geometry | Actual retained-nozzle UI plus synthetic geometry cases |
| Guided/batch face authoring | Guided face candidates and face builder | Native adapter transactions and browser operation probes |
| Body assembly | Body geometry and graph readiness | Conservative UI diagnostics; synthetic closed hollow Body |
| STEP/ZIP export | Nozzle CAD exporter and its readiness checks | UI face bundle plus native reopening of browser artifacts |
| Replay ownership | Native replay scopes | Serial reuse, new-operation reconstruction, exception cleanup and fresh workers |

Face collections remain separate faces. A solid requires an explicitly validated
Body; an open assembly is not healed into a solid. Legacy targeted export retains
its existing native semantics. Non-target exports retain required-root and
failure checks, including face review owners and transforms. Export does not
refit or change the recipe. Source identity, physical boundaries, units, labels,
transforms and declared assembly tolerances retain their existing meanings.

## Executed browser evidence

`validate-cad.mjs` serves the complete verified static artifact at a nested URL
with no Python API server. The retained nozzle's real UI authors a shared
intersection and a bounded plane face named `肩面 Ø12 — café`, evaluates it,
displays tessellation and downloads a Centimeters STEP ZIP with reference mesh.
The worker copies ZIP bytes from a Python buffer into owned JavaScript storage,
releases the view/proxy and clears the retained Python bytes before transferring.
The client resolves only after the worker confirms its sender buffer is detached.

A stale export returns status 409 without changing the accepted graph; a
subsequent valid export succeeds. The bundle is reopened with native OCP and
its metadata/recipe, geometry, Unicode name and units are checked. The reference
PLY retains float64 positions and source topology; transformed coordinates
differed from the expected source transform by at most 3.55e-15.

The UI also creates a Body from the single bounded face. Its declared tolerance
is explicitly set to 2e-6, above that retained face's approximately 9.47e-7
tolerance, so the test reaches the open-shell check. The graph reports an open
boundary with source-linked diagnostics; the inspector displays the diagnostic
and Body export disables Download ZIP. The stricter default declaration was
also rejected. No sewing tolerance or production validation rule was changed.

Normal worker operations exercise face candidates, preview and application on
the retained cone/plane: one candidate and one bounded managed plane are
produced and evaluated. Stale preview/application requests return status 409
without changing the accepted graph. This verifies the new operation bridge,
not merely the manual face-edit path.

Separate synthetic production-function cases cover cylinder and positive,
negative and shallow cone sides, planar holes, an oblique ellipse, a periodic
band, a valid closed hollow Body, an open-assembly rejection and a uniform
transform. Browser STEP files are reopened natively in Millimeters, Centimeters
and Meters. These are deterministic fixtures, not a claim that the UI authored
a closed Body from the captured nozzle.

The complete 18-case comparison checked 5,908 numeric fields with relative and
absolute tolerance 1e-8. Maximum native/browser difference was 4.55e-13 in a
periodic-band area. Repeated execution and a fresh worker matched exactly.
Replay checks verify same-scope/nested reuse, new-operation reconstruction,
cleanup after an exception and reconstruction from a disposed copied context.
The original two thread-based ownership tests remain native tests: browser
thread creation is incompatible, and serial/fresh-worker ownership checks
exercise the browser execution model without silently skipping them.

Additional Body ZIP fixtures call the production exporter on an explicitly
injected synthetic snapshot: a seven-node source-bound scaffold, four hollow
faces and a validated Body, followed by a scale-two/rotation/translation
transform. Browser bundles in all three unit encodings reopen as one valid
solid with four faces and volume 72π in the corresponding coordinate units.
Source/recipe/request, transform, fingerprint and tolerance metadata match;
the Centimeters bundle's float64 reference mesh preserves topology and its
transformed positions differ by at most 7.11e-15. Failed/open Body records and
changed tolerance are refused. These checks validate actual browser Body ZIP
export; they do not represent UI-derived closed nozzle geometry.

The harness intercepted the optional wheel with HTTP 404. CAD failed with an
actionable error, the accepted recipe remained unchanged, and numerical graph
evaluation still succeeded. All applicable checks had zero page errors,
external requests and computation API calls. Artifact re-verification passed.

The full static CAD driver passed all eleven checks. Separate numerical UI
editing/replay still passed all eleven checks on the CAD profile; the root and
nested startup/failure driver passed eight cases in each isolation mode. The
original numerical/CAD runtime smoke and native STEP reopening also passed.
Local checks passed 56 scoped Python tests, 312 JavaScript checks, eighteen
worker lifecycle tests and configured types with zero errors/warnings.

Early probe failures were corrected in the harness: the default Original scan
coordinates placeholder is disabled and must be asserted rather than selected;
loop faces require the production face-record dispatcher; and geometric bounds
use the existing optimal-bound method instead of adding kernel tolerance margins.
No comparison tolerance was relaxed to accommodate those mistakes.

## Reproduction and limits

Build a CAD-profile runtime/site using the static README, then run
`validate-cad.mjs` with fresh external report output and the existing native
interpreter. Reports preserve the site digest, exact versions, request inventory,
browser/native case records, STEP/ZIP files, comparisons and fault injection.
No disposable session path is a prerequisite. See the [static README](README.md)
for commands and output guards.

This does not demonstrate general uploaded sources, arbitrary CAD families,
large-scan capacity, peak memory, practical network startup, offline operation,
additional engines or immediate cancellation inside OCCT. Production cancellation
and recovery remain #208; workload limits remain #209. Native shape/proxy and
virtual-file cleanup are exercised, but this is not a total-memory or leak-budget
claim. Redistribution remains blocked by the recorded runtime/UI notice and
source obligations; a selected host is not publication authorization.
