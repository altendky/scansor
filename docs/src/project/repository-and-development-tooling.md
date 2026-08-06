# Repository and Development Tooling

## Status

**Provisional, updated 2026-07-31.** A minimal internal CLI-fixture project now
uses uv locking, Ruff, basedpyright, and pytest. Mise, pre-commit configuration,
coverage, CI, dependency updating, distribution packaging, and release machinery
remain absent. This bounded repository fixture does not select a production
stack, public package shape, or supported platform matrix.

## Tool Ownership

Use [mise][mise] as the outer tool-version manager. It should provision the
selected Python interpreters, uv, pre-commit, and standalone non-Python tools
while [uv][uv] owns Python project environments, dependencies, locking,
synchronization, and command execution. Ruff, basedpyright, pytest, Hypothesis,
and pytest-cov should be uv-managed project dependencies. Invoke Ruff and
basedpyright through `uv run`; invoke pytest as the test runner, with Hypothesis
providing property-test integration and pytest-cov providing coverage integration.
Pre-commit-managed hook environments may own hook-specific tools such as
markdownlint-cli2; system hooks may invoke mise-provisioned tools such as Lychee.
Do not install the same tool through multiple owners without a concrete reason.

Exact Python versions and the supported development, CI, and product platform
matrices remain open. Once versions are selected, validate recreation from a
clean environment and keep declarations synchronized only where the same version
is demonstrably required in more than one place.

## Local Quality Gates

Use [pre-commit][pre-commit] as the local quality-gate orchestrator. Its initial
scope should include:

- focused repository hygiene checks
- Markdown linting with [markdownlint-cli2][markdownlint]
- Markdown link checking with [Lychee][lychee]

Run Ruff formatting and linting, basedpyright, and the pytest runner as uv project
commands. Pytest loads the Hypothesis and pytest-cov integrations where relevant.
These commands may have convenience local hooks later, but CI should not rerun
them inside the separate pre-commit gate.

The current `pyproject.toml` deliberately sets `testpaths = ["tests"]`, so
`uv run pytest` runs only the internal package/CLI fixture suite. Retained
generated solver, Track 5, and reconciliation scripts use separate pinned
offline commands and exact-runtime requirements documented by their planning and
evidence pages. No aggregate repository test command is selected yet, and the
package pytest count must not be presented as covering those regressions.
The CLI suite invokes `python -m scansor` in subprocesses to exercise actual
Cyclopts CLI, `SCANSOR_*` environment, and explicit TOML resolution rather than
testing a hand-assembled approximation of precedence.

Internal relative-link and heading-anchor failures should block. Treatment of
external-link timeouts, rate limits, redirects, and transient failures remains
open and needs a documented policy before those failures block work.

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

**Provisional Scansor adaptation.** Assume GitHub hosting and GitHub Actions for
this future automation; that assumption remains unvalidated. Adapt the observed
Hamster shape rather than copying product-specific jobs:

- a thin top-level orchestrator calls local reusable `workflow_call` workflows
- stable aggregate jobs provide durable required-check names
- pull-request concurrency cancels superseded runs
- third-party actions are pinned by full commit SHA
- setup flows from mise to uv and then `uv run`
- pre-commit and Python validation remain separate gates
- Python validation covers the eventual minimum and latest supported versions
- basedpyright replaces Hamster's mypy choice

Do not import Home Assistant validation, release, deployment, recovery, generated
matrix, or other project-specific workflows before Scansor has corresponding
requirements. Exact operating systems, Python versions, required checks, and
artifact handling remain open.

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

Before relying on the future repository setup, verify:

- clean mise and uv bootstrap on selected development and CI systems
- lock-file recreation and minimum/latest Python dependency resolution
- parity between documented local commands and CI commands
- eventual `uv run ruff format --check .` and `uv run ruff check .` gates; the
  current fixture checks `src tests`, while retained experiments remain under
  their separate pinned regression commands
- `uv run basedpyright`
- the `uv run pytest` runner with Hypothesis properties and pytest-cov reporting,
  without an accidental percentage gate
- separate pre-commit, format/lint, type, and test failure reporting
- stable aggregate checks and pull-request concurrency behavior
- full-SHA action pinning and minimal workflow permissions
- Renovate dry-run behavior, GitHub App permissions, scheduling, debug dispatch,
  pre-commit updates, and only demonstrated synchronization rules
- internal-link blocking and the selected external-link failure policy

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
