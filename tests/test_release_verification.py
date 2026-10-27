"""Exercise real release staging without recursively running this test suite."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import release_check as release
import verify_release as verification

VERSION = release.read_tool_version(ROOT)


def make_release(root: Path) -> None:
    for name in release.EXPECTED_RELEASE_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Neutral release fixture\n", encoding="utf-8")
    (root / "release-files.json").write_text(
        json.dumps(sorted(release.EXPECTED_RELEASE_FILES)), encoding="utf-8")
    (root / release.TOOL_VERSION_FILE).write_text(f"TOOL_VERSION = {VERSION!r}\n", encoding="utf-8")
    (root / ".codex-plugin/plugin.json").write_text(json.dumps({"version": VERSION}), encoding="utf-8")
    (root / ".claude-plugin/marketplace.json").write_text(json.dumps({"plugins": [{
        "name": "product-swimlane-drawio", "version": VERSION,
    }]}), encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## Unreleased\n\n## [{VERSION}] - 2026-10-27\n\nNotes.\n",
        encoding="utf-8")
    (root / "README.md").write_text("Neutral release ✓\n", encoding="utf-8")
    (root / "examples/request-review/preview.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\x00")


class ReleaseVersionTests(unittest.TestCase):
    def test_runtime_version_is_read_without_executing_the_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_release(root)
            path = root / release.TOOL_VERSION_FILE
            path.write_text(f"TOOL_VERSION = {VERSION!r}\nraise AssertionError('must not execute')\n")
            self.assertEqual(release.read_tool_version(root), VERSION)
            for value in ("TOOL_VERSION = get_version()", "TOOL_VERSION = True",
                          "TOOL_VERSION = 'unknown'", "TOOL_VERSION = '1.0.0'\nTOOL_VERSION = '2.0.0'",
                          "TOOL_VERSION = '1.0.0'\nTOOL_VERSION += '.1'"):
                with self.subTest(value=value):
                    path.write_text(value, encoding="utf-8")
                    self.assertFalse(release.check_versions(root)["valid"])

    def test_metadata_and_exact_tag_must_agree_with_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_release(root)
            self.assertTrue(release.check_versions(root, tag=f"v{VERSION}")["valid"])
            for tag in (VERSION, f"refs/tags/v{VERSION}", "v999.0.0", ""):
                with self.subTest(tag=tag):
                    self.assertFalse(release.check_versions(root, tag=tag)["valid"])
            mutations = {
                ".codex-plugin/plugin.json": json.dumps({"version": "999.0.0"}),
                ".claude-plugin/marketplace.json": json.dumps({"plugins": [{
                    "name": "product-swimlane-drawio", "version": "999.0.0",
                }]}),
                "CHANGELOG.md": f"## [999.0.0] - 2026-10-28\n## [{VERSION}] - 2026-10-27\n",
            }
            for name, text in mutations.items():
                with self.subTest(name=name):
                    path = root / name
                    original = path.read_bytes()
                    path.write_text(text, encoding="utf-8")
                    self.assertFalse(release.check_versions(root)["valid"])
                    path.write_bytes(original)
            (root / "CHANGELOG.md").write_text(f"## [{VERSION}] - 2026-02-30\n")
            self.assertFalse(release.check_versions(root)["valid"])

    def test_inventory_checks_stay_separate_and_cli_tag_enables_version_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_release(root)
            (root / ".codex-plugin/plugin.json").write_text("{}")
            self.assertTrue(release.check_release(root, source=False)["valid"])
            output = io.StringIO()
            with mock.patch.object(sys, "argv", ["release_check.py", "--root", str(root),
                                                "--export", "--tag", f"v{VERSION}"]), \
                    contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_result:
                release.main()
            self.assertEqual(exit_result.exception.code, 1)
            self.assertFalse(json.loads(output.getvalue())["versions"]["valid"])


class ReleaseVerificationTests(unittest.TestCase):
    def test_zip_contains_the_actual_reviewed_files_and_runs_from_clean_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            make_release(root)
            original = verification.file_hashes(root)
            staged = []

            def inspect_extraction(extracted, *, discover_skill, timeout):
                staged.append(extracted)
                self.assertFalse(extracted.is_relative_to(root))
                self.assertFalse((extracted / ".git").exists())
                self.assertEqual(verification.file_hashes(extracted), original)
                self.assertEqual((extracted / "README.md").read_bytes(), (root / "README.md").read_bytes())
                with zipfile.ZipFile(extracted.parent / "release.zip") as package:
                    self.assertEqual(set(package.namelist()), release.EXPECTED_RELEASE_FILES)
                    self.assertEqual(len(package.namelist()), len(release.EXPECTED_RELEASE_FILES))
                self.assertEqual((discover_skill, timeout), (False, 25))

            with mock.patch.object(verification, "run_checks", side_effect=inspect_extraction) as run:
                result = verification.verify_release(root, tag=f"v{VERSION}", timeout=25)
            self.assertTrue(result["valid"], result)
            run.assert_called_once()
            self.assertEqual(verification.file_hashes(root), original)
            self.assertFalse(staged[0].exists())

    def test_source_inventory_versions_and_tag_fail_before_running_tests(self):
        mutations = ("missing", "unapproved", "symlink", "cache", "version", "tag", "manifest")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                make_release(root)
                tag = f"v{VERSION}"
                if mutation == "missing":
                    (root / "README.md").unlink()
                elif mutation == "unapproved":
                    (root / "private.txt").write_text("unapproved")
                elif mutation == "symlink":
                    (root / "README.md").unlink()
                    (root / "README.md").symlink_to(root / "LICENSE")
                elif mutation == "cache":
                    (root / "tools/__pycache__").mkdir()
                    (root / "tools/__pycache__/release_check.pyc").write_bytes(b"cache")
                elif mutation == "version":
                    (root / ".codex-plugin/plugin.json").write_text('{"version": "999.0.0"}')
                elif mutation == "tag":
                    tag = "v999.0.0"
                else:
                    (root / "release-files.json").write_text('["../outside"]')
                with mock.patch.object(verification, "run_checks") as run:
                    result = verification.verify_release(root, tag=tag)
                self.assertFalse(result["valid"], result)
                run.assert_not_called()

    def test_successful_test_exit_does_not_hide_modified_files_or_extra_output(self):
        for mutation in ("changed", "removed", "extra", "cache", "empty-directory", "symlink"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                make_release(root)
                original = verification.file_hashes(root)

                def mutate(extracted, **kwargs):
                    if mutation == "changed":
                        (extracted / "README.md").write_text("changed")
                    elif mutation == "removed":
                        (extracted / "README.md").unlink()
                    elif mutation == "extra":
                        (extracted / "output.txt").write_text("extra")
                    elif mutation == "cache":
                        (extracted / "tools/__pycache__").mkdir()
                        (extracted / "tools/__pycache__/release_check.pyc").write_bytes(b"cache")
                    elif mutation == "empty-directory":
                        (extracted / "unused-output").mkdir()
                    else:
                        (extracted / "README.md").unlink()
                        (extracted / "README.md").symlink_to(extracted / "LICENSE")

                with mock.patch.object(verification, "run_checks", side_effect=mutate):
                    result = verification.verify_release(root)
                self.assertFalse(result["valid"], result)
                self.assertEqual(verification.file_hashes(root), original)

    def test_failed_or_timed_out_checks_still_inspect_the_extraction(self):
        for failure in (subprocess.CalledProcessError(1, ["python"]),
                        subprocess.TimeoutExpired(["python"], 1)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                make_release(root)

                def fail(extracted, **kwargs):
                    (extracted / "README.md").write_text("changed before failure")
                    raise failure

                with mock.patch.object(verification, "run_checks", side_effect=fail):
                    result = verification.verify_release(root)
                self.assertFalse(result["valid"], result)
                self.assertIn("Release file changed during verification: README.md", result["errors"])

    def test_commands_use_no_bytecode_pinned_discovery_and_bounded_timeouts(self):
        with mock.patch.object(verification.subprocess, "run") as run:
            verification.run_checks(ROOT, discover_skill=True, timeout=25)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][:4], [sys.executable, "-B", "-m", "unittest"])
        self.assertEqual(run.call_args_list[0].kwargs, {"cwd": ROOT, "check": True, "timeout": 25})
        self.assertEqual(run.call_args_list[1].args[0], ["npx", "--yes", "skills@1.5.22", "add", ".", "--list"])
        self.assertEqual(run.call_args_list[1].kwargs["timeout"], 60)
        with mock.patch.object(verification, "run_checks") as run:
            self.assertFalse(verification.verify_release(ROOT, timeout=0)["valid"])
        run.assert_not_called()
