"""Same-schema migration uses original fields, exact changes and no edit pipeline.

Fixtures here are current neutral builds and explicit synthetic mutations.
They are not historical editor-save evidence.
"""

import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
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


class MigrationFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(TOOL, module_name="migration_tests")
        cls.migration = cls.loaded.migration
        cls.fixtures = {version: cls.loaded.build.build_tree(linear_spec(2, version=version))
                        for version in ("1", "2", "3")}

    def tree(self, version="2"):
        return copy.deepcopy(self.fixtures[version])

    def pool(self, tree):
        return self.loaded.document.find_pool(tree)

    def cells(self, tree, kind):
        return [cell for cell in tree.iter("mxCell") if cell.get("data-kind") == kind]

    def plan(self, tree, accept=False):
        return self.migration.plan_migration(ET.tostring(tree.getroot()),
                                              accept_unverified_baseline=accept)


class MigrationPlanTests(MigrationFixture):

    def test_healthy_versions_and_old_or_absent_stamp_need_no_copy(self):
        for version in ("1", "2", "3"):
            for stamp in ("0.1.0", None):
                with self.subTest(version=version, stamp=stamp):
                    tree = self.tree(version)
                    if stamp is None:
                        self.pool(tree).attrib.pop("data-tool-version")
                    else:
                        self.pool(tree).set("data-tool-version", stamp)
                    report = self.plan(tree, accept=True)
                    self.assertEqual(report["classification"], "not-needed")
                    self.assertEqual(report["planned_changes"], [])
                    self.assertFalse(report["can_write"])
                    self.assertFalse(report["baseline_accepted"])
                    self.assertIsNone(report["output"])
                    self.assertIsNone(report["validation"]["serialized"])

    def test_automatic_repairs_have_exact_values_order_and_original_state(self):
        for absent in (("data-lane-order",), ("data-model-hash-version",),
                       ("data-lane-order", "data-model-hash-version")):
            with self.subTest(absent=absent):
                tree = self.tree()
                pool = self.pool(tree)
                stored_hash = pool.get("data-model-hash")
                pool.set("data-tool-version", "0.1.0")
                for name in absent:
                    del pool.attrib[name]
                report = self.plan(tree)
                self.assertEqual(report["classification"], "automatic")
                self.assertTrue(report["can_write"])
                changes = report["planned_changes"]
                self.assertEqual([item["attribute"] for item in changes], [*absent, "data-tool-version"])
                self.assertTrue(all(item["cell_id"] == pool.get("id") for item in changes))
                self.assertTrue(all(item["old_value"] is None and not item["old_present"] for item in changes[:-1]))
                self.assertEqual(changes[-1]["old_value"], "0.1.0")
                self.assertEqual(changes[-1]["new_value"], self.loaded.contracts.TOOL_VERSION)
                candidate = self.migration._apply_exact_changes(tree, changes)
                self.assertEqual(self.pool(candidate).get("data-model-hash"), stored_hash)
                self.assertEqual(self.loaded.metadata.semantic_model_document(candidate), report["semantic_summary"])

    def test_missing_hash_requires_this_invocations_explicit_acceptance(self):
        tree = self.tree()
        self.pool(tree).attrib.pop("data-model-hash")
        without = self.plan(tree)
        accepted = self.plan(tree, True)
        self.assertEqual(without["classification"], "confirmation-required")
        self.assertTrue(without["baseline_acceptance_required"])
        self.assertFalse(without["can_write"])
        self.assertFalse(without["baseline_accepted"])
        self.assertTrue(accepted["can_write"])
        self.assertTrue(accepted["baseline_accepted"])
        self.assertEqual(without["validation"]["source"]["quality_gate_passed"], False)
        self.assertTrue(without["validation"]["projected"]["quality_gate_passed"])
        self.assertFalse(self.plan(tree)["baseline_accepted"])

    def test_v1_historical_schema_and_main_path_absence_is_retained(self):
        # This is an explicit mutation of a current fixture, not a claimed old file.
        tree = self.tree("1")
        for name in ("data-schema-version", "data-main-path", *self.migration.REPAIR_ATTRIBUTES):
            self.pool(tree).attrib.pop(name, None)
        report = self.plan(tree, True)
        self.assertEqual(report["classification"], "confirmation-required")
        self.assertTrue(report["can_write"])
        self.assertEqual(report["semantic_summary"]["schema_version"], "1")
        self.assertEqual(report["semantic_summary"]["main_path"], [])
        self.assertEqual([item["attribute"] for item in report["planned_changes"]], list(self.migration.REPAIR_ATTRIBUTES))
        result = self.migration._apply_exact_changes(tree, report["planned_changes"])
        self.assertNotIn("data-schema-version", self.pool(result).attrib)
        self.assertNotIn("data-main-path", self.pool(result).attrib)

    def test_core_field_absence_cannot_be_replaced_by_reader_defaults(self):
        fields = {"node": ("data-node-type", "data-lane-id", "data-rank", "data-semantic-id", "parent"),
                  "edge": ("data-edge-type", "data-route", "data-from", "data-to", "source", "target")}
        for version in ("1", "2", "3"):
            for kind, names in fields.items():
                for name in names:
                    with self.subTest(version=version, kind=kind, name=name):
                        tree = self.tree(version)
                        self.pool(tree).attrib.pop("data-model-hash")
                        self.cells(tree, kind)[0].attrib.pop(name)
                        report = self.plan(tree, True)
                        self.assertEqual(report["classification"], "unsafe")
                        self.assertFalse(report["can_write"])
                        self.assertFalse(report["baseline_accepted"])
                        self.assertEqual(report["planned_changes"], [])

    def test_missing_edge_type_can_pass_legacy_strict_but_migration_rejects(self):
        tree = self.tree()
        self.cells(tree, "edge")[0].attrib.pop("data-edge-type")
        self.assertTrue(self.loaded.validation.validate_tree(tree)["quality_gate_passed"])
        self.assertEqual(self.plan(tree)["classification"], "unsafe")

    def test_node_lane_mirror_and_native_endpoints_are_independently_checked(self):
        tree = self.tree()
        self.cells(tree, "node")[0].set("data-lane-id", "wrong")
        self.assertTrue(self.loaded.validation.validate_tree(tree)["model_hash_matches"])
        self.assertEqual(self.plan(tree, True)["classification"], "unsafe")
        tree = self.tree()
        self.cells(tree, "edge")[0].set("source", self.cells(tree, "node")[-1].get("id"))
        self.assertEqual(self.plan(tree, True)["classification"], "unsafe")

    def test_unsupported_versions_and_empty_values_never_act_as_absence(self):
        for name, values in {"data-schema-version": ("", "9"),
                             "data-model-hash-version": ("", "9"),
                             "data-model-hash": ("", "0" * 64),
                             "data-lane-order": ("", "[]", '["lane-a","lane-a"]')}.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    tree = self.tree()
                    self.pool(tree).set(name, value)
                    report = self.plan(tree, True)
                    self.assertEqual(report["classification"], "unsafe")
                    self.assertFalse(report["can_write"])

    def test_v3_required_fields_and_mixed_schema_cannot_be_defaulted(self):
        for name in (*self.migration.V3_POOL_ATTRIBUTES, "data-main-path", "data-schema-version"):
            tree = self.tree("3")
            del self.pool(tree).attrib[name]
            self.pool(tree).attrib.pop("data-model-hash")
            with self.subTest(name=name):
                self.assertEqual(self.plan(tree, True)["classification"], "unsafe")
        tree = self.tree("2")
        self.pool(tree).set("data-behavior-pattern", "linear")
        self.assertEqual(self.plan(tree, True)["classification"], "unsafe")

    def test_native_and_same_kind_duplicates_rejected_before_reader_collapse(self):
        for duplicate_native in (True, False):
            tree = self.tree()
            duplicate = copy.deepcopy(self.cells(tree, "node")[0])
            if not duplicate_native:
                duplicate.set("id", "separate-native-id")
            self.loaded.document.graph_root(tree).append(duplicate)
            self.assertEqual(self.plan(tree, True)["classification"], "unsafe")

    def test_valid_cross_kind_same_name_and_arbitrary_native_ids_survive(self):
        tree = self.tree()
        pool = self.pool(tree)
        lane = self.cells(tree, "lane")[0]
        lane.set("data-semantic-id", pool.get("data-semantic-id"))
        lane.set("id", "editor-generated-native")
        for node in self.cells(tree, "node"):
            node.set("data-lane-id", pool.get("data-semantic-id"))
            node.set("parent", lane.get("id"))
        pool.attrib.pop("data-lane-order")
        pool.attrib.pop("data-model-hash")
        report = self.plan(tree, True)
        self.assertEqual(report["classification"], "confirmation-required")
        self.assertTrue(report["can_write"])

    def test_lane_order_uses_original_root_entries_without_geometry_sort(self):
        tree = self.tree()
        root = self.loaded.document.graph_root(tree)
        lane = self.cells(tree, "lane")[0]
        extra = copy.deepcopy(lane)
        extra.set("id", "second-native")
        extra.set("data-semantic-id", "lane-b")
        extra.find("mxGeometry").set("x", "240")
        root.insert(list(root).index(lane), extra)
        pool = self.pool(tree)
        pool.attrib.pop("data-lane-order")
        pool.attrib.pop("data-model-hash")
        report = self.plan(tree, True)
        change = next(item for item in report["planned_changes"] if item["attribute"] == "data-lane-order")
        self.assertEqual(json.loads(change["new_value"]), ["lane-b", "lane-a"])
        self.assertEqual(lane.find("mxGeometry").get("x"), "0")

    def test_unsupported_drawing_and_unsafe_hash_coexist_without_masking_drift(self):
        tree = self.tree()
        root = self.loaded.document.graph_root(tree)
        ET.SubElement(root, "mxCell", {"id": "unmanaged", "vertex": "1", "parent": "1"})
        self.assertEqual(self.plan(tree)["classification"], "unsupported")
        self.pool(tree).set("data-model-hash", "f" * 64)
        report = self.plan(tree, True)
        self.assertEqual(report["classification"], "unsafe")
        codes = [item["code"] for item in report["reasons"]]
        self.assertIn("migration/unsupported-drawing-content", codes)
        self.assertIn("migration/model-hash-mismatch", codes)

    def test_ordinary_compressed_and_multiple_page_inputs_are_unsupported(self):
        inputs = [b'<mxfile><diagram><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram></mxfile>',
                  b'<mxfile><diagram>compressed</diagram></mxfile>',
                  b'<mxGraphModel><root/></mxGraphModel>']
        tree = self.tree()
        tree.getroot().append(copy.deepcopy(tree.find("diagram")))
        inputs.append(ET.tostring(tree.getroot()))
        for data in inputs:
            self.assertEqual(self.migration.plan_migration(data)["classification"], "unsupported")

    def test_wrapper_with_readable_pool_does_not_mask_unsafe_raw_identity(self):
        tree = self.tree()
        root = self.loaded.document.graph_root(tree)
        wrapper = ET.SubElement(root, "object", {"id": "wrapped"})
        ET.SubElement(wrapper, "mxCell", {"parent": "1", "vertex": "1"})
        self.cells(tree, "node")[0].attrib.pop("data-rank")
        report = self.plan(tree, True)
        self.assertEqual(report["classification"], "unsafe")
        self.assertIn("migration/unsupported-document", [reason["code"] for reason in report["reasons"]])
        self.assertIn("migration/unsafe-metadata", [reason["code"] for reason in report["reasons"]])

    def test_comments_pi_entities_and_namespaces_are_rejected_from_original_bytes(self):
        data = ET.tostring(self.tree().getroot())
        cases = [b'<!-- before -->' + data, data + b'<?after value?>',
                 data.replace(b'<root>', b'<root><!-- middle -->'),
                 b'<!DOCTYPE mxfile [<!ENTITY custom "content">]>' + data,
                 data.replace(b'<mxfile ', b'<mxfile xmlns:v="urn:vendor" ', 1)]
        for raw in cases:
            with self.subTest(raw=raw[:80]):
                report = self.migration.plan_migration(raw)
                self.assertEqual(report["classification"], "unsupported")
                self.assertEqual(report["reasons"][0]["code"], "migration/unprotected-xml-payload")

    def test_xml_declaration_builtin_entities_and_xml_space_keep_normal_meaning(self):
        tree = self.tree()
        pool = self.pool(tree)
        pool.attrib.pop("data-model-hash")
        pool.set("value", "A & B")
        tree.getroot().set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        raw = b'<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(tree.getroot())
        report = self.migration.plan_migration(raw, accept_unverified_baseline=True)
        self.assertEqual(report["classification"], "confirmation-required")
        self.assertEqual(report["semantic_summary"]["title"], "A & B")

    def test_ambiguous_json_and_nonfinite_geometry_are_rejected(self):
        tree = self.tree("3")
        self.pool(tree).set("data-groups", '[{"id":"a","id":"b"}]')
        self.assertEqual(self.plan(tree, True)["classification"], "unsafe")
        for value in ("NaN", "Infinity", "bad"):
            tree = self.tree()
            self.cells(tree, "node")[0].find("mxGeometry").set("x", value)
            self.assertEqual(self.plan(tree, True)["classification"], "unsafe")
        tree = self.tree()
        node = self.cells(tree, "node")[0]
        node.append(copy.deepcopy(node.find("mxGeometry")))
        self.assertEqual(self.plan(tree, True)["classification"], "unsafe")

    def test_optional_v3_intent_remains_absent_and_opaque_payload_is_preserved(self):
        tree = self.tree("3")
        for node in self.cells(tree, "node"):
            node.attrib.pop("data-slot", None)
        for edge in self.cells(tree, "edge"):
            edge.attrib.pop("data-flow-role", None)
        self.pool(tree).attrib.pop("data-model-hash")
        extension = ET.SubElement(self.cells(tree, "node")[0], "vendor", {"scope": "opaque"})
        extension.text, extension.tail = "  text  ", "  tail  "
        geom = self.cells(tree, "node")[0].find("mxGeometry")
        opaque_geom = ET.SubElement(geom, "vendor", {"x": "opaque"})
        ET.SubElement(opaque_geom, "mxPoint", {"x": "opaque"})
        ET.SubElement(geom, "mxPoint", {"as": "vendorPayload", "x": "opaque"})
        report = self.plan(tree, True)
        self.assertEqual(report["classification"], "confirmation-required")
        self.assertTrue(report["can_write"])
        self.assertTrue(all("slot" not in node for node in report["semantic_summary"]["nodes"]))

    def test_projected_quality_blockers_are_retained_without_reclassification(self):
        tree = self.tree()
        self.pool(tree).attrib.pop("data-model-hash")
        self.cells(tree, "node")[1].set("value", "Very long text " * 50)
        report = self.plan(tree, True)
        self.assertEqual(report["classification"], "confirmation-required")
        self.assertFalse(report["can_write"])
        self.assertTrue(report["validation"]["projected"]["warnings"])

    def test_plan_does_not_mutate_or_serialize_or_enter_edit_pipeline(self):
        tree = self.tree("3")
        self.pool(tree).attrib.pop("data-model-hash")
        original = ET.tostring(tree.getroot())
        with ExitStack() as stack:
            for owner, name in ((self.loaded.build, "build_tree"), (self.loaded.roundtrip, "patch_tree"),
                                (self.loaded.metadata, "refresh_managed_metadata"),
                                (self.loaded.construction, "normalize_phase_layering"),
                                (self.loaded.document, "write_tree"), (ET.ElementTree, "write")):
                stack.enter_context(mock.patch.object(owner, name, side_effect=AssertionError(name)))
            report = self.migration._plan_tree(tree, self.migration._receipt(), True)
        self.assertTrue(report["can_write"])
        self.assertEqual(ET.tostring(tree.getroot()), original)

    def test_dry_run_file_identity_and_optional_sha_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.drawio"
            raw = ET.tostring(self.tree().getroot())
            path.write_bytes(raw)
            report, code = self.migration.dry_run(path)
            self.assertEqual(code, 0)
            self.assertEqual(report["input"]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(list(Path(temp).iterdir()), [path])
            report, code = self.migration.dry_run(path, expected_input_sha256="stale")
            self.assertEqual(code, 2)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/input-sha256-mismatch")
            path.write_bytes(b"<broken>")
            report, code = self.migration.dry_run(path)
            self.assertEqual(code, 2)
            self.assertIsNotNone(report["input"])
            self.assertEqual(report["reasons"][-1]["code"], "input/drawio-xml-invalid")

    def test_cli_dry_run_returns_new_envelope_and_no_new_files(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.drawio"
            path.write_bytes(ET.tostring(self.tree().getroot()))
            result = subprocess.run([sys.executable, "-B", str(TOOL), "migrate", "--input", str(path), "--dry-run"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["operation"], "migrate")
            self.assertEqual(report["migration_rule_version"], "1")
            self.assertEqual(report["producing_tool_version"], "0.8.0")
            self.assertEqual(list(Path(temp).iterdir()), [path])


class MigrationDeliveryTests(MigrationFixture):
    def source(self, directory, *, version="2", missing="data-model-hash"):
        tree = self.tree(version)
        if missing:
            self.pool(tree).attrib.pop(missing)
        path = directory / "before.drawio"
        data = ET.tostring(tree.getroot())
        path.write_bytes(data)
        return path, data

    def deliver(self, source, output, **options):
        options.setdefault("expected_input_sha256", hashlib.sha256(source.read_bytes()).hexdigest())
        options.setdefault("accept_unverified_baseline", True)
        return self.migration.migrate_file(source, output, **options)

    def test_native_geometry_binding_controls_and_refusals(self):
        spec = linear_spec(2, version="2")
        spec["phases"] = [{"id": "phase-a", "label": "Phase A", "from_rank": 1, "to_rank": 3}]
        control = self.loaded.build.build_tree(spec)
        self.pool(control).attrib.pop("data-lane-order")
        for kind in ("pool", "lane", "node", "edge", "phase"):
            for mutation in ("unrelated-opaque", "missing-as", "wrong-as", "competing-before",
                             "competing-after", "wrong-tag"):
                with self.subTest(kind=kind, mutation=mutation), tempfile.TemporaryDirectory() as temp:
                    directory = Path(temp)
                    tree = copy.deepcopy(control)
                    cell = self.cells(tree, kind)[0]
                    geom = cell.find("mxGeometry")
                    if mutation == "unrelated-opaque":
                        vendor = ET.SubElement(cell, "vendor", {"as": "vendorMetadata"})
                        ET.SubElement(vendor, "mxGeometry", {"as": "geometry", "x": "opaque"})
                    elif mutation == "missing-as":
                        geom.attrib.pop("as")
                    elif mutation == "wrong-as":
                        geom.set("as", "vendor")
                    elif mutation.startswith("competing"):
                        competitor = ET.Element("vendor", {"as": "geometry"})
                        cell.insert(0 if mutation == "competing-before" else len(cell), competitor)
                    else:
                        geom.tag = "vendor"
                    source, output = directory / "before.drawio", directory / "after.drawio"
                    original = ET.tostring(tree.getroot())
                    source.write_bytes(original)
                    dry, dry_code = self.migration.dry_run(source)
                    report, code = self.deliver(source, output, accept_unverified_baseline=False)
                    if mutation == "unrelated-opaque":
                        self.assertEqual((dry_code, dry["classification"], dry["can_write"]), (0, "automatic", True))
                        self.assertEqual(code, 0, report)
                        self.assertTrue(report["written"])
                        comparison, compare_code = self.migration.compare_migration_files(source, output)
                        self.assertEqual(compare_code, 0, comparison)
                        self.assertTrue(comparison["preserved"])
                        saved = ET.fromstring(output.read_bytes())
                        saved_cell = next(entry for entry in saved.iter("mxCell") if entry.get("id") == cell.get("id"))
                        self.assertEqual(ET.tostring(saved_cell.find("vendor")), ET.tostring(cell.find("vendor")))
                    else:
                        self.assertEqual((dry_code, dry["classification"], dry["can_write"]), (0, "unsafe", False))
                        self.assertEqual(code, 2, report)
                        self.assertEqual(report["reasons"][-1]["code"], "migration/input-unsafe")
                        self.assertFalse(report["written"])
                        self.assertIsNone(report["delivery"])
                        self.assertFalse(output.exists())
                        comparison, compare_code = self.migration.compare_migration_files(source, source)
                        self.assertEqual(compare_code, 2, comparison)
                        self.assertEqual(comparison["reasons"][-1]["code"], "migration/input-unsafe")
                        self.assertFalse(comparison["preserved"])
                    self.assertEqual(source.read_bytes(), original)
                    self.assertFalse(list(directory.glob("*.candidate")))

    def test_success_is_strict_exact_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, data = self.source(directory)
            output = directory / "nested" / "after.drawio"
            with mock.patch.object(self.loaded.document.os, "replace", side_effect=AssertionError("replace forbidden")):
                report, code = self.deliver(source, output)
            self.assertEqual(code, 0, report)
            self.assertTrue(report["written"])
            self.assertTrue(report["delivery"]["committed"])
            self.assertEqual(report["delivery"]["temporary_cleanup"], "removed")
            self.assertTrue(report["validation"]["serialized"]["quality_gate_passed"])
            self.assertTrue(report["preservation"]["preserved"])
            self.assertEqual(report["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(source.read_bytes(), data)
            self.assertFalse(list(directory.rglob("*.candidate")))
            another = directory / "unnecessary.drawio"
            repeated, code = self.deliver(output, another)
            self.assertEqual((code, repeated["classification"], repeated["written"]), (0, "not-needed", False))
            self.assertFalse(another.exists())

    def test_sha_and_acceptance_are_required_for_write(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, data = self.source(directory)
            output = directory / "after.drawio"
            for options, expected_code in (({"expected_input_sha256": None}, "delivery/input-sha256-required"),
                                           ({"expected_input_sha256": "stale"}, "delivery/input-sha256-mismatch"),
                                           ({"accept_unverified_baseline": False}, "migration/baseline-acceptance-required")):
                with self.subTest(options=options):
                    report, code = self.deliver(source, output, **options)
                    self.assertEqual(code, 2)
                    self.assertEqual(report["reasons"][-1]["code"], expected_code)
                    self.assertFalse(report["written"])
                    self.assertFalse(output.exists())
                    self.assertEqual(source.read_bytes(), data)

    def test_existing_output_and_all_alias_forms_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, data = self.source(directory)
            symlink = directory / "source-link.drawio"
            symlink.symlink_to(source)
            hardlink = directory / "source-hard.drawio"
            os.link(source, hardlink)
            existing = directory / "existing.drawio"
            existing.write_bytes(b"sentinel")
            dangling = directory / "dangling.drawio"
            dangling.symlink_to(directory / "absent.drawio")
            for output in (source, symlink, hardlink, existing, dangling, directory):
                report, code = self.deliver(source, output)
                self.assertEqual(code, 2, (output, report))
                self.assertFalse(report["written"])
                self.assertNotIn("use-force", report["reasons"][-1]["supported_fixes"])
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual(existing.read_bytes(), b"sentinel")
            self.assertTrue(dangling.is_symlink())
            self.assertFalse(dangling.exists())

    def test_input_symlink_is_allowed_when_identity_remains_current(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, data = self.source(directory)
            link = directory / "link.drawio"
            link.symlink_to(source)
            report, code = self.deliver(link, directory / "after.drawio")
            self.assertEqual(code, 0, report)
            self.assertTrue(report["written"])
            self.assertEqual(source.read_bytes(), data)

    def test_output_appearing_at_atomic_publication_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, data = self.source(directory)
            output = directory / "after.drawio"
            real_link = os.link
            def occupy_then_link(candidate, destination):
                Path(destination).write_bytes(b"concurrent target")
                return real_link(candidate, destination)
            with mock.patch.object(self.loaded.document.os, "link", side_effect=occupy_then_link):
                report, code = self.deliver(source, output)
            self.assertEqual(code, 2)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/output-exists")
            self.assertFalse(report["delivery"]["committed"])
            self.assertEqual(report["delivery"]["temporary_cleanup"], "removed")
            self.assertEqual(output.read_bytes(), b"concurrent target")
            self.assertEqual(source.read_bytes(), data)
            self.assertFalse(list(directory.glob("*.candidate")))

    def test_input_bytes_or_identity_changes_before_commit_reject_delivery(self):
        for replace_identity in (False, True):
            with self.subTest(replace_identity=replace_identity), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                source, original = self.source(directory)
                output = directory / "after.drawio"
                actual_validate = self.migration._validate_migration_candidate
                calls = 0
                def change_source(*args):
                    nonlocal calls
                    result = actual_validate(*args)
                    calls += 1
                    if calls == 2:
                        if replace_identity:
                            replacement = directory / "replacement"
                            replacement.write_bytes(original)
                            os.replace(replacement, source)
                        else:
                            source.write_bytes(original + b" ")
                    return result
                with mock.patch.object(self.migration, "_validate_migration_candidate", side_effect=change_source):
                    report, code = self.deliver(source, output)
                self.assertEqual(code, 2, report)
                self.assertEqual(report["reasons"][-1]["code"], "delivery/input-changed")
                self.assertFalse(output.exists())
                self.assertEqual(source.read_bytes(), original if replace_identity else original + b" ")

    def test_projected_quality_failure_is_exit_one_without_serialization(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            tree = self.tree()
            self.pool(tree).attrib.pop("data-model-hash")
            self.cells(tree, "node")[1].set("value", "Long text " * 100)
            source = directory / "before.drawio"
            source.write_bytes(ET.tostring(tree.getroot()))
            with mock.patch.object(self.loaded.document, "write_tree", side_effect=AssertionError("must not serialize")):
                report, code = self.deliver(source, directory / "after.drawio")
            self.assertEqual(code, 1, report)
            self.assertEqual(report["reasons"][-1]["code"], "delivery/strict-validation-failed")
            self.assertIsNone(report["validation"]["serialized"])
            self.assertFalse(report["written"])

    def test_serialized_comments_pi_malformed_xml_and_field_tampering_are_rejected(self):
        real_write = ET.ElementTree.write
        for mutation in ("comment", "pi", "malformed", "attribute"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                source, original = self.source(directory)
                output = directory / "after.drawio"
                def corrupt(tree, path, *args, **kwargs):
                    real_write(tree, path, *args, **kwargs)
                    candidate = Path(path)
                    raw = candidate.read_bytes()
                    if mutation == "comment":
                        raw += b"<!-- injected -->"
                    elif mutation == "pi":
                        raw += b"<?injected value?>"
                    elif mutation == "malformed":
                        raw += b"<broken>"
                    else:
                        raw = raw.replace(b'data-tool-version="0.8.0"', b'data-tool-version="wrong"')
                    candidate.write_bytes(raw)
                with mock.patch.object(ET.ElementTree, "write", new=corrupt):
                    report, code = self.deliver(source, output)
                self.assertEqual(code, 1, report)
                self.assertFalse(report["written"])
                self.assertFalse(report["preservation"]["preserved"])
                self.assertEqual(report["reasons"][-1]["code"], "delivery/candidate-preservation-failed")
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(output.exists())

    def test_candidate_byte_change_after_parsed_validation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, original = self.source(directory)
            output = directory / "after.drawio"
            validate = self.migration._validate_migration_candidate
            calls = 0
            def tamper_after_validation(*args):
                nonlocal calls
                result = validate(*args)
                calls += 1
                if calls == 2:
                    next(directory.glob("*.candidate")).write_bytes(b"not validated")
                return result
            with mock.patch.object(self.migration, "_validate_migration_candidate", side_effect=tamper_after_validation):
                report, code = self.deliver(source, output)
            self.assertEqual(code, 1, report)
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_projected_and_serialized_validation_results_are_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, original = self.source(directory)
            output = directory / "after.drawio"
            actual = self.loaded.validation.validate_tree
            counter = 0
            # Mark only the actual candidate's final check; independent planning
            # may perform additional validations before that check.
            candidate_check = self.migration._validate_migration_candidate
            def fail_serialized(*args):
                nonlocal counter
                counter += 1
                if counter == 2:
                    result = actual(args[2])
                    result["quality_gate_passed"] = False
                    result["warnings"] = ["injected serialized warning"]
                    raise self.loaded.contracts.DiagramError(
                        "serialized warning", code="delivery/strict-validation-failed",
                        evidence={"validation": result})
                return candidate_check(*args)
            with mock.patch.object(self.migration, "_validate_migration_candidate", side_effect=fail_serialized):
                report, code = self.deliver(source, output)
            self.assertEqual(code, 1)
            self.assertTrue(report["validation"]["projected"]["quality_gate_passed"])
            self.assertFalse(report["validation"]["serialized"]["quality_gate_passed"])
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_write_fsync_and_link_failures_leave_no_output(self):
        for boundary in ("write", "fsync", "link"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                source, original = self.source(directory)
                output = directory / "after.drawio"
                owner = ET.ElementTree if boundary == "write" else self.loaded.document.os
                with mock.patch.object(owner, boundary, side_effect=OSError("injected failure")):
                    report, code = self.deliver(source, output)
                self.assertEqual(code, 2, report)
                self.assertEqual(report["reasons"][-1]["code"], "delivery/io-error")
                self.assertEqual(report["reasons"][-1]["message"], "injected failure")
                self.assertFalse(report["written"])
                self.assertFalse(report["delivery"]["committed"])
                self.assertEqual(report["delivery"]["temporary_cleanup"], "removed")
                self.assertIsNone(report["delivery"]["output"])
                self.assertFalse(Path(report["delivery"]["temporary_path"]).exists())
                self.assertFalse(output.exists())
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(list(directory.glob("*.candidate")))

    def test_postcommit_cleanup_failure_reports_delivered_artifact_without_reread(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, original = self.source(directory)
            output = directory / "after.drawio"
            actual_unlink = Path.unlink
            actual_receipt = self.loaded.document.file_receipt
            def unlink(path, *args, **kwargs):
                if path.suffix == ".candidate":
                    raise OSError("cleanup denied")
                return actual_unlink(path, *args, **kwargs)
            def receipt(path):
                if path == output:
                    raise AssertionError("No output receipt read after commit")
                return actual_receipt(path)
            with mock.patch.object(Path, "unlink", new=unlink), mock.patch.object(self.loaded.document, "file_receipt", side_effect=receipt):
                report, code = self.deliver(source, output)
            self.assertEqual(code, 0, report)
            self.assertTrue(report["written"])
            self.assertTrue(report["delivery"]["committed"])
            self.assertEqual(report["delivery"]["temporary_cleanup"], "failed")
            self.assertEqual(report["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(report["reasons"][-1]["severity"], "warning")
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(Path(report["delivery"]["temporary_path"]).exists())

    def test_delivery_evidence_keeps_unexpected_failure_and_legacy_exception_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, original = self.source(directory)
            output = directory / "after.drawio"
            with mock.patch.object(self.loaded.document.os, "fsync", side_effect=RuntimeError("unexpected")):
                report, code = self.deliver(source, output)
            self.assertEqual(code, 3, report)
            self.assertEqual(report["reasons"][-1]["code"], "internal/unexpected")
            self.assertEqual(report["reasons"][-1]["evidence"]["exception_type"], "RuntimeError")
            self.assertEqual(report["delivery"]["temporary_cleanup"], "removed")
            self.assertFalse(report["written"])
            self.assertFalse(output.exists())
            failure = OSError("legacy fsync failure")
            with mock.patch.object(self.loaded.document.os, "fsync", side_effect=failure):
                with self.assertRaises(OSError) as caught:
                    self.loaded.document.write_tree(self.tree(), output)
            self.assertIs(caught.exception, failure)
            self.assertFalse(hasattr(caught.exception, "_migration_delivery"))
            self.assertFalse(output.exists())
            self.assertFalse(list(directory.glob("*.candidate")))
            self.assertEqual(source.read_bytes(), original)

    def test_precommit_cleanup_failure_retains_temporary_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, original = self.source(directory)
            output = directory / "after.drawio"
            actual_unlink = Path.unlink
            def unlink(path, *args, **kwargs):
                if path.suffix == ".candidate":
                    raise OSError("cleanup denied")
                return actual_unlink(path, *args, **kwargs)
            with mock.patch.object(Path, "unlink", new=unlink), mock.patch.object(self.loaded.document.os, "link", side_effect=OSError("link failed")):
                report, code = self.deliver(source, output)
            self.assertEqual(code, 2, report)
            self.assertFalse(report["written"])
            self.assertFalse(report["delivery"]["committed"])
            self.assertEqual(report["delivery"]["temporary_cleanup"], "failed")
            self.assertTrue(Path(report["delivery"]["temporary_path"]).exists())
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_changed_output_parent_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, original = self.source(directory)
            first, second = directory / "first", directory / "second"
            first.mkdir()
            second.mkdir()
            link = directory / "parent"
            link.symlink_to(first, target_is_directory=True)
            validate = self.migration._validate_migration_candidate
            calls = 0
            def replace_parent(*args):
                nonlocal calls
                result = validate(*args)
                calls += 1
                if calls == 2:
                    link.unlink()
                    link.symlink_to(second, target_is_directory=True)
                return result
            with mock.patch.object(self.migration, "_validate_migration_candidate", side_effect=replace_parent):
                report, code = self.deliver(source, link / "after.drawio")
            self.assertEqual(code, 2, report)
            self.assertFalse((first / "after.drawio").exists())
            self.assertFalse((second / "after.drawio").exists())
            self.assertEqual(source.read_bytes(), original)


class MigrationComparisonTests(MigrationFixture):
    def pair(self, *, version="2", missing="data-model-hash"):
        before = self.tree(version)
        if missing:
            self.pool(before).attrib.pop(missing)
        plan = self.plan(before)
        after = copy.deepcopy(before)
        for change in plan["planned_changes"]:
            self.pool(after).set(change["attribute"], change["new_value"])
        return before, after

    def compare(self, before, after):
        before_bytes = before if isinstance(before, bytes) else ET.tostring(before.getroot())
        after_bytes = after if isinstance(after, bytes) else ET.tostring(after.getroot())
        with tempfile.TemporaryDirectory() as temp:
            before_path, after_path = Path(temp) / "before.drawio", Path(temp) / "after.drawio"
            before_path.write_bytes(before_bytes)
            after_path.write_bytes(after_bytes)
            result = self.migration.compare_migration_files(before_path, after_path)
            self.assertEqual(before_path.read_bytes(), before_bytes)
            self.assertEqual(after_path.read_bytes(), after_bytes)
            self.assertEqual(len(list(Path(temp).iterdir())), 2)
            return result

    def test_exact_pairs_all_versions_and_no_baseline_acceptance(self):
        for version in ("1", "2", "3"):
            for missing, classification in (("data-model-hash", "confirmation-required"),
                                             ("data-lane-order", "automatic"), (None, "not-needed")):
                with self.subTest(version=version, missing=missing):
                    before, after = self.pair(version=version, missing=missing)
                    report, code = self.compare(before, after)
                    self.assertEqual(code, 0, report)
                    self.assertTrue(report["preserved"])
                    self.assertEqual(report["classification"], classification)
                    self.assertEqual(report["operation"], "compare-migration")
                    self.assertNotIn("baseline_accepted", report)
                    self.assertNotIn("validation", report)
                    self.assertEqual(report["before"]["sha256"], hashlib.sha256(ET.tostring(before.getroot())).hexdigest())

    def test_not_needed_preserves_old_or_absent_stamp_and_checks_whole_document(self):
        for stamp in (None, "0.1.0"):
            before, _ = self.pair(missing=None)
            if stamp is None:
                self.pool(before).attrib.pop("data-tool-version")
            else:
                self.pool(before).set("data-tool-version", stamp)
            self.assertEqual(self.compare(before, copy.deepcopy(before))[1], 0)
            after = copy.deepcopy(before)
            self.pool(after).set("data-tool-version", self.loaded.contracts.TOOL_VERSION)
            report, code = self.compare(before, after)
            self.assertEqual(code, 1, report)
            self.assertFalse(report["preserved"])

    def test_exact_attribute_cell_presence_value_and_tool_version_are_required(self):
        mutations = ("wrong-value", "missing", "wrong-cell", "extra-pool-field",
                     "unchanged-allowed-field", "wrong-tool-version", "native-id")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                before, after = self.pair()
                pool = self.pool(after)
                if mutation == "wrong-value":
                    pool.set("data-model-hash", "f" * 64)
                elif mutation == "missing":
                    pool.attrib.pop("data-model-hash")
                elif mutation == "wrong-cell":
                    self.cells(after, "node")[0].set("data-model-hash", pool.attrib.pop("data-model-hash"))
                elif mutation == "extra-pool-field":
                    pool.set("unrelated", "extra")
                elif mutation == "unchanged-allowed-field":
                    pool.set("data-lane-order", '[ "lane-a" ]')
                elif mutation == "wrong-tool-version":
                    pool.set("data-tool-version", "0.7.1")
                else:
                    pool.set("id", "different-native-id")
                report, code = self.compare(before, after)
                self.assertEqual(code, 1, report)
                self.assertTrue(report["differences"])

    def test_full_native_semantic_geometry_label_order_and_opaque_payload_are_protected(self):
        before, _ = self.pair()
        node = self.cells(before, "node")[0]
        node.set("vendor-state", "keep")
        payload = ET.SubElement(node, "vendor", {"value": "opaque"})
        payload.text, payload.tail = " text ", " tail "
        edge_geometry = self.cells(before, "edge")[0].find("mxGeometry")
        point = edge_geometry.find("mxPoint[@as='offset']")
        if point is None:
            point = ET.SubElement(edge_geometry, "mxPoint", {"as": "offset", "x": "4", "y": "5"})
        point.set("vendor", "keep")
        for mutation in ("semantic-id", "parent", "style", "label", "native-geometry", "point",
                         "point-extension", "opaque-attribute", "opaque-text", "opaque-tail",
                         "opaque-child", "extra-child", "sibling-order", "top-attribute"):
            with self.subTest(mutation=mutation):
                after = copy.deepcopy(before)
                for change in self.plan(before)["planned_changes"]:
                    self.pool(after).set(change["attribute"], change["new_value"])
                node = self.cells(after, "node")[0]
                if mutation in {"semantic-id", "parent", "style", "label", "opaque-attribute"}:
                    key = {"semantic-id": "data-semantic-id", "parent": "parent", "style": "style",
                           "label": "value", "opaque-attribute": "vendor-state"}[mutation]
                    node.set(key, "changed")
                elif mutation == "native-geometry":
                    node.find("mxGeometry").set("x", "999")
                elif mutation in {"point", "point-extension"}:
                    self.cells(after, "edge")[0].find("mxGeometry/mxPoint[@as='offset']").set(
                        "x" if mutation == "point" else "vendor", "999")
                elif mutation == "opaque-text":
                    node.find("vendor").text = "changed"
                elif mutation == "opaque-tail":
                    node.find("vendor").tail = "changed"
                elif mutation == "opaque-child":
                    node.remove(node.find("vendor"))
                elif mutation == "extra-child":
                    ET.SubElement(node, "unexpected")
                elif mutation == "sibling-order":
                    root = self.loaded.document.graph_root(after)
                    root.remove(node)
                    root.append(node)
                else:
                    after.getroot().set("vendor", "changed")
                report, code = self.compare(before, after)
                self.assertEqual(code, 1, report)
                self.assertFalse(report["preserved"])
                self.assertTrue(report["differences"])

    def test_formatting_boundary_allows_known_indentation_but_retains_xml_space(self):
        before, after = self.pair()
        ET.indent(after, space="    ")
        self.assertEqual(self.compare(before, after)[1], 0)
        before.getroot().set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        after = copy.deepcopy(before)
        for change in self.plan(before)["planned_changes"]:
            self.pool(after).set(change["attribute"], change["new_value"])
        self.assertEqual(self.compare(before, after)[1], 0)
        after.getroot().text = " "
        self.assertEqual(self.compare(before, after)[1], 1)

    def test_valid_unprotectable_after_is_difference_and_malformed_after_is_input_failure(self):
        before, after = self.pair()
        after_bytes = ET.tostring(after.getroot())
        for payload in (after_bytes + b"<!--comment-->", after_bytes + b"<?pi value?>",
                        b'<!DOCTYPE mxfile [<!ENTITY extra "x">]>' + after_bytes,
                        after_bytes.replace(b"<mxfile ", b'<mxfile xmlns:v="urn:vendor" ', 1)):
            report, code = self.compare(before, payload)
            self.assertEqual(code, 1, report)
            self.assertEqual(report["differences"][0]["field"], "xml-payload")
            self.assertIsNotNone(report["after"])
        report, code = self.compare(before, b"<broken>")
        self.assertEqual(code, 2, report)
        self.assertEqual(report["classification"], "confirmation-required")
        self.assertIsNotNone(report["after"])
        self.assertEqual(report["reasons"][-1]["code"], "input/drawio-xml-invalid")

    def test_ineligible_or_unreadable_inputs_are_exit_two_with_available_context(self):
        before, after = self.pair()
        self.pool(before).set("data-model-hash", "wrong")
        report, code = self.compare(before, after)
        self.assertEqual((code, report["classification"]), (2, "unsafe"))
        self.assertEqual(report["reasons"][-1]["code"], "migration/input-unsafe")
        self.assertIsNotNone(report["before"])
        self.assertIsNotNone(report["after"])
        report, code = self.compare(b"<mxfile><diagram>compressed</diagram></mxfile>", after)
        self.assertEqual((code, report["classification"]), (2, "unsupported"))
        report, code = self.compare(b"<broken>", after)
        self.assertEqual(code, 2)
        self.assertIsNotNone(report["before"])
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            report, code = self.migration.compare_migration_files(missing, missing)
            self.assertEqual(code, 2)
            self.assertIsNone(report["before"])

    def test_comparison_does_not_certify_strict_or_invoke_edit_pipeline(self):
        before, _ = self.pair()
        self.cells(before, "node")[1].set("value", "Long text " * 100)
        plan = self.plan(before)
        self.assertFalse(plan["validation"]["projected"]["quality_gate_passed"])
        after = copy.deepcopy(before)
        for change in plan["planned_changes"]:
            self.pool(after).set(change["attribute"], change["new_value"])
        with ExitStack() as stack:
            for owner, name in ((self.loaded.build, "build_tree"), (self.loaded.roundtrip, "patch_tree"),
                                (self.loaded.metadata, "refresh_managed_metadata"),
                                (self.loaded.construction, "normalize_phase_layering"),
                                (self.loaded.document, "write_tree")):
                stack.enter_context(mock.patch.object(owner, name, side_effect=AssertionError(name)))
            report, code = self.compare(before, after)
        self.assertEqual(code, 0, report)
        self.assertNotIn("quality_gate_passed", report)

    def test_production_writer_rejects_apply_helper_injection_via_independent_compare(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, output = directory / "before.drawio", directory / "after.drawio"
            before, _ = self.pair()
            raw = ET.tostring(before.getroot())
            source.write_bytes(raw)
            apply = self.migration._apply_exact_changes
            def injected_apply(tree, changes):
                candidate = apply(tree, changes)
                self.pool(candidate).set("injected-by-writer", "not-allowed")
                return candidate
            with mock.patch.object(self.migration, "_apply_exact_changes", side_effect=injected_apply):
                report, code = self.migration.migrate_file(
                    source, output, expected_input_sha256=hashlib.sha256(raw).hexdigest(),
                    accept_unverified_baseline=True)
            self.assertEqual(code, 1, report)
            self.assertFalse(report["written"])
            self.assertFalse(report["preservation"]["preserved"])
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), raw)

    def test_cli_migration_compare_is_mutually_exclusive_and_default_envelope_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            before, after = self.pair()
            before_path, after_path = directory / "before.drawio", directory / "after.drawio"
            before_path.write_bytes(ET.tostring(before.getroot()))
            after_path.write_bytes(ET.tostring(after.getroot()))
            command = [sys.executable, "-B", str(TOOL), "compare", "--before", str(before_path), "--after", str(after_path)]
            result = subprocess.run([*command, "--migration"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["operation"], "compare-migration")
            result = subprocess.run([*command, "--migration", "--changes", "unused.json"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            result = subprocess.run(command, capture_output=True, text=True)
            expected = self.loaded.roundtrip.compare_trees(before, after)
            self.assertEqual(json.loads(result.stdout), expected)
            self.assertEqual(result.returncode, 0 if expected["preserved"] else 1)


if __name__ == "__main__":
    unittest.main()
