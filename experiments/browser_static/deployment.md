# Manual GitHub Pages deployment

**Published bounded experiment:** <https://scansor.app/> and its
[/app/](https://scansor.app/app/) interface. The manual GitHub Pages workflow
verifies an already-tested main CI artifact; it does not rebuild the application.
Future publication and external configuration changes still require authority;
merging preparation alone does not grant it. The existing HTTP/native deployment
remains available.

## Initial publication record

The user authorized publication, verified the account domain and saved the apex
DNS records on 2026-10-14. The initial release selected source
`cb2ead3b4d611c76aa0dbf014a4ca0e8e60881a6`, successful main CI
[run 38624257462](https://github.com/altendky/scansor/actions/runs/38624257462),
artifact `11924122131`. The 101,726,881-byte ZIP matched its API/reviewed SHA-256:
`d2c31a2c357a1e2d3427752e69fa38a89660b48d3cd2bd4bf368568fe481b4f5`.
The site manifest matched
`e6fc5a38a3adad54fe9e0deac9517b371f80986fe1c6c1e85c966942a8a5c64a`.
The validator accepted all 20 CI steps and exactly 417 site files, 110,302,903
bytes. The artifact expires at 2026-10-28T19:39:30Z.

The final selection
[dry run](https://github.com/altendky/scansor/actions/runs/38625807024) passed.
Authorized [publication](https://github.com/altendky/scansor/actions/runs/38630640083)
then succeeded with the same tested bytes and maintainer review in the main-only
`github-pages` environment. Pages uses GitHub Actions as its source and the
publication variable is enabled. Account/repository domain verification passed;
both authoritative nameservers returned all four configured A and four AAAA
records. GitHub's domain certificate was approved and HTTPS enforced; the actual
HTTPS endpoint returned 200 and HTTP redirected to HTTPS. The certificate's
observed expiry was 2027-01-12, not a guarantee of future certificate state.

Eight actual public-site checks passed in desktop Chromium 156 on Linux between
20:35:59Z and 20:36:47Z on 2026-10-14:

- Landing, manifest identity, worker startup, nozzle fitting and Wasm MIME.
- Polite numerical Cancel preserved the recipe; explicit Force recovered the
  worker and reevaluation succeeded.
- Recipe save, reload, upload and numerical replay preserved committed actions,
  token, states and results.
- Lazy OCP loading, intersection, trimmed-face construction and tessellation
  produced a 590,237-byte export ZIP. Native OCP 8.0.1.1 read back the actual
  public STEP export, verifying the Unicode face name, centimeter units, exact
  recipe and float64 source mesh with 24,999 vertices.
- Requests used same-origin static assets without computation API or external
  fallback; there were no page errors. The secure context was true and
  `crossOriginIsolated` false. HTML used the observed Pages cache response
  `max-age=600`; custom isolation headers were absent.

The local resolver retained an earlier negative DNS result during this smoke.
The probe therefore mapped `scansor.app` to authoritative/public-DNS-verified
`185.199.108.153` within its own HTTP client and browser processes. It kept the
original hostname, SNI and full certificate validation; no global DNS change,
response mock or TLS bypass was used. Public recursive A answers were positive,
but ordinary local resolution and every IPv6 route were not demonstrated.

The prior main artifact `11921096972` passed a separate rollback-selection
[dry run](https://github.com/altendky/scansor/actions/runs/38624532024).
An actual provider rollback was not exercised. The bounded workload and browser
limitations below still apply; this is not a full-scan or WAN performance result.

## Artifact selection and dry run

Choose a completed successful **push to main** run of `.github/workflows/ci.yml`
in `altendky/scansor`, with its immutable `validated-static-browser` artifact ID.
PR artifacts, including successful test-merge artifacts, are excluded. Record
the run URL, source commit, artifact ID, ZIP SHA-256 from the artifact API,
`site/manifest.json` SHA-256, expiry and review evidence. Do not select by artifact
name alone, most recent download or branch name alone.

The manual **Manual browser deployment** workflow accepts those four IDs/hashes.
Run on `main` with `publish: false` to verify without Pages setup or publication.
The preparation job downloads the exact ZIP, reads the selected source's runtime,
UI and redistribution locks as data, checks provenance, archive digest, clean
matching sources, all 25 CI steps, readiness and exact assets. It rejects unsafe,
duplicate, hidden, linked, oversized or unlisted files. It executes no downloaded
code and publishes neither evidence nor temporary build files. Its summary is
the selection record. Source lock changes on today's main do not invalidate a
valid older release; rollback uses the selected commit's approved inventory.

Numerical-only workspace and source reports also bind to their own complete
site and runtime manifests, capability profile and manifest-listed asset bytes.
Those assets travel under `evidence/numerical-assets/` for validation, including
the numerical source bundle and redistribution records. The trusted verifier
requires a numerical-only profile there and a CAD profile for the deployed site;
both use the selected source locks. It checks driver runtime identities and
workspace capabilities against those exact manifests. Numerical proof assets
are excluded from the Pages output, and the existing archive limits remain in
force. A matching report hash alone does not establish that a run occurred.

For a local rehearsal, use the same raw `gh api` run/artifact JSON and three
raw lock files retrieved at that run's `head_sha`, in a disposable external
directory. Name them `runtime-lock.json`, `browser-redistribution-lock.json` and
`package-lock.json`. Then:

```sh
uv run scripts/validate-pages-artifact.py \
  --repository altendky/scansor --run-id "$TASK_CI_RUN" \
  --artifact-id "$TASK_ARTIFACT_ID" \
  --run "$TASK_SELECTION/run.json" --artifact "$TASK_SELECTION/artifact.json" \
  --archive "$TASK_SELECTION/artifact.zip" --archive-sha256 "$TASK_ZIP_SHA256" \
  --site-manifest-sha256 "$TASK_SITE_SHA256" \
  --source-locks "$TASK_SELECTION/locks" --output "$TASK_SELECTION/site" \
  --evidence "$TASK_SELECTION/selection.json"
node experiments/browser_static/preview.mjs --directory "$TASK_SELECTION/site"
```

Variables denote chosen paths and reviewed values, not existing session files.
The output site and evidence must be fresh and outside the checkout. Preview
the root landing and `/app/`, and repeat with `--prefix /scansor/` for the default
project URL. Correctness evidence travels beside the release and is excluded
from the Pages payload. The payload preserves every tested site byte, including
the landing page, versioned runtime, retained example, notices and source offer.
Pages packaging creates a new transport tar; it does not rebuild those bytes.

## Preparation verification

On 2026-10-14, the validator accepted actual main CI run `38618326141`, artifact
`11921096972`, source `d3146a7cbe4320c1362dba79e5f3e498ddaecb38`. The ZIP was
101,719,897 bytes and matched the reviewed/API digest:
`62d62f7c954c952fd50ea6e20bd111ba73d54dec24640be56b47e7a45b10f93e`.
The site manifest matched
`90949f20b6c9d28a7c00bf51a77c75fcf8d8313c2222762f788590c111f18ad7`.
It emitted exactly 417 site files, 110,299,962 bytes, with all 20 steps and
selected numeric/CAD/UI readiness verified. This predecessor is rollback
rehearsal evidence, not the final deployment selection with the new landing.

The 109 focused tests, full type checks, Ruff and configured workflow/security
hooks passed. A separate actual Chromium 156 probe performed nine checks against
unchanged disposable artifacts: an initialized numerical UI survived a coherent
local server release replacement, whose removed old runtime namespace returned
a real lazy-CAD HTTP 404 without response injection. The failure popup preserved
committed actions; selection edits and all 30 numerical actions still worked.
Downloaded actions matched the committed recipe. Reload/upload into the new
release reproduced recipe, token, states and results, and lazy CAD loaded again.
Root and `/scansor/` landing links reached the real application. There were no
external/API requests or page errors; both artifacts remained unchanged and all
owned browser/server processes closed.

The replacement preview had dirty source and preceded a footer-link clarification;
it is local recovery evidence, not a clean release or real Pages deployment.
After the CAD failure popup, the status text could still say it was unpacking
CAD while the runtime footer was ready; the next numerical operation reset it.
That display inconsistency is tracked in #230; committed work was preserved.
Final main CI, the workflow dry run and actual Pages/DNS/TLS checks subsequently
passed as recorded above. Provider rollback, WAN performance and other browsers
remain untested.

## Final approval record

After preparation merges, select and verify its successful **main** artifact.
Present a concrete record before changing external settings:

- Exact workflow/source/run/artifact/ZIP/site identities, expiry, verified
  payload size, local checks and acknowledged browser/workload limits.
- Enable repository Pages with **GitHub Actions** as source; configure the
  `github-pages` environment for main-only deployment and required review where
  available. Do not assume an environment name creates review protection.
- Add `scansor.app` to the account's verified domains, retrieve GitHub's actual
  TXT challenge and approve its exact value before writing it. Keep that record.
- Set repository custom domain to `scansor.app`, then apply the approved apex
  records below. Review existing DNS at the provider first and preserve unrelated
  mail, TXT and service records.
- Enable `SCANSOR_PAGES_PUBLICATION_ENABLED=true` only under this approval, then
  dispatch with `publish: true`, inspect actual TLS/DNS and enforce HTTPS once
  the certificate is available. Record the deployment URL and result.

The variable is an operational gate, not a substitute for authorization. It is
unset/disabled by default. There is no push-triggered deployment and no
`configure-pages` action that enables hosting as a side effect. Only preparation
has read access to contents/actions; only deployment has `pages:write` and
`id-token:write`. Deployments are serialized without cancelling an active one.
The upload/deploy actions are pinned, and the ordinary release workflow remains
separate. No 1Password credential or new paid service is needed for this workflow.

## Domain configuration

Preparation's read-only public DNS on 2026-10-14 returned no apex A/AAAA answers.
Nameservers were `dns1.registrar-servers.com` and `dns2.registrar-servers.com`.
This is not access
to the provider configuration or proof that other records are absent. No DNS,
domain verification, Pages setup or TLS changes were made during preparation.

The user subsequently saved the following records for the chosen apex host;
GitHub reports the domain as verified. Preserve the existing verification TXT:

| Type | Name | Configured value |
| --- | --- | --- |
| TXT | `_github-pages-challenge-altendky` | `a91e7ef99fefa6c3a7fed849c9fa75` |
| A | `@` | `185.199.108.153` |
| A | `@` | `185.199.109.153` |
| A | `@` | `185.199.110.153` |
| A | `@` | `185.199.111.153` |
| AAAA | `@` | `2606:50c0:8000::153` |
| AAAA | `@` | `2606:50c0:8001::153` |
| AAAA | `@` | `2606:50c0:8002::153` |
| AAAA | `@` | `2606:50c0:8003::153` |

Initial scope is the apex only. If separately included in the approval, add
`www` CNAME to `altendky.github.io` and test its redirect to the apex. No wildcard
or repository path belongs in that target. Verify the account domain first,
configure the repository domain before pointing traffic, then check propagation
and HTTPS. Actions publishing ignores a `CNAME` file, so the tested artifact
needs no domain-specific mutation. Recheck the current provider instructions
before executing future domain changes. See GitHub's
[domain management](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site)
and [domain verification](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/verifying-your-custom-domain-for-github-pages)
documentation, checked 2026-10-14.

## Cache, cancellation and open tabs

This Pages deployment uses the provider's caching behavior. Custom cache,
COOP/COEP or CSP response headers are not configured or promised. Runtime paths
contain the manifest hash; every release serves coherent matching files.
Client integrity checks reject mismatched bytes. Verify actual MIME and cache
responses after publication rather than treating local `no-store` preview
timings as WAN/cache measurements. Offline caching remains separate work.

Plain hosting uses polite cancellation at safe checkpoints and explicit
Force/restart. Record `crossOriginIsolated` on the actual host; false is supported
and does not automatically trigger a shared-buffer workaround. Optional isolated
local previews are separate evidence, not proof of Pages response headers.

Each deployment replaces the complete site. Old runtime directories are not
retained indefinitely. An already open numerical worker may continue until it
requests a lazy CAD asset under a removed runtime path; that request can fail.
The UI reports CAD loading failure and keeps numerical work usable. Download
the committed recipe, reload the current site and upload/replay it. Drafts and
computed results are not recovered by that recipe. Force/restart in the stale
tab alone cannot restore assets removed from the host. This is an initial
operational limitation, not an autosave guarantee; loss-of-work review is
tracked separately in #214.

## Rollback and post-publication checks

Record a known good selection before every update. Roll back by manually
dispatching the same workflow with that selection's original run/artifact/hashes
and `publish: true`. It restores the complete matching site, not just its HTML.
The validator rejects expired or changed artifacts. CI artifacts currently last
**14 days**; the Pages transport artifact lasts **one day**. Indefinite rollback
is not provided. A local copy helps review but is not accepted as a replacement
for an expired GitHub artifact. A long-term release archive is follow-up scope.
Rehearse selection and exact-byte preview of a prior retained artifact before
approving an update; do not rebuild an old release and call it the same artifact.

After an authorized deploy or rollback, record:

1. DNS answers, valid HTTPS certificate and HTTP-to-HTTPS behavior at the apex;
   optional alternate host redirect only when configured.
2. Root landing and `/app/`, same-origin worker/runtime URLs, Wasm MIME and actual
   caching/isolation responses; no missing manifest-listed assets.
3. Desktop Chromium clean-profile startup, retained nozzle display, selection,
   fitting, recipe download/upload replay, CAD face STEP/bundle download and
   readback. Keep synthetic closed-body validation distinct from nozzle UI work.
4. Progress, polite Cancel, explicit Force/restart, stale-tab lazy CAD failure
   and recipe recovery. Inspect requests: static assets only, no `/api/`
   computation or outside-origin dependency fallback.
5. Deployment workflow run, selected source/ZIP/site identities, payload bytes,
   browser/version, failures and the still-available known-good rollback record.

The [workload validation](workload-validation.md) defines the current bounded
evidence. Other engines, mobile, realistic full scans, real-network startup and
offline use remain unverified. Actual domain checks passed with the resolution
caveat above; an actual provider rollback remains unexercised. Local preview and
adversarial validator tests do not demonstrate provider rollback. GitHub's
[workflow requirements](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
and [Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
remain applicable, including the 1 GB published-site limit and commercial-SaaS
restriction documented in the
[hosting decision](../../docs/src/project/experiments/browser-hosting.md).
