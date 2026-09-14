# GitHub Releases

Scansor remains a concept-stage prototype. Releases record source snapshots and
generated GitHub release notes; they do not publish to PyPI, package the browser
viewer, or establish an end-user distribution format.

## Prepare a Release

From a clean `main` checkout of `altendky/scansor`, run:

```sh
mise run release 0.1.0
```

The helper fast-forwards from `origin/main`, rejects local-only commits and
existing release branches/tags, synchronizes `pyproject.toml`,
`src/scansor/__init__.py`, and the local editable package entry in `uv.lock`, then
creates `release/v0.1.0` with a normal GPG-signed commit. It pushes and opens a PR
for review. Dependency resolutions remain unchanged. If a PR template exists,
supply a completed `--body-file` after the version argument.

The helper stops at the first Git, signing, or GitHub failure without retrying or
removing work. Inspect the branch and finish the normal signed commit/push/PR
steps after resolving the failure; do not restart preparation over an existing
branch. Releasing a lower version is rejected. Only `X.Y.Z` stable releases and
`X.Y.Z.devN` development versions are supported.

## Automated Lifecycle

1. After the release PR merges, the complete main CI `all` gate must pass.
2. The release App tags the validated commit `vX.Y.Z` only if it is still current
   main and all three version declarations agree. Development versions skip
   tagging. The lightweight tag is created with an App token so it triggers CI.
3. Tag CI runs the same checks. After `all` passes, publication checks the exact
   tag/commit, synchronized versions, and main ancestry, then creates a stable
   GitHub release with generated notes.
4. Only after successful GitHub publication, the release App prepares a
   `post-release/vX.Y.Z` PR advancing to the next patch `.dev0`. GitHub creates the
   commit with an expected-head guard and its signature is verified before the
   PR receives `enqueue`. Bot approval and queue policy live in Mergify.

Release jobs are downstream of `all`, so their intentional skips and mutations
do not enter the required check or create a CI dependency cycle. Workflows and
scripts both require a push in the canonical repository and
`RELEASE_ENABLED=true`. The initial `0.0.0.dev0` prevents setup from tagging the
former placeholder `0.0.0`.

## Configuration and Recovery

Install the release GitHub App for this repository. Set repository variable
`RELEASE_APP_ID`, secret `RELEASE_APP_PRIVATE_KEY`, and variable
`RELEASE_ENABLED=true` when the lifecycle is ready. The installation needs
contents and pull-request write access; tokens request contents write for
tagging/publication and additionally pull-request write for the development PR.
No registry credentials or PyPI trusted publisher is needed.

Rerun the original failed Actions run to recover publication or post-release
creation. Existing matching tags, published releases, verified development
commits, and open PRs are reused. A same-version tag at a main ancestor is left
intact while the development PR is pending. Conflicting refs, unsigned or
unexpected commits, and closed development PRs stop automation for review;
branches are never deleted, recreated, or force-pushed. If main advances during
post-release preparation, rerun against a fresh main checkout. Changing the main
version before an older release finishes makes that release's development bump
unnecessary.

Set `RELEASE_ENABLED=false` to suspend future release mutations. It does not
undo existing tags, releases, or PRs. GitHub cannot atomically compare main while
creating a separate tag; the helper rechecks main immediately before creation,
and the tag always names the commit whose checks passed.

Release safeguards are tested without network or credentials as part of normal
pytest execution:

```sh
uv run --locked pytest tests/test_release_version.py tests/test_release_github.py
uv run scripts/release-version.py check
```
