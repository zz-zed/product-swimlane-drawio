"""Independent D2 tests for the serialized-candidate delivery boundary.

Fixtures are current neutral builds with explicit synthetic mutations.  The
tests exercise public migration entry points while injecting only at the
writer's opt-in I/O callbacks and operating-system publication boundaries.
"""

import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import linear_spec
from swimlane_loader import load_skill_modules

TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"


class D2DeliveryBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(TOOL, module_name="d2_delivery_boundaries")
        cls.migration = cls.loaded.migration
        cls.original_tree = cls.loaded.build.build_tree(linear_spec(2, version="2"))

    def make_source(self, directory: Path) -> tuple[Path, bytes]:
        tree = copy.deepcopy(self.original_tree)
        self.loaded.document.find_pool(tree).attrib.pop("data-model-hash")
        data = ET.tostring(tree.getroot())
        source = directory / "source.drawio"
        source.write_bytes(data)
        return source, data

    def migrate(self, source: Path, output: Path):
        return self.migration.migrate_file(
            source,
            output,
            expected_input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            accept_unverified_baseline=True,
        )

    def wrap_before_commit(self, action):
        """Run an action after candidate validation and before publication."""
        real_write_tree = self.loaded.document.write_tree

        def write_tree(tree, output, **options):
            production_check = options["before_commit"]

            def injected_check(candidate_path, output_receipt):
                action(candidate_path)
                production_check(candidate_path, output_receipt)

            return real_write_tree(tree, output, **{**options, "before_commit": injected_check})

        return write_tree

    def assert_removed_uncommitted_delivery(self, report, directory: Path):
        self.assertFalse(report["written"])
        self.assertIsNone(report["output"])
        self.assertIsNotNone(report["delivery"])
        self.assertFalse(report["delivery"]["committed"])
        self.assertEqual(report["delivery"]["temporary_cleanup"], "removed")
        self.assertIsNone(report["delivery"]["output"])
        self.assertEqual(report["delivery"]["diagnostics"], [])
        self.assertFalse(Path(report["delivery"]["temporary_path"]).exists())
        self.assertFalse(list(directory.glob("*.candidate")))

    def test_clean_delivery_records_commit_output_and_removed_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, source_bytes = self.make_source(directory)
            output = directory / "output.drawio"

            report, exit_code = self.migrate(source, output)

            self.assertEqual(exit_code, 0, report)
            self.assertTrue(report["written"])
            self.assertTrue(report["delivery"]["committed"])
            self.assertEqual(report["delivery"]["temporary_cleanup"], "removed")
            self.assertEqual(report["delivery"]["diagnostics"], [])
            self.assertEqual(report["output"]["bytes"], report["delivery"]["output"]["bytes"])
            self.assertEqual(report["output"]["sha256"], report["delivery"]["output"]["sha256"])
            self.assertEqual(Path(report["delivery"]["output"]["path"]).resolve(), output.resolve())
            self.assertEqual(report["output"]["path"], str(output))
            self.assertEqual(report["output"]["bytes"], len(output.read_bytes()))
            self.assertEqual(report["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertFalse(Path(report["delivery"]["temporary_path"]).exists())
            self.assertFalse(list(directory.glob("*.candidate")))
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_fsync_and_link_failures_record_removed_candidate_without_output(self):
        for boundary in ("fsync", "link"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, source_bytes = self.make_source(directory)
                output = directory / "output.drawio"

                with mock.patch.object(
                    self.loaded.document.os,
                    boundary,
                    side_effect=OSError(f"injected {boundary} failure"),
                ):
                    report, exit_code = self.migrate(source, output)

                self.assertEqual(exit_code, 2, report)
                self.assertEqual(report["reasons"][-1]["code"], "delivery/io-error")
                self.assert_removed_uncommitted_delivery(report, directory)
                self.assertFalse(output.exists())
                self.assertEqual(source.read_bytes(), source_bytes)
                if boundary == "fsync":
                    self.assertIsNone(report["validation"]["serialized"])
                else:
                    self.assertTrue(report["validation"]["serialized"]["quality_gate_passed"])

    def test_precommit_failure_with_cleanup_failure_retains_temporary_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, source_bytes = self.make_source(directory)
            output = directory / "output.drawio"
            real_unlink = Path.unlink

            def reject_candidate_cleanup(path, *args, **kwargs):
                if path.suffix == ".candidate":
                    raise OSError("injected cleanup failure")
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", new=reject_candidate_cleanup), mock.patch.object(
                self.loaded.document.os, "link", side_effect=OSError("injected link failure")
            ):
                report, exit_code = self.migrate(source, output)

            self.assertEqual(exit_code, 2, report)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/io-error")
            self.assertFalse(report["written"])
            self.assertIsNone(report["output"])
            self.assertFalse(report["delivery"]["committed"])
            self.assertEqual(report["delivery"]["temporary_cleanup"], "failed")
            self.assertIsNone(report["delivery"]["output"])
            self.assertEqual(len(report["delivery"]["diagnostics"]), 1)
            self.assertEqual(report["delivery"]["diagnostics"][0]["code"], "delivery/io-error")
            self.assertTrue(Path(report["delivery"]["temporary_path"]).exists())
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_postcommit_cleanup_failure_retains_truthful_output_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, source_bytes = self.make_source(directory)
            output = directory / "output.drawio"
            real_unlink = Path.unlink

            def reject_candidate_cleanup(path, *args, **kwargs):
                if path.suffix == ".candidate":
                    raise OSError("injected cleanup failure")
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", new=reject_candidate_cleanup):
                report, exit_code = self.migrate(source, output)

            self.assertEqual(exit_code, 0, report)
            self.assertTrue(report["written"])
            self.assertTrue(report["delivery"]["committed"])
            self.assertEqual(report["delivery"]["temporary_cleanup"], "failed")
            self.assertEqual(report["delivery"]["output"]["bytes"], report["output"]["bytes"])
            self.assertEqual(report["delivery"]["output"]["sha256"], report["output"]["sha256"])
            self.assertEqual(Path(report["delivery"]["output"]["path"]).resolve(), output.resolve())
            self.assertEqual(report["output"]["path"], str(output))
            self.assertEqual(report["output"]["bytes"], len(output.read_bytes()))
            self.assertEqual(report["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(report["reasons"][-1]["code"], "delivery/io-error")
            self.assertEqual(report["reasons"][-1]["severity"], "warning")
            self.assertEqual(report["delivery"]["diagnostics"][-1], report["reasons"][-1])
            self.assertTrue(Path(report["delivery"]["temporary_path"]).exists())
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_candidate_bytes_changed_after_validation_callback_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, source_bytes = self.make_source(directory)
            output = directory / "output.drawio"

            def tamper(candidate_path):
                candidate_path.write_bytes(candidate_path.read_bytes() + b" ")

            with mock.patch.object(
                self.loaded.document, "write_tree", side_effect=self.wrap_before_commit(tamper)
            ):
                report, exit_code = self.migrate(source, output)

            self.assertEqual(exit_code, 1, report)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/candidate-preservation-failed")
            self.assert_removed_uncommitted_delivery(report, directory)
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_atomic_output_race_preserves_concurrent_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, source_bytes = self.make_source(directory)
            output = directory / "output.drawio"
            concurrent_bytes = b"concurrent target"
            real_link = os.link

            def occupy_then_link(candidate, destination):
                Path(destination).write_bytes(concurrent_bytes)
                return real_link(candidate, destination)

            with mock.patch.object(self.loaded.document.os, "link", side_effect=occupy_then_link):
                report, exit_code = self.migrate(source, output)

            self.assertEqual(exit_code, 2, report)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/output-exists")
            self.assert_removed_uncommitted_delivery(report, directory)
            self.assertEqual(output.read_bytes(), concurrent_bytes)
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_hardlink_output_alias_is_rejected_without_touching_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, source_bytes = self.make_source(directory)
            output = directory / "source-hardlink.drawio"
            os.link(source, output)

            report, exit_code = self.migrate(source, output)

            self.assertEqual(exit_code, 2, report)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/input-output-alias")
            self.assertFalse(report["written"])
            self.assertEqual(output.read_bytes(), source_bytes)
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_source_replaced_by_same_bytes_and_new_inode_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, source_bytes = self.make_source(directory)
            output = directory / "output.drawio"

            def replace_source(_candidate_path):
                replacement = directory / "same-bytes-new-inode.drawio"
                replacement.write_bytes(source_bytes)
                os.replace(replacement, source)

            with mock.patch.object(
                self.loaded.document, "write_tree", side_effect=self.wrap_before_commit(replace_source)
            ):
                report, exit_code = self.migrate(source, output)

            self.assertEqual(exit_code, 2, report)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/input-changed")
            self.assertFalse(report["written"])
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertFalse(report["reasons"][-1]["evidence"]["object_identity_matches"])

    def test_output_parent_reinterpretation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, source_bytes = self.make_source(directory)
            first = directory / "first"
            second = directory / "second"
            first.mkdir()
            second.mkdir()
            parent = directory / "parent"
            parent.symlink_to(first, target_is_directory=True)
            output = parent / "output.drawio"

            def reinterpret_parent(_candidate_path):
                parent.unlink()
                parent.symlink_to(second, target_is_directory=True)

            with mock.patch.object(
                self.loaded.document, "write_tree", side_effect=self.wrap_before_commit(reinterpret_parent)
            ):
                report, exit_code = self.migrate(source, output)

            self.assertEqual(exit_code, 2, report)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/io-error")
            self.assertEqual(report["reasons"][-1]["evidence"]["reason"], "output-parent-changed")
            self.assertFalse(report["written"])
            self.assertFalse((first / output.name).exists())
            self.assertFalse((second / output.name).exists())
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_source_comment_and_processing_instruction_are_rejected_from_actual_bytes(self):
        for label, prefix in (
            ("comment", b"<!-- injected source comment -->"),
            ("processing-instruction", b"<?injected source?>"),
        ):
            with self.subTest(payload=label), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, source_bytes = self.make_source(directory)
                source.write_bytes(prefix + source_bytes)
                actual_bytes = source.read_bytes()
                output = directory / "output.drawio"

                report, exit_code = self.migrate(source, output)

                self.assertEqual(exit_code, 2, report)
                self.assertEqual(report["classification"], "unsupported")
                self.assertEqual(report["reasons"][-1]["code"], "migration/input-unsupported")
                raw = [reason for reason in report["reasons"]
                       if reason["code"] == "migration/unprotected-xml-payload"]
                self.assertEqual(len(raw), 1)
                self.assertEqual(raw[0]["evidence"]["payload_kinds"], [label])
                self.assertFalse(report["written"])
                self.assertFalse(output.exists())
                self.assertEqual(source.read_bytes(), actual_bytes)

    def test_projected_and_serialized_strict_failures_fill_only_the_executed_slot(self):
        for failing_stage in ("projected", "serialized"):
            with self.subTest(stage=failing_stage), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, source_bytes = self.make_source(directory)
                output = directory / "output.drawio"
                production_validate = self.migration._validate_migration_candidate

                def inject_strict_failure(before_data, accepted, candidate, semantic_snapshot):
                    stage = "projected" if candidate is accepted else "serialized"
                    if stage == failing_stage:
                        validation = self.loaded.validation.validate_tree(candidate)
                        validation["quality_gate_passed"] = False
                        validation["warnings"] = [f"injected {stage} failure"]
                        raise self.loaded.contracts.DiagramError(
                            f"injected {stage} strict failure",
                            code="delivery/strict-validation-failed",
                            evidence={"validation": validation},
                        )
                    return production_validate(before_data, accepted, candidate, semantic_snapshot)

                with mock.patch.object(
                    self.migration,
                    "_validate_migration_candidate",
                    side_effect=inject_strict_failure,
                ):
                    report, exit_code = self.migrate(source, output)

                self.assertEqual(exit_code, 1, report)
                self.assertEqual(report["reasons"][-1]["code"], "delivery/strict-validation-failed")
                self.assertFalse(report["validation"][failing_stage]["quality_gate_passed"])
                other_stage = "serialized" if failing_stage == "projected" else "projected"
                if failing_stage == "projected":
                    self.assertIsNone(report["validation"][other_stage])
                else:
                    self.assertTrue(report["validation"][other_stage]["quality_gate_passed"])
                self.assertFalse(report["written"])
                self.assertFalse(output.exists())
                self.assertEqual(source.read_bytes(), source_bytes)


if __name__ == "__main__":
    unittest.main()
