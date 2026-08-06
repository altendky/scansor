# Python Prototype Support Libraries

## Status

**Provisional, snapshot dated 2026-07-25.** This page records accepted
provisional general-support and near-domain-support directions for the first
Python evidence-generating prototype. It does not define a production stack,
and the full combined validation experiment described here does not exist. The
first internal CLI slice exercises a bounded subset using structlog, NumPy,
pytest, uv, and Ruff; HTTP, platform-default paths, property tests, coverage,
documentation tools, and the remaining combined fixture stay open.

The [foundation choices](python-foundation-libraries.md) remain unchanged:
Cyclopts owns the CLI and settings-source resolution into a plain Pydantic v2
`BaseModel`, and basedpyright is the required initial type checker with ty as a
future challenger.

## Runtime Diagnostics

Provisionally use [structlog][structlog] to create structured diagnostic events,
with standard-library `logging` interoperability where dependency or adapter
logs require it. Events should have stable names and explicit fields, preserve
exception information, support request and run correlation, and apply redaction
before reaching any renderer or sink.

Runtime logs are operational evidence, not the authoritative fit-result record.
Required run inputs, resolved non-secret settings, warnings, solver termination,
and publication state belong in the versioned application records even if some
of the same facts also appear in logs. No log transport, collector, retention
policy, or observability service is selected.

Validate plain and JSON rendering, exception chains, context isolation across
concurrent work, standard-library and dependency-log capture, secret redaction,
and deterministic event capture in tests. Also determine which event names and
fields merit compatibility guarantees before treating the logging shape as an
interface.

## Terminal Presentation

Provisionally use [Rich][rich] for human-oriented terminal tables, tracebacks,
progress, and emphasis where it materially improves legibility. Machine-readable
application data should remain a separate, stable stream and should never
require parsing Rich output. The CLI fixture must determine stdout and stderr
ownership and behavior for pipes, redirected output, narrow terminals,
`NO_COLOR`, explicit color controls, and non-interactive execution.

Do not select Textual or a terminal application framework. The initial need is
bounded presentation, not a full-screen interface.

## Filesystem and Platform Paths

Use standard-library `pathlib` for path manipulation and provisionally use
[platformdirs][platformdirs] only for platform-appropriate user configuration,
cache, state, data, and log locations. Explicit CLI and configuration paths must
remain possible, and fit semantics must not depend on an ambient cache or
platform-specific default.

Validate the intended directory categories on selected development and CI
operating-system fixtures, application and author naming, environment overrides,
directory creation and permission failures, read-only operation, and cleanup
ownership. The supported-platform scope remains open. Do not add a broader
filesystem abstraction or `fsspec` until a remote or non-local storage boundary
is demonstrated.

## HTTP and Adapter Retry Behavior

Provisionally use [HTTPX][httpx] for HTTP-based adapters because its explicit
client lifecycle, timeout model, transport hooks, and synchronous and
asynchronous APIs permit focused adapter tests. This does not decide that the
prototype itself is asynchronous or that all adapters use HTTP.

Retry policy belongs to each adapter operation rather than to an invisible
global client default. A policy must use bounded attempts and elapsed time,
explicit connect/read/write/pool timeouts, backoff with jitter, and server
guidance such as `Retry-After` where applicable. Retry only failures classified
as transient for that operation. Authentication and validation failures, most
other client errors, malformed responses, and non-idempotent publication must
fail without automatic replay. A publication operation may be retried only when
the remote contract supplies a tested idempotency or reconciliation mechanism.

Defer selecting [Tenacity][tenacity], [backoff][backoff], or a small local retry
loop until a scripted HTTP fixture demonstrates the required policy. Compare
policy visibility, typed exception history, cancellation and async behavior,
`Retry-After`, deterministic testing, and the ability to record every attempt
without leaking credentials. HTTP authentication, rate-limit coordination, and
Onshape-specific client behavior also remain adapter experiments rather than
general library decisions.

## Tests and Property-Based Checks

Provisionally use [pytest][pytest] as the test runner and [Hypothesis][hypothesis]
for property-based checks of boundary models, reduced parameterizations,
serialization, and adapter state transitions. Examples remain necessary for
specific regressions and expert-readable contracts; generated cases complement
rather than replace them.

Initial evidence should cover deterministic seeding and failure reproduction,
useful shrinking for constrained finite arrays and nested Pydantic models,
deadline behavior for solver tests, interaction with NumPy and SciPy, and
retention of minimal failing examples. Use [pytest-cov][pytest-cov] for initial
coverage reporting without a percentage threshold. Parallel test execution,
snapshot testing, and other pytest plugins remain unselected until a test suite
creates a concrete need.

Prefer Sans-IO cores, injected nondeterminism, ordinary state/value assertions,
recording fakes at owned boundaries, and real loopback transports where they
improve evidence. This is not a ban on mocks: framework and vendor SDK edges may
still need focused test doubles. The [repository tooling
direction](repository-and-development-tooling.md#test-seams) owns the broader
test and repository policy.

## Environment, Packaging, and Code Quality

Provisionally use [uv][uv] for Python environments, dependency locking,
synchronization, and command execution, and [Ruff][ruff] for focused formatting
and linting alongside basedpyright. Mise should own outer tool versions and
pre-commit should orchestrate local gates as described by the [repository and
development tooling direction](repository-and-development-tooling.md).

No distributable package shape, production build backend, executable bundler,
installer, platform support matrix, or release channel is selected. The internal
fixture uses `uv_build` only for its uv-managed source package and console entry
point. Choose a production build backend only after the prototype shows whether
Scansor has one package, multiple packages, native components, plugins, or
bundled data. Environment management for development must not be mistaken for
validated end-user deployment.

## Documentation Tooling

Continue maintaining the current project knowledge as directly readable
Markdown. Provisionally use pre-commit with markdownlint-cli2 and Lychee;
internal relative-link and heading-anchor failures should block, while policy
for external-link failures remains open. No documentation generator is selected.
The [repository tooling direction](repository-and-development-tooling.md) owns
these checks and the generator deferral. Existing documentation must not be
described as a generated site.

## Larger Numeric Payloads

Assume initially that bulk payloads are local single-file artifacts, the solver
normally reads all points in selected groups, stable observation IDs and
overlapping memberships are required, and canonical bulk storage has no initial
non-Python consumer. Keep metadata and control/audit records JSON-compatible;
they should reference immutable bulk payloads by media type, schema version,
shape, numeric representation, and content hash.

No durable production format is selected. NumPy `.npy` or `.npz` is acceptable
only as an internal prototype-fixture convenience, not as a public interchange
or archival contract. Arrow or [Apache Parquet][parquet] remain future tabular
candidates. Evaluate [Zarr][zarr] or HDF5 only if representative payloads
demonstrate chunked, out-of-core, or object-access needs. PLY, LAS, and similar
formats remain adapter formats rather than canonical application storage.

A bounded format fixture should include representative coordinates, normals,
memberships, stable observation identifiers, optional attributes, and deliberate
invalid values. Measure exact round trips of shape, dtype, ordering, and missing
or non-finite policy; schema evolution; streaming and partial reads; compression;
content hashing; corruption detection; cross-language access; and package and
deployment cost. Record payload sizes and access patterns before comparing
performance.

## Combined Validation Fixture

As a representative combined fixture, exercise one small adapter and CLI flow
that includes:

- correlated structured events, dependency-log capture, and secret redaction
- human terminal output and separate machine-readable output under TTY and pipe
  conditions
- explicit and platform-default paths, cache independence, and failure modes
- scripted HTTP timeout, transient failure, `Retry-After`, cancellation, and
  non-idempotent publication cases
- pytest examples and Hypothesis properties with reproducible minimized failures
- clean mise/uv environment recreation plus pre-commit, Ruff, and basedpyright
  gates
- markdownlint-cli2 and Lychee checks, including blocking internal links and the
  selected external-link policy
- a representative bulk numeric round trip if payload scale makes JSON
  unsuitable

The fixture should record usability, diagnostic completeness, typing friction,
dependency and packaging cost, and any behavior that would become a public or
persisted compatibility boundary. Before treating this support layer as
validated, also evaluate against every applicable detailed validation
requirement in the sections above; this checklist is not exhaustive. The
combined and category-specific evaluation would produce evidence about a
prototype combination, not select a production stack.

[backoff]: https://github.com/litl/backoff
[httpx]: https://www.python-httpx.org/
[hypothesis]: https://hypothesis.readthedocs.io/en/latest/
[parquet]: https://parquet.apache.org/
[platformdirs]: https://platformdirs.readthedocs.io/en/latest/
[pytest]: https://docs.pytest.org/en/stable/
[pytest-cov]: https://pytest-cov.readthedocs.io/en/latest/
[rich]: https://rich.readthedocs.io/en/stable/
[ruff]: https://docs.astral.sh/ruff/
[structlog]: https://www.structlog.org/en/stable/
[tenacity]: https://tenacity.readthedocs.io/en/latest/
[uv]: https://docs.astral.sh/uv/
[zarr]: https://zarr.readthedocs.io/en/stable/
