# Static bootstrap validation

**Bounded experiment, not application capability validation.** Local Chromium
checks on 2026-10-14 UTC used the actual assembled HTML, React docking workspace,
versioned Pyodide worker and retained Scansor/example resources. Node served
static files only. No fitting, selection, graph evaluation, CAD construction or
recipe replay was implemented or exercised through this UI stage.

## Inputs and method

The checkout baseline was `2c6d282527cf792ec4492a9e17854f0ecd8d3c02`, with
the #205 working changes. Manifests report the dirty checkout and separate
runtime/UI revisions rather than claiming a clean release. Both builders record
file hashes; the browser runner records the site manifest digest and requests.
The runtime source ZIP is unchanged for the numerical/CAD implementation, while
its worker gains a non-computing initialization operation and a bootstrap
protocol marker. Earlier probe-only artifacts are rejected before assembly.

The browser was Chromium 156.0.8078.4 on Linux. Runtime pins remain Pyodide
314.0.7, Python 3.14.2, NumPy 2.4.6 and Pydantic 2.12.5. The approved OCP wheel
was packaged but not imported by application startup. Native baseline building
used the existing Python 3.12.14 environment, NumPy 2.5.1 and Pydantic 2.13.4;
these local versions differ from CI's pinned interpreters. The root Python
support range and dependency lock are unchanged.

The retained fixture inventory includes its 24,999 vertices, 49,994 triangles,
source identity sidecar, model, selections and cone-plane recipe. These are
packaged resources, not displayed or editable workspace data at this stage.
The initial CAD-profile site had 114 listed files totaling 47,578,605 bytes,
excluding its self-describing site manifest. That includes the optional CAD
wheel, notices and investigation resources. It is not downloaded startup size,
live memory, GPU allocation or an offline cache budget.

## Executed checks

The static runner passed six initial cases in ordinary hosting and six with
COOP/COEP isolation: runtime readiness, deliberately missing Wasm, and unsupported
backend selection at both `/app/` and `/preview/nested/app/`. Every applicable
assertion passed, with zero page errors, external requests or computation API
requests; re-verification confirmed the artifact remained unchanged. The real
docking layout control worked, while workspace authoring/evaluation/export
controls remained disabled and the status stated their unavailability.

Review found that top-level worker imports could break native startup and that
a post-ready failure could leave stale readiness. Dependencies now load inside
the guarded bootstrap, worker support loads only in worker mode, and failure
clears readiness. The final reviewed runs passed eight cases per hosting mode,
adding a fatal message after real initialization at each prefix: sixteen cases
total, again without page errors, external/computation requests or on-disk
artifact changes. The fault cases record their modified worker response hashes;
ordinary readiness cases use untouched runtime responses.

Local checks passed 28 scoped Python packaging/probe tests, all 300 JavaScript
checks, and five static server/output-guard regressions. The six worker lifecycle
tests cover initialization reuse, startup failure, timeout, close, invalid
messages and failure after readiness. Twenty-two existing native recovery and
workspace browser tests passed; the expanded four-test startup suite then passed
including worker-module independence and configuration failure. Configured
Python types reported zero errors and warnings after fixture annotations.

The first PR CI run passed 300 JavaScript checks and 98 UI cases, but a
recipe-import case timed out. Logs showed the test treating the raw HTML's
enabled Load button plus idle server as readiness before the file-change handler
existed; a local run reproduced the race in another import case. Load now starts
disabled and is enabled only after application startup. Recipe tests also wait
for initialized Save readiness. A regression holds metadata loading, verifies
Load remains disabled, then releases it and successfully imports a recipe.
All eight targeted startup/import cases passed after this correction. Fresh
full CI is required before merge; the failed run is not bypassed.

Sandboxed loopback listeners were denied; server/browser checks ran with the
authorized local-network execution permission. Sandboxed interpreter discovery
omitted installed packages; the type checker passed with access to the existing
configured interpreter. An automatic permission review timed out once; its
permitted single retry succeeded. These were environment restrictions, not
substituted successful tests. Vite's existing dependency directive warnings
remained warnings; the build completed.

## Limits

This validates a static UI and runtime bootstrap, preserving operational HTTP
startup. It does not demonstrate browser fitting/CAD integration, source display,
progress/cancellation/recovery of real edits, large-scan suitability, peak memory,
offline operation, network startup budgets, Firefox/WebKit support or deployment.
The plain and isolated checks establish startup behavior, not shared interrupt
support for all operations. Publication remains blocked by the manifest's
`redistribution_ready: false` and unresolved notice/source obligations.
