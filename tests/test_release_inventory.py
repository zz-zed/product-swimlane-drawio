import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_check", ROOT / "tools/release_check.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
CORE_MODULES = ("contracts", "geometry", "document", "metadata", "sizing",
                "routing_policy", "ports", "labels", "routing", "routing_adapter", "validation")


class ReleaseInventoryTests(unittest.TestCase):
    def make_source(self, root, *, source=True):
        if source:
            subprocess.run(["git", "init", "-q", str(root)], check=True)
        for name in release.EXPECTED_RELEASE_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("neutral text\n", encoding="utf-8")
        (root / ".gitignore").write_text(".DS_Store\n__pycache__/\n*.pyc\n", encoding="utf-8")

    def test_source_ignores_only_nonpublished_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_source(root)
            self.assertTrue(release.check_release(root)["valid"])
            skill = root / release.SKILL_PREFIX
            (skill / ".DS_Store").write_bytes(b"\xff\xfe")
            (skill / "scripts/__pycache__").mkdir()
            (skill / "scripts/__pycache__/tool.pyc").write_bytes(b"\xff\xfe")
            for module in CORE_MODULES:
                nested = skill / f"scripts/swimlane_core/__pycache__/{module}.cpython-314.pyc"
                nested.parent.mkdir(exist_ok=True)
                nested.write_bytes(b"\xff\xfe")
            self.assertTrue(release.check_release(root)["valid"])
            (skill / "unknown.bin").write_bytes(b"\xff\xfe")
            self.assertIn("Unexpected packaged file: unknown.bin", release.check_release(root)["errors"])

    def test_tracked_cache_is_pollution_even_when_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_source(root)
            cache = root / release.SKILL_PREFIX / ".DS_Store"
            cache.write_bytes(b"\xff")
            subprocess.run(["git", "-C", str(root), "add", "-f", str(cache)], check=True)
            self.assertFalse(release.check_release(root)["valid"])
            for module in CORE_MODULES:
                name = f"scripts/swimlane_core/__pycache__/{module}.cpython-314.pyc"
                nested = root / release.SKILL_PREFIX / name
                nested.parent.mkdir(exist_ok=True)
                nested.write_bytes(b"\xff")
                subprocess.run(["git", "-C", str(root), "add", "-f", str(nested)], check=True)
                self.assertIn(f"Unexpected packaged file: {name}", release.check_release(root)["errors"])

    def test_export_rejects_caches_and_unknown_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_source(root, source=False)
            self.assertTrue(release.check_release(root, source=False)["valid"])
            for name in (".DS_Store", "unknown.bin", "scripts/__pycache__/tool.pyc",
                         *(f"scripts/swimlane_core/__pycache__/{module}.cpython-314.pyc" for module in CORE_MODULES)):
                path = root / release.SKILL_PREFIX / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"\xff")
                self.assertIn(f"Unexpected packaged file: {name}", release.check_release(root, source=False)["errors"])

    def test_missing_or_unknown_core_module_fails_against_exact_inventory(self):
        for source in (True, False):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_source(root, source=source)
                core = root / release.SKILL_PREFIX / "scripts/swimlane_core"
                for module in CORE_MODULES:
                    path = core / f"{module}.py"
                    original = path.read_bytes()
                    path.unlink()
                    self.assertIn(f"Missing packaged file: scripts/swimlane_core/{module}.py",
                                  release.check_release(root, source=source)["errors"])
                    path.write_bytes(original)
                (core / "unreviewed_helper.py").write_text("# not allowlisted\n", encoding="utf-8")
                self.assertIn("Unexpected packaged file: scripts/swimlane_core/unreviewed_helper.py",
                              release.check_release(root, source=source)["errors"])

    def test_outer_release_payload_and_extra_skills_are_checked(self):
        for source in (True, False):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_source(root, source=source)
                for name in ("skills/extra/SKILL.md", "private/notes.md", "tests/fixtures/sample.drawio"):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("neutral", encoding="utf-8")
                    if source:
                        subprocess.run(["git", "-C", str(root), "add", "-f", str(path)], check=True)
                    self.assertIn(f"Unexpected release file: {name}", release.check_release(root, source=source)["errors"])

    def test_missing_binary_text_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_source(root)
            path = root / release.SKILL_PREFIX / "SKILL.md"
            path.write_bytes(b"\xff")
            self.assertIn("Invalid UTF-8 packaged text: SKILL.md", release.check_release(root)["errors"])
            path.unlink()
            self.assertIn("Missing packaged file: SKILL.md", release.check_release(root)["errors"])
            path.symlink_to(root / ".gitignore")
            self.assertIn("Not a regular packaged file: SKILL.md", release.check_release(root)["errors"])

    def test_git_failure_does_not_fall_back_to_cache_filtering(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(subprocess.CalledProcessError):
                release.skill_inventory(Path(temporary), source=True)

    def test_tracked_files_cannot_be_redirected_through_directory_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_source(root)
            subprocess.run(["git", "-C", str(root), "add", release.SKILL_PREFIX], check=True)
            scripts = root / release.SKILL_PREFIX / "scripts"
            scripts.rename(root / "redirected")
            scripts.symlink_to(root / "redirected", target_is_directory=True)
            self.assertIn("Not a regular packaged file: scripts/drawio_swimlane.py",
                          release.check_release(root)["errors"])

    def test_package_file_and_package_directory_symlinks_fail_closed(self):
        for source in (True, False):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_source(root, source=source)
                if source:
                    subprocess.run(["git", "-C", str(root), "add", release.SKILL_PREFIX], check=True)
                core = root / release.SKILL_PREFIX / "scripts/swimlane_core"
                for module in CORE_MODULES:
                    path = core / f"{module}.py"
                    redirected_file = root / f"redirected-{module}.py"
                    path.rename(redirected_file)
                    path.symlink_to(redirected_file)
                    self.assertIn(f"Not a regular packaged file: scripts/swimlane_core/{module}.py",
                                  release.check_release(root, source=source)["errors"])
                    path.unlink()
                    redirected_file.rename(path)
                redirected_directory = root / "redirected-core"
                core.rename(redirected_directory)
                core.symlink_to(redirected_directory, target_is_directory=True)
                errors = release.check_release(root, source=source)["errors"]
                if not source:
                    self.assertIn("Unexpected packaged file: scripts/swimlane_core", errors)
                for module in CORE_MODULES:
                    reason = "Not a regular packaged file" if source else "Missing packaged file"
                    self.assertIn(f"{reason}: scripts/swimlane_core/{module}.py", errors)
