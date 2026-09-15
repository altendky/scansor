#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Validate release metadata and prepare a signed release-version PR."""

import argparse
import ast
import os
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "altendky/scansor"
NUMBER = r"(?:0|[1-9][0-9]*)"
VERSION = re.compile(rf"({NUMBER})\.({NUMBER})\.({NUMBER})(?:\.dev({NUMBER}))?")


def parse_version(value, *, stable=False):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None or (stable and match[4] is not None):
        kind = "X.Y.Z" if stable else "X.Y.Z or X.Y.Z.devN"
        raise ValueError(
            f"Invalid version {value!r}; expected {kind} without leading zeros"
        )
    return (*map(int, match.group(1, 2, 3)), match[4] is None, int(match[4] or 0))


def version_files(root):
    """Return the complete version-edit allowlist."""
    return [root / "pyproject.toml", root / "src/scansor/__init__.py", root / "uv.lock"]


def check_versions(root, tag=None):
    manifest = tomllib.loads((root / "pyproject.toml").read_text())
    if manifest["project"]["name"] != "scansor":
        raise ValueError("Expected the scansor project")
    version = manifest["project"]["version"]
    parse_version(version)
    if tag is not None:
        if not tag.startswith("v"):
            raise ValueError("Release tag must be vX.Y.Z")
        parse_version(tag[1:], stable=True)
        if tag != f"v{version}":
            raise ValueError(f"Tag {tag!r} does not match package version {version}")
    module = ast.parse((root / "src/scansor/__init__.py").read_text())
    versions = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
    ]
    if versions != [version]:
        raise ValueError("src/scansor/__init__.py version differs from pyproject.toml")
    locked = tomllib.loads((root / "uv.lock").read_text())["package"]
    entries = [item for item in locked if item["name"] == "scansor"]
    if (
        len(entries) != 1
        or entries[0]["version"] != version
        or entries[0].get("source") != {"editable": "."}
    ):
        raise ValueError(
            "uv.lock: expected one local editable scansor package at the project version"
        )
    return version


def replace_once(pattern, replacement, value, label):
    result, count = re.subn(pattern, replacement, value, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"{label}: expected one version field, found {count}")
    return result


def synchronized_contents(root, version):
    """Plan all edits, preserving dependency resolutions and unrelated text."""
    parse_version(version)
    check_versions(root)
    contents = {path: path.read_text() for path in version_files(root)}
    for relative, pattern in (
        (
            "pyproject.toml",
            r'(^\[project\]\s*\n(?:[^\[]*\n)*?version\s*=\s*")[^"]+("[^\n]*$)',
        ),
        ("src/scansor/__init__.py", r'(^__version__\s*=\s*")[^"]+("[^\n]*$)'),
    ):
        path = root / relative
        contents[path] = replace_once(
            pattern,
            lambda match: match[1] + version + match[2],
            contents[path],
            relative,
        )
    path = root / "uv.lock"
    blocks = re.split(r"(?m)(?=^\[\[package\]\]$)", contents[path])
    for index, block in enumerate(blocks):
        if (
            block.startswith("[[package]]")
            and tomllib.loads(block)["package"][0]["name"] == "scansor"
        ):
            blocks[index] = replace_once(
                r'(^version\s*=\s*")[^"]+("[^\n]*$)',
                lambda match: match[1] + version + match[2],
                block,
                "uv.lock scansor",
            )
    contents[path] = "".join(blocks)
    return contents


def synchronize(root, version):
    contents = synchronized_contents(root, version)
    changed = []
    for path, content in contents.items():
        if path.read_text() != content:
            path.write_text(content)
            changed.append(path)
    check_versions(root)
    return changed


def next_version(root):
    current = check_versions(root)
    major, minor, patch, _, _ = parse_version(current, stable=True)
    return f"{major}.{minor}.{patch + 1}.dev0"


def run(root, *command):
    return subprocess.run(
        command,
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    ).stdout.strip()


def pr_templates(root):
    paths = [
        root / name
        for name in (
            "pull_request_template.md",
            ".github/pull_request_template.md",
            "docs/pull_request_template.md",
        )
    ]
    paths.extend((root / ".github/PULL_REQUEST_TEMPLATE").glob("*"))
    return [path for path in paths if path.is_file()]


def prepare(root, version, *, body_file=None, runner=run):
    parse_version(version, stable=True)
    if (
        Path(runner(root, "git", "rev-parse", "--show-toplevel")).resolve()
        != root.resolve()
    ):
        raise ValueError("Run preparation from the scansor repository")
    if runner(root, "git", "symbolic-ref", "--short", "HEAD") != "main":
        raise ValueError("Release preparation requires main")
    if runner(root, "git", "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("Release preparation requires a clean working tree")
    origin = runner(root, "git", "remote", "get-url", "origin")
    if origin.removesuffix(".git") not in (
        f"git@github.com:{REPOSITORY}",
        f"https://github.com/{REPOSITORY}",
        f"ssh://git@github.com/{REPOSITORY}",
    ):
        raise ValueError("Origin must identify altendky/scansor")
    if body_file is not None:
        body_file = body_file.resolve(strict=True)
        if not body_file.is_file() or not body_file.read_text().strip():
            raise ValueError("PR body file must contain a reviewed description")
    elif pr_templates(root):
        raise ValueError(
            "A PR template exists; provide its completed structure with --body-file"
        )
    current = check_versions(root)
    if parse_version(version) < parse_version(current):
        raise ValueError(f"Refusing downgrade from {current} to {version}")
    # Normal authenticated Git operations are sequential. Failures are never retried.
    runner(root, "git", "pull", "--ff-only", "origin", "main")
    if runner(root, "git", "rev-parse", "HEAD") != runner(
        root, "git", "rev-parse", "origin/main"
    ):
        raise ValueError("Local main has commits absent from origin/main; stopping")
    current = check_versions(root)
    if parse_version(version) < parse_version(current):
        raise ValueError(
            f"Refusing downgrade from updated version {current} to {version}"
        )
    if runner(root, "git", "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("Working tree changed during pull; stopping")
    if body_file is None and pr_templates(root):
        raise ValueError(
            "A PR template was added; provide its completed structure with --body-file"
        )
    branch, tag = f"release/v{version}", f"v{version}"
    if runner(
        root,
        "git",
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{branch}",
        f"refs/remotes/origin/{branch}",
        f"refs/tags/{tag}",
    ):
        raise ValueError("The release branch or tag already exists locally")
    if runner(
        root,
        "git",
        "ls-remote",
        "origin",
        f"refs/heads/{branch}",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    ):
        raise ValueError("The release branch or tag already exists on origin")
    synchronized_contents(root, version)
    runner(root, "git", "switch", "--create", branch)
    changed = synchronize(root, version)
    if changed:
        runner(
            root, "git", "add", "--", *(str(path.relative_to(root)) for path in changed)
        )
    runner(root, "git", "commit", "--gpg-sign", "--allow-empty", "-m", f"Release {tag}")
    runner(root, "git", "push", "--set-upstream", "origin", branch)

    def create_pr(path):
        return runner(
            root,
            "gh",
            "pr",
            "create",
            "--repo",
            REPOSITORY,
            "--base",
            "main",
            "--head",
            branch,
            "--title",
            f"Release {tag}",
            "--body-file",
            str(path),
        )

    if body_file is not None:
        return create_pr(body_file)
    temporary_root = Path(os.environ.get("TMPDIR", "/tmp")) / "agents"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="release-", dir=temporary_root
    ) as directory:
        path = Path(directory) / "pull-request.md"
        path.write_text(
            f"Prepare version {version} for release. Keep pyproject.toml, the Python package version, "
            "and uv.lock synchronized.\n\n"
            "Version metadata was checked locally. The pull request CI validates the "
            "project before tagging and GitHub publication.\n"
        )
        return create_pr(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument("--tag")
    commands.add_parser("set").add_argument("version")
    commands.add_parser("next")
    preparation = commands.add_parser("prepare")
    preparation.add_argument("version")
    preparation.add_argument("--body-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(check_versions(ROOT, args.tag))
        elif args.command == "set":
            synchronize(ROOT, args.version)
            print(args.version)
        elif args.command == "next":
            print(next_version(ROOT))
        elif args.command == "prepare":
            print(prepare(ROOT, args.version, body_file=args.body_file))
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"release-version: {error}\n")
    except subprocess.CalledProcessError as error:
        parser.exit(
            error.returncode,
            f"release-version: command failed; stopped without retry: {' '.join(error.cmd)}\n",
        )


if __name__ == "__main__":
    main()
