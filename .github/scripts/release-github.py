#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Release-App operations; never fetch, push, delete, or force a Git ref.

`tag` requires the main push checkout at GITHUB_SHA. `post-release --tag vX.Y.Z`
requires a successful tag release run (GITHUB_REF/GITHUB_SHA still identify the
tag), but a clean checkout of current main. GH_TOKEN must be the release App's
token. A PR template requires --body-file containing a completed matching body.
"""

import argparse
import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[2]
STABLE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
REPOSITORY = "altendky/scansor"


class ReleaseError(RuntimeError):
    """An unmet release precondition; existing remote work is left intact."""


def command(args, *, cwd=ROOT, input_text=None):
    result = subprocess.run(
        args, cwd=cwd, input=input_text, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise ReleaseError(
            f"{args[0]} {args[1]} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


class GitHub:
    def __init__(self, repository):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ReleaseError("GITHUB_REPOSITORY must be owner/repository")
        self.repository = repository

    def api(self, endpoint, *, method="GET", data=None, missing_ok=False):
        args = ["gh", "api", f"repos/{self.repository}/{endpoint}", "--method", method]
        if data is not None:
            args += ["--input", "-"]
        result = subprocess.run(
            args,
            cwd=ROOT,
            input=None if data is None else json.dumps(data),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            if missing_ok and "(HTTP 404)" in result.stderr:
                return None
            raise ReleaseError(f"GitHub {method} {endpoint}: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def contents(self, path, sha):
        item = self.api(f"contents/{quote(path, safe='/')}?{urlencode({'ref': sha})}")
        if item.get("type") != "file" or item.get("encoding") != "base64":
            raise ReleaseError(f"Cannot read regular file {path} at {sha}")
        return base64.b64decode(item["content"])

    def ref(self, name):
        return self.api(f"git/ref/{quote(name, safe='/')}", missing_ok=True)

    def target(self, ref):
        obj = ref["object"]
        for _ in range(10):
            if obj["type"] == "commit":
                return obj["sha"]
            if obj["type"] != "tag":
                break
            obj = self.api(f"git/tags/{obj['sha']}")["object"]
        raise ReleaseError("Release ref does not resolve to a commit")

    def create_ref(self, name, sha):
        try:
            self.api(
                "git/refs", method="POST", data={"ref": f"refs/{name}", "sha": sha}
            )
        except ReleaseError:
            # Another run may have created exactly this ref, or the successful
            # response may have been lost. Never replace a different target.
            existing = self.ref(name)
            if existing is None or self.target(existing) != sha:
                raise


@lru_cache(maxsize=1)
def version_tool():
    spec = importlib.util.spec_from_file_location(
        "release_version", ROOT / "scripts/release-version.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def manifest_version(content):
    return tomllib.loads(content.decode("utf-8"))["project"]["version"]


def stable_version(tag):
    if not tag.startswith("v") or not STABLE.fullmatch(tag[1:]):
        raise ReleaseError(
            "Release tag must be vX.Y.Z (without prerelease or build metadata)"
        )
    return tag[1:]


def version_command(*args):
    return command([sys.executable, str(ROOT / "scripts/release-version.py"), *args])


def clean_checkout(sha):
    if command(["git", "rev-parse", "HEAD"]).strip() != sha:
        raise ReleaseError(
            "Checkout is stale: check out the current main SHA and rerun"
        )
    if command(["git", "status", "--porcelain"]):
        raise ReleaseError(
            "Release helper requires a clean checkout, including untracked files"
        )


def release_context(environ):
    if environ.get("RELEASE_ENABLED") != "true":
        raise ReleaseError("RELEASE_ENABLED must be true")
    if (
        environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or environ.get("GITHUB_EVENT_NAME") != "push"
    ):
        raise ReleaseError("Releases require a push workflow in altendky/scansor")


def tag_release(github, environ):
    release_context(environ)
    if environ.get("GITHUB_REF") != "refs/heads/main":
        raise ReleaseError("Automatic tagging requires GITHUB_REF=refs/heads/main")
    sha = environ.get("GITHUB_SHA", "")
    main_ref = github.ref("heads/main")
    if main_ref is None or github.target(main_ref) != sha:
        raise ReleaseError(
            "This run is no longer current main; refusing to tag a stale commit"
        )
    clean_checkout(sha)
    version_command("check")
    version = manifest_version((ROOT / "pyproject.toml").read_bytes())
    if ".dev" in version:
        print(f"Version {version} is a prerelease; no tag needed")
        return False, version
    if not STABLE.fullmatch(version):
        raise ReleaseError(f"Not a stable release version: {version}")
    tag = f"v{version}"
    existing = github.ref(f"tags/{tag}")
    if existing is not None:
        target = github.target(existing)
        if target != sha:
            ancestry = github.api(f"compare/{target}...{sha}")
            if ancestry.get("status") not in {"ahead", "identical"}:
                raise ReleaseError(
                    f"Tag {tag} points to another commit outside main history; leaving it unchanged"
                )
            if manifest_version(github.contents("pyproject.toml", target)) != version:
                raise ReleaseError(
                    f"Tag {tag} points to a different package version; leaving it unchanged"
                )
        print(f"Tag {tag} already identifies version {version} on main at {target}")
        return False, version
    # Recheck main immediately before the mutation. The API cannot atomically
    # compare main while creating another ref; the tag always names validated SHA.
    if github.target(github.ref("heads/main")) != sha:
        raise ReleaseError(
            "Main advanced during validation; refusing to create the tag"
        )
    github.create_ref(f"tags/{tag}", sha)
    print(f"Created {tag} at {sha}")
    return True, version


def validate_tag(github, environ, tag):
    release_context(environ)
    version = stable_version(tag)
    if environ.get("GITHUB_REF") != f"refs/tags/{tag}":
        raise ReleaseError("Release requires the corresponding tag push workflow")
    ref = github.ref(f"tags/{tag}")
    if ref is None or github.target(ref) != environ.get("GITHUB_SHA"):
        raise ReleaseError("Release tag no longer matches this workflow's commit")
    sha = github.target(ref)
    main = github.ref("heads/main")
    if main is None or github.api(f"compare/{sha}...{github.target(main)}").get(
        "status"
    ) not in {"ahead", "identical"}:
        raise ReleaseError("Release commit must belong to main history")
    # Check every version declaration at the tag, even in a current-main checkout.
    with temporary_directory() as directory:
        root = Path(directory)
        for path in version_tool().version_files(root):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(github.contents(path.relative_to(root).as_posix(), sha))
        version_tool().check_versions(root, tag)
    return version


def publish_release(github, environ, tag):
    validate_tag(github, environ, tag)
    clean_checkout(environ["GITHUB_SHA"])
    version_command("check", "--tag", tag)
    existing = github.api(f"releases/tags/{quote(tag, safe='')}", missing_ok=True)
    if existing is None:
        existing = github.api(
            "releases",
            method="POST",
            data={
                "tag_name": tag,
                "name": tag,
                "draft": False,
                "prerelease": False,
                "generate_release_notes": True,
            },
        )
    if (
        existing.get("tag_name") != tag
        or existing.get("draft")
        or existing.get("prerelease")
        or not existing.get("published_at")
    ):
        raise ReleaseError(
            "Existing release is not a published stable release; leaving it unchanged"
        )
    return existing["html_url"]


def temporary_directory():
    parent = Path(os.environ.get("TMPDIR", "/tmp")) / "agents"
    parent.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="release-github-", dir=parent)


def pull_request_body(version, next_version, body_file):
    templates = [
        ROOT / "pull_request_template.md",
        ROOT / ".github/pull_request_template.md",
        ROOT / "docs/pull_request_template.md",
    ]
    template_dir = ROOT / ".github/PULL_REQUEST_TEMPLATE"
    if template_dir.is_dir():
        templates.extend(path for path in template_dir.iterdir() if path.is_file())
    templates = [path for path in templates if path.is_file()]
    if body_file is None:
        if templates:
            raise ReleaseError(
                "PR template found; supply a completed matching --body-file"
            )
        return (
            f"Start {next_version} development after publishing v{version}.\n\n"
            "Validation: pyproject.toml, package, and uv.lock versions are synchronized.\n"
        )
    body = body_file.read_text(encoding="utf-8")

    def headings(text):
        return re.findall(r"(?m)^#{1,6} .+$", text)

    if not body.strip() or (
        templates
        and not any(
            headings(body) == headings(path.read_text(encoding="utf-8"))
            for path in templates
        )
    ):
        raise ReleaseError(
            "PR body must be nonempty and preserve the template section headings"
        )
    return body


def local_changes(next_version):
    paths = {
        path.relative_to(ROOT).as_posix() for path in version_tool().version_files(ROOT)
    }
    version_command("check")
    version_command("set", next_version)
    version_command("check")
    changed = set(
        filter(None, command(["git", "diff", "--name-only", "-z"]).split("\0"))
    )
    if not changed or not changed <= paths:
        raise ReleaseError(
            "Version synchronization changed unexpected files, or changed nothing"
        )
    return {path: (ROOT / path).read_bytes() for path in sorted(changed)}


def changes_at_parent(github, parent, version, next_version):
    """Reconstruct the intended bump from an older main without checking it out."""
    paths = {
        path.relative_to(ROOT).as_posix() for path in version_tool().version_files(ROOT)
    }
    with temporary_directory() as directory:
        root = Path(directory)
        originals = {}
        for path in sorted(paths):
            originals[path] = github.contents(path, parent)
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(originals[path])
        if version_tool().check_versions(root) != version:
            raise ReleaseError("Existing branch was not based on the released version")
        version_tool().synchronize(root, next_version)
        version_tool().check_versions(root)
        return {
            path: (root / path).read_bytes()
            for path in sorted(paths)
            if (root / path).read_bytes() != originals[path]
        }


def verify_bump(github, sha, base, version, next_version, changes):
    commit = github.api(f"git/commits/{sha}")
    if (
        not commit.get("verification", {}).get("verified")
        or commit.get("message") != f"Start {next_version} development"
        or len(commit.get("parents", [])) != 1
    ):
        raise ReleaseError(
            "Existing post-release branch is not the expected signed bump; leaving it unchanged"
        )
    parent = commit["parents"][0]["sha"]
    if parent != base:
        ancestry = github.api(f"compare/{parent}...{base}")
        if ancestry.get("status") not in {"ahead", "identical"}:
            raise ReleaseError(
                "Existing post-release branch is not based on main history"
            )
        changes = changes_at_parent(github, parent, version, next_version)
    comparison = github.api(f"compare/{parent}...{sha}")
    files = comparison.get("files", [])
    if (
        comparison.get("total_commits") != 1
        or {item["filename"] for item in files} != changes.keys()
        or any(item["status"] != "modified" for item in files)
    ):
        raise ReleaseError(
            "Existing post-release branch includes unrelated changes; leaving it unchanged"
        )
    if any(github.contents(path, sha) != content for path, content in changes.items()):
        raise ReleaseError(
            "Existing post-release branch has unexpected contents; leaving it unchanged"
        )


def create_bump(github, branch, base, next_version, changes):
    payload = {
        "query": "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
        "variables": {
            "input": {
                "branch": {
                    "repositoryNameWithOwner": github.repository,
                    "branchName": branch,
                },
                "expectedHeadOid": base,
                "message": {"headline": f"Start {next_version} development"},
                "fileChanges": {
                    "additions": [
                        {
                            "path": path,
                            "contents": base64.b64encode(content).decode("ascii"),
                        }
                        for path, content in changes.items()
                    ]
                },
            }
        },
    }
    response = json.loads(
        command(
            ["gh", "api", "graphql", "--input", "-"], input_text=json.dumps(payload)
        )
    )
    if response.get("errors"):
        raise ReleaseError(
            f"GitHub rejected the signed commit: {json.dumps(response['errors'])}"
        )
    return response["data"]["createCommitOnBranch"]["commit"]["oid"]


def ensure_pull_request(github, branch, sha, next_version, body):
    query = urlencode(
        {
            "state": "all",
            "head": f"{github.repository.split('/')[0]}:{branch}",
            "base": "main",
            "per_page": 100,
        }
    )
    pulls = github.api(f"pulls?{query}")
    for pull in pulls:
        if pull["state"] == "open":
            if pull["head"]["sha"] != sha:
                raise ReleaseError(
                    "Post-release branch advanced; refusing to reuse an unverified PR"
                )
            if not any(label["name"] == "enqueue" for label in pull.get("labels", [])):
                github.api(
                    f"issues/{pull['number']}/labels",
                    method="POST",
                    data={"labels": ["enqueue"]},
                )
            return pull["html_url"]
    if pulls:
        raise ReleaseError(
            f"Existing post-release PR is closed; leaving it unchanged: {pulls[0]['html_url']}"
        )
    with temporary_directory() as directory:
        path = Path(directory) / "body.md"
        path.write_text(body, encoding="utf-8")
        return command(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                github.repository,
                "--base",
                "main",
                "--head",
                branch,
                "--title",
                f"Post-release: start {next_version}",
                "--body-file",
                str(path),
                "--label",
                "enqueue",
            ]
        ).strip()


def post_release(github, environ, tag, body_file=None):
    version = validate_tag(github, environ, tag)
    if environ.get("GITHUB_REF") != f"refs/tags/{tag}":
        raise ReleaseError("Post-release requires the corresponding tag workflow run")
    release = github.api(f"releases/tags/{quote(tag, safe='')}")
    if (
        release.get("tag_name") != tag
        or release.get("draft")
        or release.get("prerelease")
        or not release.get("published_at")
    ):
        raise ReleaseError(
            "Post-release requires a successfully published stable GitHub release"
        )
    release_ref = github.ref(f"tags/{tag}")
    if release_ref is None or github.target(release_ref) != environ.get("GITHUB_SHA"):
        raise ReleaseError("Release tag no longer matches this workflow's commit")
    main_ref = github.ref("heads/main")
    if main_ref is None:
        raise ReleaseError("Main branch is missing")
    base = github.target(main_ref)
    current_version = manifest_version(github.contents("pyproject.toml", base))
    if current_version != version:
        print(
            f"Main is already at {current_version}; no post-release bump needed for {tag}"
        )
        return None
    clean_checkout(base)
    major, minor, patch = map(int, version.split("."))
    next_version = f"{major}.{minor}.{patch + 1}.dev0"
    body = pull_request_body(version, next_version, body_file)
    changes = local_changes(next_version)
    branch = f"post-release/{tag}"
    existing = github.ref(f"heads/{branch}")
    if existing is None:
        if github.target(github.ref("heads/main")) != base:
            raise ReleaseError(
                "Main advanced during preparation; rerun with a fresh main checkout"
            )
        github.create_ref(f"heads/{branch}", base)
        existing = github.ref(f"heads/{branch}")
    sha = github.target(existing)
    if sha == base:
        sha = create_bump(github, branch, base, next_version, changes)
    else:
        # A prior run may have created the empty branch before main advanced.
        # An ancestor of main contains no branch-only work, so it is safe to
        # complete that exact bump with an expectedHeadOid guard.
        ancestry = github.api(f"compare/{sha}...{base}")
        if ancestry.get("status") in {"ahead", "identical"}:
            older_changes = changes_at_parent(github, sha, version, next_version)
            sha = create_bump(github, branch, sha, next_version, older_changes)
    verify_bump(github, sha, base, version, next_version, changes)
    return ensure_pull_request(github, branch, sha, next_version, body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tag")
    commands.add_parser("publish").add_argument("--tag", required=True)
    post = commands.add_parser("post-release")
    post.add_argument("--tag", required=True)
    post.add_argument("--body-file", type=Path)
    args = parser.parse_args()
    if not os.environ.get("GH_TOKEN"):
        raise ReleaseError("GH_TOKEN must contain the release App token")
    github = GitHub(os.environ.get("GITHUB_REPOSITORY", ""))
    if args.command == "tag":
        tagged, version = tag_release(github, os.environ)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"tagged={str(tagged).lower()}\nversion={version}\n")
    elif args.command == "publish":
        print(publish_release(github, os.environ, args.tag))
    else:
        url = post_release(github, os.environ, args.tag, args.body_file)
        if url:
            print(url)


if __name__ == "__main__":
    try:
        main()
    except (ReleaseError, KeyError, ValueError, OSError) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
