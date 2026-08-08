# Repository and Development Tooling

## Status

**Provisional, updated 2026-08-08.** The internal CLI fixture now has locked mise
and uv bootstrap, repository checks, terminal-only coverage, and baseline GitHub
Actions CI. Dependency updating, distribution packaging, and release machinery
remain absent. This bounded repository fixture does not select a production
stack, public package shape, or supported platform matrix.

## Tool Ownership

Use [mise][mise] as the outer tool-version manager. It provisions exact CPython
`3.12.13` and `3.13.15`, uv `0.11.2`, pre-commit `4.6.1`, and Lychee `0.24.2`
from `mise.toml` and `mise.lock`. These Python versions validate only the current
fixture declaration; they are not a product or public-package support promise.
Mise provisions the selected interpreters, uv, pre-commit, and non-Python tools
while [uv][uv] owns Python project environments, dependencies, locking,
synchronization, and command execution. Ruff, basedpyright, pytest, Hypothesis,
and pytest-cov should be uv-managed project dependencies. Invoke Ruff and
basedpyright through `uv run`; invoke pytest as the test runner, with Hypothesis
providing property-test integration and pytest-cov providing coverage integration.
Pre-commit-managed hook environments may own hook-specific tools such as
markdownlint-cli2; system hooks may invoke mise-provisioned tools such as Lychee.
Do not install the same tool through multiple owners without a concrete reason.

The current CI runner is Ubuntu only. Development, product, and public support
matrices remain open. The mise lock includes the platforms supported by its lock
generator for reproducibility, not a claim that Scansor validates those systems.

## Local Quality Gates

Install and validate the outer toolchain, then synchronize either fixture
interpreter from the uv lock:

```console
mise install --locked
mise install --locked --dry-run
uv sync --locked --python 3.12.13
uv sync --locked --python 3.13.15
```

Use [pre-commit][pre-commit] as the local repository-quality orchestrator:

```console
pre-commit run --all-files --show-diff-on-failure --verbose
```

Its blocking scope includes:

- focused repository hygiene plus JSON, TOML, and YAML syntax
- Markdown linting with [markdownlint-cli2][markdownlint], retaining bounded
  prose lines while exempting tables only
- offline internal relative-link and heading-fragment checking with
  [Lychee][lychee]
- GitHub Actions syntax and security checks with actionlint and zizmor
- locked mise dry-run validation when mise declarations change

Run the Python gates directly through uv:

```console
uv run --locked --python 3.12.13 ruff format --check .
uv run --locked --python 3.12.13 ruff check .
uv run --locked --python 3.12.13 basedpyright
uv run --locked --python 3.12.13 pytest
uv run --locked --python 3.12.13 pytest --cov=scansor --cov-report=term-missing
uv run --locked --python 3.13.15 pytest
```

Ruff checks every applicable Python file under the repository root. Basedpyright
analyzes `src`, `tests`, and `experiments`; the committed baseline records the
existing diagnostics exposed by expanding beyond `src`, so new diagnostics fail
without misrepresenting existing dynamic test and experiment seams as resolved.
Ruff's formatter does not enforce prose/comment line length, so the separate
`E501` lint rule is disabled; `RUF100` is also disabled because suppressions may
refer to security rules outside the selected Ruff rule set.

The current `pyproject.toml` deliberately sets `testpaths = ["tests"]`, so
`uv run pytest` runs only the internal package/CLI fixture suite. Retained
generated solver, Track 5, and reconciliation scripts use separate pinned
offline commands and exact-runtime requirements documented by their planning and
evidence pages. No aggregate repository test command is selected yet, and the
package pytest count must not be presented as covering those regressions.
The CLI suite invokes `python -m scansor` in subprocesses to exercise actual
Cyclopts CLI, `SCANSOR_*` environment, and explicit TOML resolution rather than
testing a hand-assembled approximation of precedence.

Internal relative-link and heading-anchor failures block. External links are
checked with bounded retries, concurrency, redirects, and timeouts by the manual
`lychee-external` pre-commit hook. CI runs that hook as an advisory
`continue-on-error` step, so transient network, rate-limit, and remote-site
failures remain visible without blocking `all`. A future requirement may select
a narrower blocking external-link policy.

Use [pytest][pytest] for examples and ordinary state/value tests,
[Hypothesis][hypothesis] for properties where generated cases add evidence, and
[pytest-cov][pytest-cov] for initial coverage reporting. Coverage should reveal
untested behavior; no percentage threshold is selected.

The Hamster and onshape-mcp markdownlint precedents disable MD013 globally for
semantic line breaks. Scansor should retain its general bounded prose-line
intent. If compact comparison tables cannot satisfy that limit readably, the
future configuration should exempt table lines specifically, such as with
`MD013.tables: false`, rather than disabling MD013 for all Markdown.

## Test Seams

Prefer Sans-IO cores where protocol or adapter logic can be separated cleanly
from transport. Inject clocks, randomness, identifiers, environment access, and
other nondeterminism that affects observable behavior. Prefer ordinary
value/state assertions, recording fakes at owned boundaries, and real loopback
transport tests over interaction-heavy mocking.

This is not a repository-wide prohibition on mocks or test doubles. Framework
edges, vendor SDK callbacks, process boundaries, and otherwise impractical
failure paths may need focused doubles. Tests should preserve the distinction
between evidence about owned logic and evidence about a real integration.

## Observed Repository Precedent

**Research finding, source snapshots dated 2026-07-25.** At commit
`f51614a751655cb7c9b791897ba1aca4b427c923`, Hamster uses a thin CI orchestrator
with local reusable workflows, PR concurrency cancellation, aggregate checks,
and separate pre-commit and Python jobs ([orchestrator][hamster-ci], [Python
workflow][hamster-python-ci]). Its Python workflow runs mise, uv, Ruff, mypy, a
minimum/latest Python matrix, pytest, and coverage; Scansor's provisional
adaptation substitutes basedpyright for mypy.

At that Hamster commit and onshape-mcp commit
`c4a9a4a394e977522aba8bcd1b7f332f290f07f2`, root Renovate configuration extends
recommended defaults, pins GitHub Actions digests, rebases only conflicts, and
enables the pre-commit manager ([Hamster configuration][hamster-renovate],
[onshape-mcp configuration][onshape-renovate]). GitHub Actions workflows schedule
the self-hosted Renovate application every six hours on GitHub-hosted runners and
also provide manual debug dispatch, empty top-level permissions, and GitHub App
authentication ([Hamster workflow][hamster-renovate-workflow], [onshape-mcp
workflow][onshape-renovate-workflow]). These observed files are precedent, not
existing Scansor configuration or evidence that the workflows have been run.

The repositories' markdownlint configurations also document their global MD013
choice ([Hamster markdownlint][hamster-markdownlint], [onshape-mcp
markdownlint][onshape-markdownlint]); Scansor's narrower table-only intent above
is a provisional adaptation, not an observed shared setting.

## Continuous Integration

**Provisional Scansor adaptation.** `.github/workflows/ci.yml` runs for pull
requests to and pushes on `main`. It delegates to local reusable mise,
pre-commit, and Python workflows:

- a thin top-level orchestrator calls local reusable `workflow_call` workflows
- the only aggregate job has job ID and displayed name `all`; it consumes every
  required reusable gate through SHA-pinned `re-actors/alls-green`
- pull-request concurrency includes the workflow reference and pull-request
  number, cancelling superseded runs without cross-PR cancellation
- third-party actions are pinned by full commit SHA
- explicit `contents: read` permissions and checkout
  `persist-credentials: false` keep access read-only
- Ubuntu setup flows from mise to uv and then `uv run`
- pre-commit and Python validation remain separate gates
- Python tests use a fail-fast-disabled `3.12.13`/`3.13.15` fixture matrix
- basedpyright replaces Hamster's mypy choice
- Python `3.12.13` reports package coverage to the terminal only, without a
  threshold, upload, retained artifact, or support claim

Do not import Home Assistant validation, release, deployment, recovery, generated
matrix, or other project-specific workflows before Scansor has corresponding
requirements. Experiment execution, releases, deployment, and cross-platform
jobs remain outside this baseline.

## Dependency Updating

**Provisional Scansor adaptation.** Use Renovate as a future repository direction
following the observed onshape-mcp/Hamster shape:

- root `renovate.json5` extending `config:recommended`
- GitHub Actions digest pinning
- rebasing only when conflicted
- the pre-commit manager enabled
- a GitHub Actions workflow that runs self-hosted Renovate every six hours on a
  GitHub-hosted runner, plus manual dispatch with a debug option
- minimal workflow permissions and GitHub App authentication
- synchronization rules only for version coupling demonstrated by the repository

No automerge policy is inferred or selected. Post-upgrade commands and grouped
updates must remain narrowly scoped and auditable; merely finding the same tool
name in two files does not prove that their versions must move together.

## Packaging, Documentation, and Release Deferrals

The internal CLI fixture uses `uv_build` only to expose its source package and
console entry point in the uv-managed development environment. This is not a
production build-backend preference or distributable package-shape decision.
Native component strategy, executable bundling, installer, release/deployment
tooling, documentation generator, tox or Nox orchestration, production dependency
policy, supported Python versions, and platform support remain deferred until
concrete prototype or distribution requirements exist.

Project knowledge should remain directly readable as Markdown. Pre-commit
Markdown and link checks do not imply a generated documentation site. A future
generator comparison should begin only when navigation, publication, versioning,
search, or generated API-reference requirements justify it.

## Required Validation Evidence

The implemented baseline is locally reproducible with the commands above. Its
required evidence is:

- locked mise dry-run and uv lock/sync under both fixture interpreters
- repository-wide Ruff format/lint and applicable broad basedpyright analysis
- package pytest under both fixture interpreters and terminal coverage under one
- all blocking pre-commit checks over all tracked files, with external-link
  failures reported separately as advisory
- retained experiment pin updates and their documented offline verification when
  repository-wide formatting changes those sources
- independently visible mise, pre-commit, Python format/lint/type, and Python
  matrix-test failures, all consumed by the stable `all` gate
- full-SHA action pins, explicit read-only permissions, non-persisted checkout
  credentials, and PR-safe concurrency
- internal-link blocking and bounded advisory external-link checking

Completing these checks would validate a repository configuration, not an
implementation, supported release process, or production deployment model.

[hypothesis]: https://hypothesis.readthedocs.io/en/latest/
[hamster-ci]: https://github.com/altendky/hamster-mcp/blob/f51614a751655cb7c9b791897ba1aca4b427c923/.github/workflows/ci.yml#L1-L32
[hamster-markdownlint]: https://github.com/altendky/hamster-mcp/blob/f51614a751655cb7c9b791897ba1aca4b427c923/.markdownlint-cli2.yaml#L1-L24
[hamster-python-ci]: https://github.com/altendky/hamster-mcp/blob/f51614a751655cb7c9b791897ba1aca4b427c923/.github/workflows/reflow-python.yml#L1-L43
[hamster-renovate]: https://github.com/altendky/hamster-mcp/blob/f51614a751655cb7c9b791897ba1aca4b427c923/renovate.json5#L1-L36
[hamster-renovate-workflow]: https://github.com/altendky/hamster-mcp/blob/f51614a751655cb7c9b791897ba1aca4b427c923/.github/workflows/renovate.yml#L1-L35
[lychee]: https://lychee.cli.rs/
[markdownlint]: https://github.com/DavidAnson/markdownlint-cli2
[mise]: https://mise.jdx.dev/
[pre-commit]: https://pre-commit.com/
[pytest]: https://docs.pytest.org/en/stable/
[pytest-cov]: https://pytest-cov.readthedocs.io/en/latest/
[onshape-markdownlint]: https://github.com/altendky/onshape-mcp/blob/c4a9a4a394e977522aba8bcd1b7f332f290f07f2/.markdownlint-cli2.yaml#L1-L22
[onshape-renovate]: https://github.com/altendky/onshape-mcp/blob/c4a9a4a394e977522aba8bcd1b7f332f290f07f2/renovate.json5#L1-L74
[onshape-renovate-workflow]: https://github.com/altendky/onshape-mcp/blob/c4a9a4a394e977522aba8bcd1b7f332f290f07f2/.github/workflows/renovate.yml#L1-L35
[uv]: https://docs.astral.sh/uv/
