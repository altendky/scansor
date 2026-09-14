#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Exercise release-App safeguards without credentials or network access."""

# Dynamic module loading and unittest mock fixtures use basic type checking.
# pyright: basic, reportUninitializedInstanceVariable=false

import base64
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_github", ROOT / ".github/scripts/release-github.py"
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def ref(sha):
    return {"object": {"type": "commit", "sha": sha}}


def manifest(version):
    return f'[project]\nversion = "{version}"\n'.encode()


class TagTests(unittest.TestCase):
    def setUp(self):
        self.github = Mock(repository="owner/repo")
        self.github.target.side_effect = lambda value: value["object"]["sha"]
        self.refs = {"heads/main": ref("main")}
        self.github.ref.side_effect = self.refs.get
        self.environ = {
            "RELEASE_ENABLED": "true",
            "GITHUB_REPOSITORY": "altendky/scansor",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": "main",
        }
        self.clean = patch.object(release, "clean_checkout").start()
        self.version_command = patch.object(release, "version_command").start()
        self.root = patch.object(release, "ROOT").start()
        (self.root / "pyproject.toml").read_bytes.return_value = manifest("1.2.3")
        self.addCleanup(patch.stopall)

    def test_creates_only_validated_main_tag(self):
        self.assertEqual(
            release.tag_release(self.github, self.environ), (True, "1.2.3")
        )
        self.github.create_ref.assert_called_once_with("tags/v1.2.3", "main")
        self.clean.assert_called_once_with("main")
        self.version_command.assert_called_once_with("check")

    def test_rejects_non_main_run(self):
        self.environ["GITHUB_REF"] = "refs/pull/10/merge"
        with self.assertRaises(release.ReleaseError):
            release.tag_release(self.github, self.environ)
        self.github.create_ref.assert_not_called()

    def test_rejects_stale_main_run_before_running_version_tool(self):
        self.refs["heads/main"] = ref("new-main")
        with self.assertRaisesRegex(release.ReleaseError, "stale"):
            release.tag_release(self.github, self.environ)
        self.version_command.assert_not_called()
        self.github.create_ref.assert_not_called()

    def test_skips_prerelease(self):
        (self.root / "pyproject.toml").read_bytes.return_value = manifest("1.2.4.dev0")
        self.assertEqual(
            release.tag_release(self.github, self.environ), (False, "1.2.4.dev0")
        )
        self.github.create_ref.assert_not_called()

    def test_rejects_build_metadata(self):
        (self.root / "pyproject.toml").read_bytes.return_value = manifest("1.2.3+build")
        with self.assertRaises(release.ReleaseError):
            release.tag_release(self.github, self.environ)
        self.github.create_ref.assert_not_called()

    def test_existing_tag_at_same_commit_is_idempotent(self):
        self.refs["tags/v1.2.3"] = ref("main")
        self.assertEqual(
            release.tag_release(self.github, self.environ), (False, "1.2.3")
        )
        self.github.api.assert_not_called()
        self.github.create_ref.assert_not_called()

    def test_existing_same_version_tag_on_main_ancestor_is_idempotent(self):
        self.refs["tags/v1.2.3"] = ref("old-main")
        self.github.api.return_value = {"status": "ahead"}
        self.github.contents.return_value = manifest("1.2.3")
        self.assertEqual(
            release.tag_release(self.github, self.environ), (False, "1.2.3")
        )
        self.github.api.assert_called_once_with("compare/old-main...main")
        self.github.contents.assert_called_once_with("pyproject.toml", "old-main")
        self.github.create_ref.assert_not_called()

    def test_existing_ancestor_tag_with_different_version_is_rejected(self):
        self.refs["tags/v1.2.3"] = ref("old-main")
        self.github.api.return_value = {"status": "ahead"}
        self.github.contents.return_value = manifest("1.2.2")
        with self.assertRaisesRegex(release.ReleaseError, "different package version"):
            release.tag_release(self.github, self.environ)
        self.github.create_ref.assert_not_called()

    def test_existing_tag_outside_main_history_is_rejected(self):
        self.refs["tags/v1.2.3"] = ref("different")
        self.github.api.return_value = {"status": "diverged"}
        with self.assertRaisesRegex(release.ReleaseError, "another commit"):
            release.tag_release(self.github, self.environ)
        self.github.contents.assert_not_called()
        self.github.create_ref.assert_not_called()

    def test_main_advance_during_validation_does_not_create_tag(self):
        self.github.ref.side_effect = [ref("main"), None, ref("new-main")]
        with self.assertRaisesRegex(release.ReleaseError, "advanced"):
            release.tag_release(self.github, self.environ)
        self.github.create_ref.assert_not_called()


class PostReleaseTests(unittest.TestCase):
    def setUp(self):
        self.github = Mock(repository="owner/repo")
        self.github.target.side_effect = lambda value: value["object"]["sha"]
        self.refs = {"heads/main": ref("main"), "tags/v1.2.3": ref("tag-commit")}
        self.github.ref.side_effect = self.refs.get
        self.release_record = {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-09-13",
        }
        self.github.api.return_value = self.release_record
        self.github.contents.return_value = manifest("1.2.3")
        self.environ = {
            "RELEASE_ENABLED": "true",
            "GITHUB_REPOSITORY": "altendky/scansor",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/tags/v1.2.3",
            "GITHUB_SHA": "tag-commit",
        }
        self.clean = patch.object(release, "clean_checkout").start()
        self.validate = patch.object(
            release, "validate_tag", return_value="1.2.3"
        ).start()
        self.body = patch.object(
            release, "pull_request_body", return_value="Body"
        ).start()
        self.changes = patch.object(
            release,
            "local_changes",
            return_value={"pyproject.toml": manifest("1.2.4.dev0")},
        ).start()
        self.create = patch.object(release, "create_bump", return_value="bump").start()
        self.verify = patch.object(release, "verify_bump").start()
        self.pr = patch.object(
            release,
            "ensure_pull_request",
            return_value="https://github.com/owner/repo/pull/2",
        ).start()
        self.github.create_ref.side_effect = lambda name, sha: self.refs.update(
            {name: ref(sha)}
        )
        self.addCleanup(patch.stopall)

    def run_post(self):
        return release.post_release(self.github, self.environ, "v1.2.3")

    def test_requires_published_release(self):
        for field, value in [
            ("draft", True),
            ("prerelease", True),
            ("published_at", None),
        ]:
            with self.subTest(field=field):
                original = self.release_record[field]
                self.release_record[field] = value
                with self.assertRaises(release.ReleaseError):
                    self.run_post()
                self.release_record[field] = original
        self.github.create_ref.assert_not_called()
        self.changes.assert_not_called()

    def test_rejects_wrong_tag_event_or_retargeted_tag(self):
        self.environ["GITHUB_REF"] = "refs/heads/main"
        with self.assertRaises(release.ReleaseError):
            self.run_post()
        self.environ["GITHUB_REF"] = "refs/tags/v1.2.3"
        self.refs["tags/v1.2.3"] = ref("wrong")
        with self.assertRaises(release.ReleaseError):
            self.run_post()
        self.github.create_ref.assert_not_called()

    def test_inspects_exact_main_and_skips_advanced_version(self):
        self.github.contents.return_value = manifest("1.2.4.dev0")
        self.assertIsNone(self.run_post())
        self.github.contents.assert_called_once_with("pyproject.toml", "main")
        self.clean.assert_not_called()
        self.changes.assert_not_called()
        self.github.create_ref.assert_not_called()

    def test_creates_signed_bump_then_verifies_before_pr(self):
        self.assertEqual(self.run_post(), "https://github.com/owner/repo/pull/2")
        self.clean.assert_called_once_with("main")
        self.github.create_ref.assert_called_once_with(
            "heads/post-release/v1.2.3", "main"
        )
        self.create.assert_called_once()
        self.verify.assert_called_once_with(
            self.github,
            "bump",
            "main",
            "1.2.3",
            "1.2.4.dev0",
            self.changes.return_value,
        )
        self.pr.assert_called_once()

    def test_recovers_branch_creation_without_deleting_or_recreating(self):
        self.refs["heads/post-release/v1.2.3"] = ref("main")
        self.run_post()
        self.github.create_ref.assert_not_called()
        self.create.assert_called_once()

    def test_recovers_existing_bump_without_committing_twice(self):
        self.refs["heads/post-release/v1.2.3"] = ref("bump")
        self.run_post()
        self.github.create_ref.assert_not_called()
        self.create.assert_not_called()
        self.verify.assert_called_once()
        self.pr.assert_called_once()

    def test_recovers_empty_branch_when_main_advanced(self):
        self.refs["heads/post-release/v1.2.3"] = ref("old-main")
        self.github.api.side_effect = lambda path: (
            {"status": "ahead"}
            if path == "compare/old-main...main"
            else self.release_record
        )
        older_changes = {"pyproject.toml": manifest("1.2.4.dev0")}
        with patch.object(release, "changes_at_parent", return_value=older_changes):
            self.run_post()
        self.github.create_ref.assert_not_called()
        self.create.assert_called_once_with(
            self.github, "post-release/v1.2.3", "old-main", "1.2.4.dev0", older_changes
        )

    def test_main_advance_before_branch_creation_does_not_create_branch(self):
        self.github.ref.side_effect = [
            ref("tag-commit"),
            ref("main"),
            None,
            ref("new-main"),
        ]
        with self.assertRaisesRegex(release.ReleaseError, "advanced"):
            self.run_post()
        self.github.create_ref.assert_not_called()
        self.create.assert_not_called()

    def test_rejects_unrelated_branch_without_pr_creation(self):
        self.refs["heads/post-release/v1.2.3"] = ref("unrelated")
        self.verify.side_effect = release.ReleaseError("unrelated work")
        with self.assertRaises(release.ReleaseError):
            self.run_post()
        self.github.create_ref.assert_not_called()
        self.create.assert_not_called()
        self.pr.assert_not_called()


class CommitVerificationTests(unittest.TestCase):
    def setUp(self):
        self.github = Mock(repository="owner/repo")
        self.commit = {
            "verification": {"verified": True},
            "message": "Start 1.2.4.dev0 development",
            "parents": [{"sha": "main"}],
        }
        self.comparison = {
            "total_commits": 1,
            "files": [{"filename": "pyproject.toml", "status": "modified"}],
        }
        self.github.api.side_effect = lambda path: (
            self.commit if path == "git/commits/bump" else self.comparison
        )
        self.contents = manifest("1.2.4.dev0")
        self.github.contents.return_value = self.contents

    def verify(self):
        release.verify_bump(
            self.github,
            "bump",
            "main",
            "1.2.3",
            "1.2.4.dev0",
            {"pyproject.toml": self.contents},
        )

    def test_accepts_only_exact_signed_change(self):
        self.verify()

    def test_rejects_unsigned_commit(self):
        self.commit["verification"]["verified"] = False
        with self.assertRaisesRegex(release.ReleaseError, "signed"):
            self.verify()

    def test_rejects_added_unrelated_file(self):
        self.comparison["files"].append(
            {"filename": "unrelated.txt", "status": "added"}
        )
        with self.assertRaisesRegex(release.ReleaseError, "unrelated"):
            self.verify()

    def test_rejects_unexpected_content_in_version_file(self):
        self.github.contents.return_value = self.contents + b"# unrelated work\n"
        with self.assertRaisesRegex(release.ReleaseError, "contents"):
            self.verify()

    def test_recovers_older_parent_only_when_on_main_history(self):
        self.commit["parents"] = [{"sha": "old-main"}]
        self.github.api.side_effect = lambda path: (
            self.commit
            if path == "git/commits/bump"
            else {"status": "ahead"}
            if path == "compare/old-main...main"
            else self.comparison
        )
        with patch.object(
            release, "changes_at_parent", return_value={"pyproject.toml": self.contents}
        ) as reconstruct:
            self.verify()
            reconstruct.assert_called_once_with(
                self.github, "old-main", "1.2.3", "1.2.4.dev0"
            )


class ApiAndPullRequestTests(unittest.TestCase):
    def test_clean_checkout_rejects_stale_head_and_untracked_work(self):
        with (
            patch.object(release, "command", return_value="stale\n"),
            self.assertRaisesRegex(release.ReleaseError, "stale"),
        ):
            release.clean_checkout("main")
        with (
            patch.object(
                release, "command", side_effect=["main\n", "?? personal.txt\n"]
            ),
            self.assertRaisesRegex(release.ReleaseError, "clean"),
        ):
            release.clean_checkout("main")

    def test_create_ref_recovers_lost_response_only_for_matching_target(self):
        github = release.GitHub("owner/repo")
        with (
            patch.object(
                github, "api", side_effect=release.ReleaseError("lost response")
            ),
            patch.object(github, "ref", return_value=ref("wanted")),
        ):
            github.create_ref("tags/v1.2.3", "wanted")
        with (
            patch.object(github, "api", side_effect=release.ReleaseError("conflict")),
            patch.object(github, "ref", return_value=ref("other")),
            self.assertRaises(release.ReleaseError),
        ):
            github.create_ref("tags/v1.2.3", "wanted")

    def test_permission_failure_is_not_missing_ref(self):
        github = release.GitHub("owner/repo")
        with (
            patch.object(
                subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 1, "", "gh: Forbidden (HTTP 403)"
                ),
            ),
            self.assertRaises(release.ReleaseError),
        ):
            github.ref("heads/main")
        with patch.object(
            subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                [], 1, "", "gh: Not Found (HTTP 404)"
            ),
        ):
            self.assertIsNone(github.ref("heads/main"))

    def test_graphql_uses_expected_head_and_inline_encoded_file_payload(self):
        github = Mock(repository="owner/repo")
        with patch.object(
            release,
            "command",
            return_value=json.dumps(
                {"data": {"createCommitOnBranch": {"commit": {"oid": "signed"}}}}
            ),
        ) as run:
            self.assertEqual(
                release.create_bump(
                    github,
                    "post-release/v1.2.3",
                    "main",
                    "1.2.4.dev0",
                    {"pyproject.toml": b"content"},
                ),
                "signed",
            )
        payload = json.loads(run.call_args.kwargs["input_text"])["variables"]["input"]
        self.assertEqual(payload["expectedHeadOid"], "main")
        self.assertEqual(
            base64.b64decode(payload["fileChanges"]["additions"][0]["contents"]),
            b"content",
        )

    def test_existing_open_pr_is_reused(self):
        github = Mock(repository="owner/repo")
        github.api.return_value = [
            {
                "number": 2,
                "state": "open",
                "head": {"sha": "bump"},
                "labels": [{"name": "enqueue"}],
                "html_url": "https://github.com/owner/repo/pull/2",
            }
        ]
        with patch.object(release, "command") as run:
            self.assertEqual(
                release.ensure_pull_request(
                    github, "post-release/v1.2.3", "bump", "1.2.4.dev0", "Body"
                ),
                "https://github.com/owner/repo/pull/2",
            )
        run.assert_not_called()

    def test_existing_pr_recovers_missing_enqueue_label(self):
        github = Mock(repository="owner/repo")
        github.api.return_value = [
            {
                "number": 2,
                "state": "open",
                "head": {"sha": "bump"},
                "labels": [],
                "html_url": "https://github.com/owner/repo/pull/2",
            }
        ]
        with patch.object(release, "command") as run:
            release.ensure_pull_request(
                github, "post-release/v1.2.3", "bump", "1.2.4.dev0", "Body"
            )
        run.assert_not_called()
        self.assertEqual(github.api.call_args.args, ("issues/2/labels",))
        self.assertEqual(
            github.api.call_args.kwargs,
            {"method": "POST", "data": {"labels": ["enqueue"]}},
        )

    def test_new_pr_uses_body_file_and_enqueue_label(self):
        github = Mock(repository="owner/repo")
        github.api.return_value = []
        observed_body = []

        def create(args):
            observed_body.append(Path(args[args.index("--body-file") + 1]).read_text())
            self.assertEqual(args[args.index("--label") + 1], "enqueue")
            self.assertNotIn("--body", args)
            return "https://github.com/owner/repo/pull/2\n"

        with patch.object(release, "command", side_effect=create):
            release.ensure_pull_request(
                github, "post-release/v1.2.3", "bump", "1.2.4.dev0", "Exact body\n"
            )
        self.assertEqual(observed_body, ["Exact body\n"])

    def test_pr_body_preserves_existing_template_sections(self):
        with (
            release.temporary_directory() as directory,
            patch.object(release, "ROOT", Path(directory)),
        ):
            root = Path(directory)
            (root / "pull_request_template.md").write_text(
                "## Summary\n\n## Validation\n"
            )
            with self.assertRaisesRegex(release.ReleaseError, "template"):
                release.pull_request_body("1.2.3", "1.2.4.dev0", None)
            body = root / "body.md"
            body.write_text(
                "## Summary\n\nDevelopment bump.\n\n## Validation\n\nVersion check passed.\n"
            )
            self.assertEqual(
                release.pull_request_body("1.2.3", "1.2.4.dev0", body), body.read_text()
            )
            body.write_text("## Unrelated section\n\nBody\n")
            with self.assertRaisesRegex(release.ReleaseError, "headings"):
                release.pull_request_body("1.2.3", "1.2.4.dev0", body)

    def test_closed_pr_is_not_deleted_or_recreated(self):
        github = Mock(repository="owner/repo")
        github.api.return_value = [
            {"state": "closed", "html_url": "https://github.com/owner/repo/pull/2"}
        ]
        with (
            patch.object(release, "command") as run,
            self.assertRaisesRegex(release.ReleaseError, "closed"),
        ):
            release.ensure_pull_request(
                github, "post-release/v1.2.3", "bump", "1.2.4.dev0", "Body"
            )
        run.assert_not_called()


class VersionRecoveryIntegrationTests(unittest.TestCase):
    def test_reconstructs_real_version_file_bump_from_api_contents(self):
        tool = release.version_tool()
        planned = tool.synchronized_contents(ROOT, "1.2.3")
        originals = {
            path.relative_to(ROOT).as_posix(): content.encode()
            for path, content in planned.items()
        }
        github = Mock(repository="owner/repo")
        github.contents.side_effect = lambda path, sha: originals[path]
        changes = release.changes_at_parent(github, "old-main", "1.2.3", "1.2.4.dev0")
        self.assertEqual(
            release.manifest_version(changes["pyproject.toml"]), "1.2.4.dev0"
        )
        self.assertIn("uv.lock", changes)
        self.assertIn("src/scansor/__init__.py", changes)
        self.assertTrue(
            set(changes)
            <= {path.relative_to(ROOT).as_posix() for path in tool.version_files(ROOT)}
        )


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.github = Mock(repository="altendky/scansor")
        self.github.target.side_effect = lambda value: value["object"]["sha"]
        self.refs = {"heads/main": ref("main"), "tags/v1.2.3": ref("tag")}
        self.github.ref.side_effect = self.refs.get
        self.environ = {
            "GITHUB_REPOSITORY": "altendky/scansor",
            "RELEASE_ENABLED": "true",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/tags/v1.2.3",
            "GITHUB_SHA": "tag",
        }
        self.record = {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-09-14",
            "html_url": "https://github.com/altendky/scansor/releases/tag/v1.2.3",
        }
        planned = release.version_tool().synchronized_contents(ROOT, "1.2.3")
        self.files = {
            path.relative_to(ROOT).as_posix(): text.encode()
            for path, text in planned.items()
        }
        self.github.contents.side_effect = lambda path, sha: self.files[path]
        self.github.api.return_value = {"status": "ahead"}

    def test_context_rejects_disabled_fork_and_non_push(self):
        for key, value in (
            ("RELEASE_ENABLED", "false"),
            ("GITHUB_REPOSITORY", "other/scansor"),
            ("GITHUB_EVENT_NAME", "pull_request"),
        ):
            with self.subTest(key=key), self.assertRaises(release.ReleaseError):
                release.validate_tag(
                    self.github, {**self.environ, key: value}, "v1.2.3"
                )
        self.github.ref.assert_not_called()

    def test_tag_validation_checks_all_versions_and_main_ancestry(self):
        self.assertEqual(
            release.validate_tag(self.github, self.environ, "v1.2.3"), "1.2.3"
        )
        self.files["src/scansor/__init__.py"] = b'__version__ = "1.2.4"\n'
        with self.assertRaises(ValueError):
            release.validate_tag(self.github, self.environ, "v1.2.3")
        self.github.api.return_value = {"status": "diverged"}
        with self.assertRaisesRegex(release.ReleaseError, "main history"):
            release.validate_tag(self.github, self.environ, "v1.2.3")

    def test_retargeted_tag_is_rejected(self):
        self.refs["tags/v1.2.3"] = ref("changed")
        with self.assertRaisesRegex(release.ReleaseError, "no longer matches"):
            release.validate_tag(self.github, self.environ, "v1.2.3")

    def test_publishes_once_with_generated_notes_and_reuses_existing_release(self):
        records = [None, self.record]
        with (
            patch.object(release, "validate_tag"),
            patch.object(release, "clean_checkout") as clean,
            patch.object(release, "version_command") as versions,
        ):
            self.github.api.side_effect = records
            self.assertEqual(
                release.publish_release(self.github, self.environ, "v1.2.3"),
                self.record["html_url"],
            )
            clean.assert_called_once_with("tag")
            versions.assert_called_once_with("check", "--tag", "v1.2.3")
            self.assertTrue(
                self.github.api.call_args.kwargs["data"]["generate_release_notes"]
            )
            self.github.api.reset_mock(side_effect=True)
            self.github.api.return_value = self.record
            release.publish_release(self.github, self.environ, "v1.2.3")
            self.assertEqual(self.github.api.call_count, 1)

    def test_draft_and_prerelease_records_are_not_overwritten(self):
        with (
            patch.object(release, "validate_tag"),
            patch.object(release, "clean_checkout"),
            patch.object(release, "version_command"),
        ):
            for field in ("draft", "prerelease"):
                self.github.api.return_value = {**self.record, field: True}
                with self.subTest(field=field), self.assertRaises(release.ReleaseError):
                    release.publish_release(self.github, self.environ, "v1.2.3")
            self.assertTrue(
                all(
                    call.kwargs.get("method", "GET") == "GET"
                    for call in self.github.api.call_args_list
                )
            )


if __name__ == "__main__":
    unittest.main()
