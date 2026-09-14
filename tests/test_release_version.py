#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Exercise release metadata and preparation using fixtures and mocked Git."""

# Dynamic module loading and unittest mock fixtures use basic type checking.
# pyright: basic, reportUninitializedInstanceVariable=false

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

SPEC = importlib.util.spec_from_file_location(
    "release_version",
    Path(__file__).resolve().parents[1] / "scripts/release-version.py",
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class FakeCommands:
    def __init__(
        self,
        root,
        *,
        fail=None,
        branch="main",
        dirty="",
        origin=None,
        local_refs="",
        remote_refs="",
        after_pull=None,
        local_ahead: bool = False,
    ):
        self.root = root
        self.fail = fail
        self.branch = branch
        self.dirty = dirty
        self.origin = origin or "git@github.com:altendky/scansor.git"
        self.local_refs = local_refs
        self.remote_refs = remote_refs
        self.after_pull = after_pull
        self.local_ahead = local_ahead
        self.calls = []
        self.pr_body = None

    def __call__(self, root, *command):
        if root != self.root:
            raise AssertionError("Unexpected command working directory")
        self.calls.append(command)
        if self.fail and command[: len(self.fail)] == self.fail:
            raise subprocess.CalledProcessError(1, command)
        if command[:2] == ("git", "rev-parse"):
            if command[-1] == "--show-toplevel":
                return str(root)
            return (
                "local-only"
                if self.local_ahead and command[-1] == "HEAD"
                else "main-commit"
            )
        if command[:2] == ("git", "symbolic-ref"):
            return self.branch
        if command[:2] == ("git", "status"):
            return self.dirty
        if command[:2] == ("git", "remote"):
            return self.origin
        if command[:2] == ("git", "pull") and self.after_pull:
            self.after_pull()
        if command[:2] == ("git", "for-each-ref"):
            return self.local_refs
        if command[:2] == ("git", "ls-remote"):
            return self.remote_refs
        if command[:3] == ("gh", "pr", "create"):
            self.pr_body = Path(command[command.index("--body-file") + 1]).read_text()
            return "https://github.com/altendky/scansor/pull/100"
        return ""


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(os.environ.get("TMPDIR", "/tmp")) / "agents"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="release-test-", dir=temporary_root
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write(
            "pyproject.toml",
            '[project]\nname = "scansor"\nversion = "0.1.0" # release version\n',
        )
        self.write("src/scansor/__init__.py", '__version__ = "0.1.0"\n')
        self.external_lock = '[[package]]\nname = "external"\nversion = "0.1.0"\nsource = { registry = "https://pypi.org/simple" }\n\n'
        self.write(
            "uv.lock",
            "# preserved comment\nversion = 1\n\n"
            + self.external_lock
            + '[[package]]\nname = "scansor"\nversion = "0.1.0"\nsource = { editable = "." }\n',
        )

    def write(self, path, content):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_strict_versions_and_stable_tags(self):
        for value in ("0.0.0", "1.2.3", "1.2.3.dev0", "1.2.3.dev10"):
            release.parse_version(value)
        for value in (
            "v1.2.3",
            "1.2",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "1.2.3.dev01",
            "1.2.3-alpha.0",
            "1.2.3-dev",
            "1.2.3+build",
            "1.2.3\n",
            " 1.2.3",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.parse_version(value)
        self.assertEqual(release.check_versions(self.root, "v0.1.0"), "0.1.0")
        for tag in ("0.1.0", "v0.1.1", "v0.1.0.dev0", "v00.1.0"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.check_versions(self.root, tag)

    def test_synchronizes_only_owned_version_fields(self):
        changed = release.synchronize(self.root, "0.2.0.dev3")
        self.assertEqual(release.check_versions(self.root), "0.2.0.dev3")
        self.assertEqual(set(changed), set(release.version_files(self.root)))
        self.assertIn(self.external_lock, (self.root / "uv.lock").read_text())
        self.assertEqual(release.synchronize(self.root, "0.2.0.dev3"), [])

    def test_drift_is_rejected_before_any_writes(self):
        for relative in ("uv.lock", "src/scansor/__init__.py"):
            path = self.root / relative
            original = path.read_text()
            path.write_text(original.replace('"0.1.0"', '"0.0.9"'))
            before = self.snapshot()
            with self.assertRaises(ValueError):
                release.synchronize(self.root, "0.1.1")
            self.assertEqual(self.snapshot(), before)
            path.write_text(original)

    def test_next_requires_stable_version(self):
        self.assertEqual(release.next_version(self.root), "0.1.1.dev0")
        release.synchronize(self.root, "0.1.1.dev0")
        with self.assertRaises(ValueError):
            release.next_version(self.root)

    def test_prepare_initial_version_uses_signed_empty_commit_and_body_file(self):
        commands = FakeCommands(self.root)
        before = self.snapshot()
        result = release.prepare(self.root, "0.1.0", runner=commands)
        self.assertTrue(result.endswith("/pull/100"))
        self.assertEqual(self.snapshot(), before)
        self.assertIn(("git", "pull", "--ff-only", "origin", "main"), commands.calls)
        self.assertIn(
            ("git", "commit", "--gpg-sign", "--allow-empty", "-m", "Release v0.1.0"),
            commands.calls,
        )
        self.assertFalse(
            any(command[:2] == ("git", "add") for command in commands.calls)
        )
        assert commands.pr_body is not None
        self.assertIn("0.1.0", commands.pr_body)
        self.assertFalse(
            any("--force" in arg for command in commands.calls for arg in command)
        )

    def test_prepare_signing_failure_stops_without_push_or_retry(self):
        commands = FakeCommands(self.root, fail=("git", "commit"))
        with self.assertRaises(subprocess.CalledProcessError):
            release.prepare(self.root, "0.1.1", runner=commands)
        self.assertEqual(commands.calls[-1][:2], ("git", "commit"))
        self.assertEqual(
            sum(command[:2] == ("git", "commit") for command in commands.calls), 1
        )
        self.assertEqual(release.check_versions(self.root), "0.1.1")
        add = next(
            command for command in commands.calls if command[:2] == ("git", "add")
        )
        self.assertEqual(
            set(add[3:]),
            {
                str(path.relative_to(self.root))
                for path in release.version_files(self.root)
            },
        )

    def test_prepare_preconditions_fail_before_branch_creation(self):
        cases: tuple[dict[str, Any], ...] = (
            {"branch": "feature"},
            {"dirty": "?? file"},
            {"origin": "git@github.com:someone/other.git"},
            {"local_refs": "refs/tags/v0.1.0"},
            {"remote_refs": "abc\trefs/heads/release/v0.1.0"},
        )
        for options in cases:
            with self.subTest(options=options):
                commands = FakeCommands(self.root, **options)
                with self.assertRaises(ValueError):
                    release.prepare(self.root, "0.1.0", runner=commands)
                self.assertFalse(
                    any(command[:2] == ("git", "switch") for command in commands.calls)
                )

    def test_prepare_rejects_downgrade_including_after_pull(self):
        commands = FakeCommands(self.root)
        with self.assertRaisesRegex(ValueError, "downgrade"):
            release.prepare(self.root, "0.0.9", runner=commands)
        self.assertFalse(
            any(command[:2] == ("git", "pull") for command in commands.calls)
        )
        commands = FakeCommands(
            self.root, after_pull=lambda: release.synchronize(self.root, "0.2.0")
        )
        with self.assertRaisesRegex(ValueError, "downgrade"):
            release.prepare(self.root, "0.1.0", runner=commands)
        self.assertFalse(
            any(command[:2] == ("git", "switch") for command in commands.calls)
        )

    def test_prepare_rejects_local_unpushed_main(self):
        commands = FakeCommands(self.root, local_ahead=True)
        with self.assertRaisesRegex(ValueError, "absent from origin/main"):
            release.prepare(self.root, "0.1.1", runner=commands)
        self.assertFalse(
            any(command[:2] == ("git", "switch") for command in commands.calls)
        )

    def test_prepare_network_failure_stops_at_first_command(self):
        for failure in (
            ("git", "pull"),
            ("git", "ls-remote"),
            ("git", "push"),
            ("gh", "pr", "create"),
        ):
            with self.subTest(failure=failure):
                commands = FakeCommands(self.root, fail=failure)
                with self.assertRaises(subprocess.CalledProcessError):
                    release.prepare(self.root, "0.1.0", runner=commands)
                self.assertEqual(commands.calls[-1][: len(failure)], failure)

    def test_template_requires_explicit_completed_body(self):
        self.write(".github/pull_request_template.md", "## Summary\n\n## Validation\n")
        commands = FakeCommands(self.root)
        with self.assertRaisesRegex(ValueError, "--body-file"):
            release.prepare(self.root, "0.1.0", runner=commands)
        self.assertFalse(
            any(command[:2] == ("git", "pull") for command in commands.calls)
        )
        self.write(
            "body.md",
            "## Summary\n\nPrepare release.\n\n## Validation\n\nVersion check.\n",
        )
        release.prepare(
            self.root, "0.1.0", body_file=self.root / "body.md", runner=commands
        )
        self.assertEqual(commands.pr_body, (self.root / "body.md").read_text())


if __name__ == "__main__":
    unittest.main()
