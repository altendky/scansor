# Browser hosting investigation

**Research finding and selected experiment host.** Primary provider documentation
was verified on 2026-10-13 for
[issue #204](https://github.com/altendky/scansor/issues/204). The maintainer has
selected GitHub Pages for the initial bounded experiment, with plain hosting
supported and cross-origin isolation optional. This matches Mujou's manual
deployment pattern. Cloudflare Workers Static Assets remains a future alternative
with documented response-header controls, not a selected deployment. This choice
enables neither publication nor DNS changes. HTTP/native operation remains
another deployment option, and browser fitting does not select a browser-only
product architecture.

## Requirements and cancellation decision

The [selected cancellation policy](../../../../experiments/browser_runtime/cancellation/decision.md)
uses polite Cancel, a separate explicit Force action, and optional cross-origin
isolation. Plain hosting must remain usable: Cancel fences unfinished publication
immediately, but waits for natural computation checkpoints. Force terminates and
recreates the worker only when explicitly requested. Isolated hosting may add
Pyodide's shared interrupt buffer; it does not guarantee immediate interruption
of opaque OCCT calls. No automatic cancellation escalation is permitted.

Pyodide [documents](https://pyodide.org/en/stable/usage/keyboard-interrupts.html)
that shared-buffer interrupts require a worker and appropriate security headers;
C extensions must check Python signals. Hosting selection therefore need not
block portable application assembly or the worker implementation.

## Provider comparison

| Requirement | GitHub Pages | Cloudflare Workers Static Assets |
| --- | --- | --- |
| Delivery pattern | Manual GitHub Actions publishing, matching Mujou | Same portable artifact, additional provider configuration and deployment credentials |
| Optional isolation | Native COOP/COEP configuration not verified | Documented `_headers` customization |
| Asset limits | Published site at most 1 GB; soft bandwidth limit 100 GB/month | 25 MiB per file; 20,000 files on Free, 100,000 on Paid |
| Browser caching | No native custom cache-header configuration verified | ETag and revalidation defaults; custom cache headers supported |
| Rollback | Explicitly redeploy a retained, previously verified complete artifact | Documented version rollback; last 100 published versions |
| Custom domain | Existing DNS provider can remain | Workers Custom Domains require an active Cloudflare zone |
| Commercial direction | Excludes sites primarily providing commercial SaaS | Reviewed Developer Platform terms contain no corresponding commercial-SaaS exclusion; general terms and resource limits still apply |

These constraints come from [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits),
[Workers asset limits](https://developers.cloudflare.com/workers/platform/limits/),
[Workers static billing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/),
[headers](https://developers.cloudflare.com/workers/static-assets/headers/),
[rollback](https://developers.cloudflare.com/workers/versions-and-deployments/rollbacks/),
[custom domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/),
and [Developer Platform terms](https://www.cloudflare.com/service-specific-terms-developer-platform/).
Static asset requests are free and unlimited on Workers; requests that invoke
server Worker code have separate quotas. Browser Python execution does not run
on Cloudflare's servers or inherit their execution-memory limit.

The [GitHub COOP/COEP discussion](https://github.com/orgs/community/discussions/13309)
remains unanswered, including reports in August 2026. This supports treating
native header configuration as unverified, not asserting that every possible
proxy or service-worker arrangement is impossible. No isolation workaround is
selected. Cloudflare [recommends Workers for new projects](https://developers.cloudflare.com/pages/),
so its newer static-asset offering is the bounded alternative considered here.

## Asset size and runtime evidence

The [runtime validation](../../../../experiments/browser_runtime/validation.md)
records 18,336,703 bytes of numerical assets and an optional OCP wheel of
22,714,552 bytes. The assembled clean #208 application was inspected on
2026-10-14 UTC: its manifest records signed source revision
`691476d4d5b2975e28737992396d0722cfb87ccc`, 114 listed
assets totalling 47,646,824 bytes, plus a 39,072-byte site manifest. This is
47,685,896 bytes of listed files and manifest, including application/runtime
source, fixtures, reference results and notices. The largest listed file is the
same OCP wheel; it fits Cloudflare's 25 MiB file limit with 3,499,848 bytes of
headroom. Serving its approximately 152.2 MB expanded contents as one asset would
exceed that alternative's limit. This candidate's inventory is below Pages'
published-site limit. Its recovery probe passed on the clean artifact, but it is
not a public release or final workload/size budget.
Repeated verification and loading requests are not unique download size.

File size is not browser memory usage: unpacked virtual files, Wasm allocations,
Python arrays, JavaScript display arrays and GPU storage add separate costs.
Measured local runtime initialization is not a WAN startup budget. Bounded UI
startup, fitting, CAD and recovery have passed in Chromium; realistic scan
memory, other browser engines and cache reuse remain validation work. See the
[recovery evidence](../../../../experiments/browser_static/recovery-validation.md).
The full production artifact must be checked for total
size, largest file, MIME types, network origins and clean-profile startup.

## Mujou reference and portable artifact

Mujou was inspected at `bb80bd325cd893cd3423504599aa9d8aa4111f24`, including its
AGENTS.md and project documentation. Its pre-existing modified mise.lock was
untouched. Its manual workflow assembles landing, application and documentation,
then uploads and deploys the artifact. Its preview serves the equivalent layout:

[Manual deployment workflow](https://github.com/altendky/mujou/blob/bb80bd325cd893cd3423504599aa9d8aa4111f24/.github/workflows/deploy-github-pages.yml#L1-L96)

[Local preview layout](https://github.com/altendky/mujou/blob/bb80bd325cd893cd3423504599aa9d8aa4111f24/scripts/preview.sh#L47-L104)

[Mujou deployment requirements](https://github.com/altendky/mujou/blob/bb80bd325cd893cd3423504599aa9d8aa4111f24/docs/src/project/requirements.md#L70-L148)

Scansor can reuse `/` for a landing page and `/app/` for the complete application,
worker, source bundle and retained example. Runtime assets should be same-origin
and independently versioned by the manifest or content digest. Correct
`application/wasm` serving is required. Mujou's embedded worker bytes, external
analytics and mdBook build are not Scansor requirements. Documentation hosting
can be added separately when its build exists.

Keep entry HTML and release selection fresh; only immutable asset URLs receive
long browser caching where supported. Publish coherent manifests and assets,
retain assets needed by existing tabs, and do not change runtime versions within
a running worker. Rollback must restore the matching entry, manifest, worker,
Python sources and runtime assets. Retain complete verified releases rather
than depending on transient CDN caches or provider rollback history alone.
Check reload, old active tabs, lazy CAD loading and rollback with the real UI.
Offline caching and service-worker update behavior remain separate work.

## Headers, CSP and browser origins

For isolated deployments, use COOP `same-origin` and COEP `require-corp` on the
relevant document and worker responses, and verify `crossOriginIsolated` plus
shared-buffer messaging in the actual browser. Serve runtime, UI libraries,
examples and workers from the same origin. If external assets are introduced,
their CORS/CORP behavior and fetch modes require validation; owning scansor.app
does not settle those requirements. [COOP](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Opener-Policy)
and [COEP](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Embedder-Policy)
are response policies, not substitutes for application cancellation semantics.

Headers such as `X-Content-Type-Options: nosniff` and an explicit referrer policy
can accompany a tested CSP on a header-capable host. Determine CSP from the
assembled application rather than copying a generic `script-src 'self'` policy:
Wasm execution requires an appropriate script permission, inline import maps or
scripts need hashes or another permitted mechanism, and worker creation and
asset fetches have their own directives. Prefer `wasm-unsafe-eval` when sufficient;
do not assume it covers any JavaScript code generation in the pinned runtime.
Audit Blob/data URLs used for downloads or previews and add only permissions
actually needed. A Blob worker, if adopted, also changes worker-source policy.
These are untested policy implications, not a verified CSP configuration.
See [script-src](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/script-src),
[worker-src](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/worker-src)
and [CSP delivery](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CSP).
An HTML meta CSP can cover some plain-host protections, but cannot supply
COOP/COEP or all header-only CSP functions. Test both plain and isolated local
previews; no third-party account, analytics, or asset origin is required here.

## Domain, publication and selection gates

No existing scansor.app DNS records, registrar, verification state, hosting
account or TLS configuration were assumed or changed. For a later authorized
Pages deployment, verify the domain at account level with GitHub's supplied TXT
record, configure repository Pages source as Actions and set scansor.app as its
custom domain, then configure apex A/AAAA or ALIAS/ANAME records. An optional
www CNAME points to altendky.github.io without a repository path. Verify DNS and
TLS, then enforce HTTPS. Actions publishing ignores CNAME files. [Domain
management](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site)
and [verification](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/verifying-your-custom-domain-for-github-pages)
define the ordering. A Cloudflare choice requires its separately approved
zone/domain and certificate setup instead.

Use a manual deploy job consuming the verified artifact; build/test jobs should
not require publication credentials. Pages uses `pages: write`,
`id-token: write` and the github-pages environment. [Workflow requirements](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
document those permissions. A Cloudflare token must be narrowly scoped and kept
out of browser assets and logs. First deployment must include smoke and rollback
checks and must wait for separate external-configuration/publication authority.

The runtime manifest deliberately reports `redistribution_ready: false`.
[Redistribution gaps](../../../../experiments/browser_runtime/redistribution.md)
must be closed before either provider distributes it, including retained
notices and applicable corresponding source/build/relink provisions. Existing
OCCT approval does not erase those obligations or approve alternative bindings.

The provider-selection gate is resolved: GitHub Pages is selected for the
initial bounded experiment, with plain hosting and optional isolation. The
remaining publication gates are completion of redistribution obligations and
application release validation, followed by separate authority for provider
setup, DNS and first publication. Selecting Pages grants none of that external
configuration or publication authority. This experiment selection does not
select the host for a future commercial SaaS product. Preserve portable static
assembly, worker operation and the existing HTTP/native deployment; reconsider
hosting if measured requirements or the product's commercial direction change.
