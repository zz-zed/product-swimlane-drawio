import binascii
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zlib


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_chunk(kind: bytes, data: bytes, *, crc: int | None = None) -> bytes:
    checksum = binascii.crc32(data, binascii.crc32(kind)) & 0xFFFFFFFF if crc is None else crc
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def png_bytes(width: int = 4, height: int = 3, *, color_type: int = 2,
              bit_depth: int = 8, compression: int = 0, filtering: int = 0,
              interlace: int = 0, filters: list[int] | None = None,
              before_idat: list[tuple[bytes, bytes]] | None = None,
              idat_parts: int = 1, after_idat: list[tuple[bytes, bytes]] | None = None,
              raw_rows: bytes | None = None, compressed: bytes | None = None,
              iend: bytes = b"", trailing: bytes = b"") -> bytes:
    channels = 3 if color_type == 2 else 4
    if raw_rows is None:
        row_filters = filters or [0] * height
        raw_rows = b"".join(bytes([row_filters[index]]) + bytes(width * channels)
                            for index in range(height))
    packed = zlib.compress(raw_rows) if compressed is None else compressed
    if idat_parts < 1:
        parts = []
    else:
        cuts = [len(packed) * index // idat_parts for index in range(idat_parts + 1)]
        parts = [packed[cuts[index]:cuts[index + 1]] for index in range(idat_parts)]
    header = struct.pack(">IIBBBBB", width, height, bit_depth, color_type,
                         compression, filtering, interlace)
    return (PNG_SIGNATURE + png_chunk(b"IHDR", header)
            + b"".join(png_chunk(kind, data) for kind, data in (before_idat or []))
            + b"".join(png_chunk(b"IDAT", part) for part in parts)
            + b"".join(png_chunk(kind, data) for kind, data in (after_idat or []))
            + png_chunk(b"IEND", iend) + trailing)


def run_tool(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, "-B", str(TOOL), *args], cwd=ROOT,
                          capture_output=True, check=False)


class E4PreviewPngTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e4_preview_png_tests")
        cls.png = cls.loaded.preview_png

    def assert_png_error(self, raw: bytes, code: str) -> None:
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.png.inspect_png(raw)
        self.assertEqual(caught.exception.code, code, caught.exception.diagnostic())

    def test_rgb_rgba_filters_and_ancillary_chunks_are_validated_from_bytes(self) -> None:
        for color_type in (2, 6):
            for row_filter in range(5):
                with self.subTest(color_type=color_type, row_filter=row_filter):
                    raw = png_bytes(color_type=color_type, filters=[row_filter] * 3,
                                    before_idat=[(b"tEXt", b"inert $() https://example.test")],
                                    idat_parts=2)
                    result = self.png.inspect_png(raw)
                    self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
                    self.assertEqual((result["width"], result["height"]), (4, 3))
                    self.assertEqual(result["color_type"], color_type)
                    self.assertEqual(result["verification"], "png_integrity_only")

    def test_signature_chunk_length_crc_and_truncation_fail_closed(self) -> None:
        valid = png_bytes()
        cases = [
            b"not-png", valid[:-1], valid[:8] + struct.pack(">I", 0xFFFFFFFF) + valid[12:],
            valid[:29] + bytes([valid[29] ^ 1]) + valid[30:],
        ]
        for index, raw in enumerate(cases):
            with self.subTest(index=index):
                self.assert_png_error(raw, "preview/png-invalid")

    def test_ihdr_must_be_first_unique_and_exact(self) -> None:
        header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        first_wrong = PNG_SIGNATURE + png_chunk(b"tEXt", b"x") + png_chunk(b"IHDR", header)
        duplicate = PNG_SIGNATURE + png_chunk(b"IHDR", header) + png_chunk(b"IHDR", header)
        wrong_size = PNG_SIGNATURE + png_chunk(b"IHDR", header + b"x")
        for raw in (first_wrong, duplicate, wrong_size):
            self.assert_png_error(raw, "preview/png-invalid")

    def test_idat_must_be_present_and_contiguous(self) -> None:
        no_data = png_bytes(idat_parts=0)
        split = png_bytes(idat_parts=2, after_idat=[(b"IDAT", zlib.compress(b"unused"))])
        interrupted = png_bytes(idat_parts=1, after_idat=[(b"tEXt", b"x"), (b"IDAT", b"x")])
        for raw in (no_data, split, interrupted):
            self.assert_png_error(raw, "preview/png-invalid")

    def test_iend_is_unique_empty_final_and_has_no_trailing_data(self) -> None:
        for raw in (png_bytes(iend=b"x"), png_bytes(trailing=b"x"),
                    png_bytes(trailing=png_chunk(b"IEND", b""))):
            self.assert_png_error(raw, "preview/png-invalid")

    def test_zlib_eof_extra_stream_and_decoded_length_are_exact(self) -> None:
        correct = b"\x00" + b"\x00" * 3
        cases = [
            png_bytes(1, 1, compressed=b"not-zlib"),
            png_bytes(1, 1, compressed=zlib.compress(correct)[:-1]),
            png_bytes(1, 1, compressed=zlib.compress(correct) + zlib.compress(correct)),
            png_bytes(1, 1, raw_rows=correct[:-1]),
            png_bytes(1, 1, raw_rows=correct + b"x"),
        ]
        for raw in cases:
            self.assert_png_error(raw, "preview/png-invalid")

    def test_scanline_filter_must_be_between_zero_and_four(self) -> None:
        self.assert_png_error(png_bytes(filters=[5, 0, 0]), "preview/png-invalid")

    def test_unsupported_depth_color_methods_interlace_apng_and_critical_chunk(self) -> None:
        cases = [
            png_bytes(bit_depth=16), png_bytes(color_type=3), png_bytes(compression=1),
            png_bytes(filtering=1), png_bytes(interlace=1),
            png_bytes(before_idat=[(b"acTL", struct.pack(">II", 1, 0))]),
            png_bytes(before_idat=[(b"ABCD", b"x")]),
        ]
        for raw in cases:
            self.assert_png_error(raw, "preview/format-unsupported")

    def test_dimensions_pixels_and_raw_file_limits_are_inclusive(self) -> None:
        header_only_over = png_bytes(self.png.PNG_MAX_DIMENSION + 1, 1)
        self.assert_png_error(header_only_over, "review/resource-limit")
        pixels_over = png_bytes(8000, 4001, compressed=zlib.compress(b""))
        self.assert_png_error(pixels_over, "review/resource-limit")

        small = png_bytes(1, 1)
        filler_size = self.png.PNG_MAX_BYTES - len(small) - 12
        exact = png_bytes(1, 1, before_idat=[(b"tEXt", b"x" * filler_size)])
        self.assertEqual(len(exact), self.png.PNG_MAX_BYTES)
        self.assertEqual(self.png.inspect_png(exact)["bytes"], self.png.PNG_MAX_BYTES)
        self.assert_png_error(exact + b"x", "review/resource-limit")

    def test_invalid_reserved_chunk_type_and_palette_reject(self) -> None:
        reserved_lower = png_bytes(before_idat=[(b"texT", b"x")])
        bad_palette = png_bytes(before_idat=[(b"PLTE", b"xx")])
        duplicate_palette = png_bytes(before_idat=[(b"PLTE", b"xxx"), (b"PLTE", b"xxx")])
        for raw in (reserved_lower, bad_palette, duplicate_palette):
            self.assert_png_error(raw, "preview/png-invalid")


def review_spec() -> dict:
    return {
        "schema_version": "3", "title": "Neutral visual review",
        "behavior_pattern": "custom", "layout": {"profile": "review"},
        "lanes": [
            {"id": "lane-a", "label": "Lane A", "width": 260},
            {"id": "lane-b", "label": "Lane B", "width": 260},
        ],
        "nodes": [
            {"id": "start", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
            {"id": "decision", "lane": "lane-a", "rank": 2, "type": "decision", "label": "Choose"},
            {"id": "action", "lane": "lane-b", "rank": 3, "type": "process", "label": "Act"},
            {"id": "end", "lane": "lane-a", "rank": 4, "type": "end", "label": ""},
        ],
        "edges": [
            {"id": "enter", "from": "start", "to": "decision"},
            {"id": "choose", "from": "decision", "to": "action", "outcome": "go", "label": "Go"},
            {"id": "finish", "from": "action", "to": "end"},
        ],
        "main_path": ["start", "decision", "action", "end"],
    }


def workflow_spec() -> dict:
    return {
        "schema_version": "3", "title": "Neutral review workflow",
        "behavior_pattern": "linear", "layout": {"profile": "review"},
        "lanes": [{"id": "lane", "label": "Lane", "width": 260}],
        "nodes": [
            {"id": "start", "lane": "lane", "rank": 1, "type": "start", "label": ""},
            {"id": "step", "lane": "lane", "rank": 2, "type": "process", "label": "Check"},
            {"id": "end", "lane": "lane", "rank": 3, "type": "end", "label": ""},
        ],
        "edges": [
            {"id": "enter", "from": "start", "to": "step"},
            {"id": "finish", "from": "step", "to": "end"},
        ],
        "main_path": ["start", "step", "end"],
    }


class E4ReviewEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e4_review_evidence_tests")
        cls.review = cls.loaded.review_evidence
        cls.tree = cls.loaded.build.build_tree(review_spec())
        cls.validation = cls.loaded.validation.validate_tree(cls.tree)
        cls.index = cls.review.review_object_index(cls.tree, cls.validation)
        cls.artifact_sha = "a" * 64
        cls.prepared_sha = "b" * 64
        cls.report_sha = "c" * 64
        cls.full_sha = "d" * 64
        cls.run_record = {
            "run_id": "12345678-1234-4234-9234-123456789abc",
            "artifact": {"sha256": cls.artifact_sha},
            "context": {"sha256": None},
            "validation": {"strict_validation": "passed"},
        }

    def no_review(self, state: str = "not_run", reason: str | None = None) -> dict:
        return {"state": state, "reason": reason, "reviewer": None,
                "full_image_viewed": False, "viewed_previews": [], "issues": []}

    def base_report(self) -> dict:
        return {
            "visual_report_version": 1, "run_id": self.run_record["run_id"],
            "prepared_sha256": self.prepared_sha, "artifact_sha256": self.artifact_sha,
            "context_sha256": None, "round_index": 0, "export": None, "previews": [],
            "reviews": {"agent": self.no_review(), "human": self.no_review()},
        }

    def renderer(self) -> dict:
        return {"name": "draw.io", "version": "31.4.5", "profile_id": "profile-a", "fonts": None}

    def full_preview(self, *, mapping=None, width: int = 2000, height: int = 2000) -> dict:
        return {"id": "full", "kind": "full", "name": "full.png", "sha256": self.full_sha,
                "bytes": 100, "width": width, "height": height, "mapping": mapping}

    def passed_export(self, *, calibration=None) -> dict:
        return {
            "state": "passed", "reason": None, "renderer": self.renderer(),
            "command": ["draw.io", "--export", "diagram.drawio"], "exit_code": 0,
            "input_before_sha256": self.artifact_sha, "input_after_sha256": self.artifact_sha,
            "full_preview_sha256": self.full_sha, "stdout": None, "stderr": None,
            "calibration": calibration,
        }

    def reviewer(self, kind: str, state: str = "passed", *, issues=None,
                 full: bool = True, viewed=None, reason=None) -> dict:
        return {
            "state": state, "reason": reason,
            "reviewer": {"kind": kind, "name": None, "host": None, "model": None,
                         "tool_receipt_ids": [f"{kind}-receipt"]},
            "full_image_viewed": full, "viewed_previews": ["full"] if viewed is None else viewed,
            "issues": [] if issues is None else issues,
        }

    def target(self, kind: str, sid: str) -> dict:
        return {"kind": kind, "id": sid}

    def indexed(self, target: dict) -> dict:
        key = self.review.review_target_key(target)
        return next(item for item in self.index["objects"] if self.review.review_target_key(item["target"]) == key)

    def issue(self, target: dict, region: dict, *, issue_id="issue", code="visual/node-text-clipped",
              severity="note", binding="bound", uncertainty=None, preview_id="full", preview_sha=None,
              suggested_intent="manual-review") -> dict:
        return {
            "issue_id": issue_id, "code": code, "severity": severity, "targets": [target],
            "preview_id": preview_id, "preview_sha256": preview_sha or self.full_sha,
            "region": region, "observation": "Visible observation", "suggested_intent": suggested_intent,
            "binding": binding, "uncertainty": uncertainty,
        }

    def assert_review_error(self, data: dict, code: str, *, assess=False) -> None:
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            if assess:
                self.review.assess_visual_report(
                    data, self.run_record, self.index, self.prepared_sha, self.report_sha)
            else:
                self.review.ReviewValidator().report(data)
        self.assertEqual(caught.exception.code, code, caught.exception.diagnostic())

    def calibrated_report(self) -> dict:
        mapping = {"mapping_version": 1, "kind": "axis_aligned", "scale_x": 1.0, "scale_y": 1.0,
                   "translate_x": 0.0, "translate_y": 0.0, "calibration_id": "calibration"}
        pool = self.indexed(self.target("pool", "main"))
        node = self.indexed(self.target("node", "action"))
        anchors = []
        for target, item, anchor in (
            (self.target("pool", "main"), pool, "top_left"),
            (self.target("pool", "main"), pool, "top_right"),
            (self.target("node", "action"), node, "center"),
        ):
            x, y = self.review.review_anchor(item["bounds"], anchor)
            anchors.append({"target": target, "anchor": anchor, "pixel": {"x": x, "y": y}})
        calibration = {
            "calibration_version": 1, "id": "calibration", "profile_id": "profile-a",
            "artifact_sha256": self.artifact_sha, "preview_sha256": self.full_sha,
            "method": "externally_observed_anchors", "anchors": anchors, "tolerance_px": 2.0,
        }
        report = self.base_report()
        report["previews"] = [self.full_preview(mapping=mapping)]
        report["export"] = self.passed_export(calibration=calibration)
        return report

    def test_object_index_uses_absolute_parent_geometry_and_typed_outcome_links(self) -> None:
        keys = [self.review.review_target_key(item["target"]) for item in self.index["objects"]]
        self.assertEqual(keys, sorted(keys))
        self.assertIn(("outcome", "decision", "go"), keys)
        outcome = self.indexed({"kind": "outcome", "decision_id": "decision", "outcome_id": "go"})
        self.assertIsNone(outcome["native_id"])
        self.assertIsNone(outcome["bounds"])
        self.assertEqual(outcome["related_targets"], [self.target("edge", "choose"), self.target("node", "decision")])
        node = self.indexed(self.target("node", "action"))
        root = self.loaded.document.graph_root(self.tree)
        pool = self.loaded.document.find_pool(self.tree)
        lanes, nodes = self.loaded.document.lane_node_records(root, pool)
        pool_geom = self.loaded.document.parse_geometry(pool)
        lane_geom = lanes["lane-b"]["geometry"]
        node_geom = nodes["action"]["geometry"]
        self.assertEqual(node["bounds"]["x"], pool_geom["x"] + lane_geom["x"] + node_geom["x"])
        self.assertEqual(node["bounds"]["y"], pool_geom["y"] + lane_geom["y"] + node_geom["y"])
        edge = self.indexed(self.target("edge", "choose"))
        self.assertGreaterEqual(len(edge["path"]), 2)
        self.assertIsNotNone(edge["label_bounds"])
        self.assertEqual(self.index["validation"]["visual_review"], "not_available")

    def test_report_shape_versions_typed_refs_and_finite_numbers_fail_closed(self) -> None:
        cases = []
        missing = self.base_report(); missing.pop("reviews"); cases.append((missing, "schema/required"))
        unknown = self.base_report(); unknown["future"] = {}; cases.append((unknown, "schema/unknown-field"))
        boolean = self.base_report(); boolean["visual_report_version"] = True; cases.append((boolean, "schema/type"))
        version = self.base_report(); version["visual_report_version"] = 2; cases.append((version, "review/version-unsupported"))
        later = self.base_report(); later["round_index"] = 1; cases.append((later, "review/version-unsupported"))
        for data, code in cases:
            with self.subTest(code=code):
                self.assert_review_error(data, code)

        report = self.calibrated_report()
        report["previews"][0]["mapping"]["scale_x"] = float("nan")
        self.assert_review_error(report, "schema/type")

    def test_baseline_identities_are_independently_bound(self) -> None:
        for key in ("run_id", "prepared_sha256", "artifact_sha256", "context_sha256"):
            with self.subTest(key=key):
                report = self.base_report()
                report[key] = "wrong" if key == "run_id" else ("e" * 64 if key != "context_sha256" else "e" * 64)
                self.assert_review_error(report, "review/baseline-mismatch", assess=True)

    def test_unperformed_unavailable_failed_and_passed_states_remain_distinct(self) -> None:
        report = self.base_report()
        receipt = self.review.assess_visual_report(report, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertEqual(receipt["states"], {
            "strict_validation": "passed", "preview_export": "not_run",
            "agent_image_review": "not_run", "human_review": "not_run",
        })
        self.assertEqual(receipt["trust"], "externally_supplied")
        self.assertFalse(receipt["can_repair"])

        unavailable = self.base_report()
        unavailable["export"] = {key: None for key in (
            "reason", "renderer", "command", "exit_code", "input_before_sha256", "input_after_sha256",
            "full_preview_sha256", "stdout", "stderr", "calibration")}
        unavailable["export"].update(state="not_available", reason="Renderer unavailable")
        unavailable["reviews"]["agent"] = self.no_review("not_available", "Viewer unavailable")
        receipt = self.review.assess_visual_report(unavailable, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertEqual(receipt["states"]["preview_export"], "not_available")
        self.assertEqual(receipt["states"]["agent_image_review"], "not_available")

    def test_not_available_export_cannot_claim_an_attempt_or_image(self) -> None:
        for key, value in (("command", ["draw.io"]), ("exit_code", 0),
                           ("full_preview_sha256", self.full_sha),
                           ("calibration", self.calibrated_report()["export"]["calibration"])):
            with self.subTest(key=key):
                report = self.base_report()
                report["export"] = {name: None for name in (
                    "reason", "renderer", "command", "exit_code", "input_before_sha256", "input_after_sha256",
                    "full_preview_sha256", "stdout", "stderr", "calibration")}
                report["export"].update(state="not_available", reason="Unavailable")
                report["export"][key] = value
                self.assert_review_error(report, "review/report-inconsistent", assess=True)

    def test_passed_export_requires_full_image_and_complete_declared_identity(self) -> None:
        report = self.base_report()
        report["export"] = self.passed_export()
        self.assert_review_error(report, "review/report-inconsistent", assess=True)
        report["previews"] = [self.full_preview()]
        receipt = self.review.assess_visual_report(report, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertEqual(receipt["states"]["preview_export"], "passed")
        self.assertEqual(receipt["mapping"], [{"preview_id": "full", "verification": "unverified_spatial"}])

    def test_calibration_requires_exact_non_degenerate_observed_anchors(self) -> None:
        report = self.calibrated_report()
        receipt = self.review.assess_visual_report(report, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertEqual(receipt["mapping"], [{"preview_id": "full", "verification": "declared_anchors_consistent"}])
        mutations = [
            ("tolerance", "review/mapping-invalid", lambda r: r["export"]["calibration"].update(tolerance_px=2.1)),
            ("pixel-range", "review/mapping-invalid", lambda r: r["export"]["calibration"]["anchors"][0]["pixel"].update(x=-1)),
            ("duplicate-object", "review/mapping-invalid", lambda r: r["export"]["calibration"]["anchors"][2].update(
                target=self.target("pool", "main"), anchor="top_left",
                pixel=copy.deepcopy(r["export"]["calibration"]["anchors"][0]["pixel"]))),
            ("scale", "schema/type", lambda r: r["previews"][0]["mapping"].update(scale_x=0)),
            ("calibration-id", "review/mapping-invalid", lambda r: r["previews"][0]["mapping"].update(calibration_id="other")),
        ]
        for label, code, mutate in mutations:
            with self.subTest(label=label):
                changed = self.calibrated_report(); mutate(changed)
                self.assert_review_error(changed, code, assess=True)

    def test_calibration_rejects_collinear_native_anchors_despite_small_pixel_jitter(self) -> None:
        synthetic_index = copy.deepcopy(self.index)
        targets = [self.target("node", sid) for sid in ("start", "decision", "end")]
        native_points = ((10.0, 10.0), (20.0, 20.0), (50.0, 50.0))
        for target, (x, y) in zip(targets, native_points):
            item = next(
                candidate for candidate in synthetic_index["objects"]
                if self.review.review_target_key(candidate["target"]) == self.review.review_target_key(target)
            )
            item["bounds"] = {"x": x, "y": y, "width": 1.0, "height": 1.0}
        report = self.base_report()
        report["previews"] = [self.full_preview(mapping={
            "mapping_version": 1, "kind": "axis_aligned", "scale_x": 1.0, "scale_y": 1.0,
            "translate_x": 0.0, "translate_y": 0.0, "calibration_id": "calibration",
        })]
        report["export"] = self.passed_export(calibration={
            "calibration_version": 1, "id": "calibration", "profile_id": "profile-a",
            "artifact_sha256": self.artifact_sha, "preview_sha256": self.full_sha,
            "method": "externally_observed_anchors", "anchors": [
                {"target": target, "anchor": "top_left",
                 "pixel": {"x": x, "y": y + (1.0 if index == 1 else 0.0)}}
                for index, (target, (x, y)) in enumerate(zip(targets, native_points))
            ], "tolerance_px": 2.0,
        })
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.review.assess_visual_report(
                report, self.run_record, synthetic_index, self.prepared_sha, self.report_sha)
        self.assertEqual(caught.exception.code, "review/mapping-invalid", caught.exception.diagnostic())

    def test_mapped_bound_node_edge_and_derived_outcome_require_intersection(self) -> None:
        targets = [
            self.target("node", "action"), self.target("edge", "choose"),
            {"kind": "outcome", "decision_id": "decision", "outcome_id": "go"},
        ]
        for target in targets:
            with self.subTest(target=target):
                report = self.calibrated_report()
                item = self.indexed(target)
                if item["bounds"] is not None:
                    region = {"x": item["bounds"]["x"], "y": item["bounds"]["y"], "width": 5, "height": 5}
                elif item["path"]:
                    region = {"x": item["path"][0]["x"] - 1, "y": item["path"][0]["y"] - 1,
                              "width": 2, "height": 2}
                else:
                    related = self.indexed(item["related_targets"][0])
                    region = {"x": related["path"][0]["x"] - 1, "y": related["path"][0]["y"] - 1,
                              "width": 2, "height": 2}
                issue = self.issue(target, region, code=("visual/edge-label-collision" if target["kind"] == "edge"
                                                         else "visual/main-path-unclear"), suggested_intent="manual-review")
                report["reviews"]["agent"] = self.reviewer("agent", issues=[issue])
                receipt = self.review.assess_visual_report(report, self.run_record, self.index, self.prepared_sha, self.report_sha)
                saved = receipt["issues"][0]
                self.assertEqual(saved["binding_status"], "spatial_bound")
                self.assertEqual(saved["spatial_verification"], "projected_intersection")
                self.assertEqual(saved["derived_geometry"], target["kind"] == "outcome")

                far = copy.deepcopy(report)
                far["reviews"]["agent"]["issues"][0]["region"] = {"x": 1900, "y": 1900, "width": 10, "height": 10}
                self.assert_review_error(far, "review/mapping-invalid", assess=True)

    def test_without_calibration_bound_is_identity_only_and_ambiguous_stays_ambiguous(self) -> None:
        report = self.base_report()
        report["previews"] = [self.full_preview()]
        report["export"] = self.passed_export()
        issue = self.issue(self.target("node", "action"), {"x": 1900, "y": 1900, "width": 5, "height": 5})
        report["reviews"]["agent"] = self.reviewer("agent", issues=[issue])
        receipt = self.review.assess_visual_report(report, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertEqual(receipt["issues"][0]["binding_status"], "identity_bound")
        self.assertEqual(receipt["issues"][0]["spatial_verification"], "unverified_spatial")

        report = self.calibrated_report()
        ambiguous = self.issue(self.target("node", "action"), {"x": 1900, "y": 1900, "width": 5, "height": 5},
                               binding="ambiguous", uncertainty="Could refer to another object")
        report["reviews"]["agent"] = self.reviewer("agent", issues=[ambiguous])
        receipt = self.review.assess_visual_report(report, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertEqual(receipt["issues"][0]["binding_status"], "ambiguous")
        ambiguous["uncertainty"] = None
        self.assert_review_error(report, "review/report-inconsistent")

    def test_cutout_transform_and_issue_region_are_bound_back_to_full_space(self) -> None:
        report = self.calibrated_report()
        cutout_sha = "e" * 64
        report["previews"].append({
            "id": "detail", "kind": "cutout", "name": "cutouts/detail.png", "sha256": cutout_sha,
            "bytes": 50, "width": 100, "height": 80,
            "mapping": {"mapping_version": 1, "kind": "cutout", "full_preview_sha256": self.full_sha,
                        "crop": {"x": 10, "y": 20, "width": 50, "height": 40},
                        "scale_x": 2.0, "scale_y": 2.0},
        })
        issue = self.issue(self.target("node", "action"), {"x": 1, "y": 1, "width": 5, "height": 5},
                           binding="ambiguous", uncertainty="Partial view", preview_id="detail", preview_sha=cutout_sha)
        report["reviews"]["agent"] = self.reviewer("agent", state="failed", issues=[issue], full=False,
                                                     viewed=["detail"], reason="Only a cutout was reviewed")
        receipt = self.review.assess_visual_report(report, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertIn({"preview_id": "detail", "verification": "declared_transform_and_bounds"}, receipt["mapping"])
        bad = copy.deepcopy(report)
        bad["previews"][1]["mapping"]["crop"]["x"] = 1990
        self.assert_review_error(bad, "review/mapping-invalid", assess=True)

    def test_review_state_predicates_consensus_and_no_repair_authority(self) -> None:
        report = self.calibrated_report()
        node = self.indexed(self.target("node", "action"))["bounds"]
        warning = self.issue(self.target("node", "action"),
                             {"x": node["x"], "y": node["y"], "width": 5, "height": 5},
                             severity="warning")
        report["reviews"]["agent"] = self.reviewer("agent", state="failed", issues=[warning])
        report["reviews"]["human"] = self.reviewer("human", state="passed")
        receipt = self.review.assess_visual_report(report, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertEqual(receipt["review_consensus"], "conflict")
        self.assertEqual(receipt["repair_support"], "not_available")
        self.assertFalse(receipt["can_repair"])

        invalid = copy.deepcopy(report)
        invalid["reviews"]["agent"]["state"] = "passed"
        self.assert_review_error(invalid, "review/report-inconsistent", assess=True)
        unperformed = self.base_report()
        unperformed["reviews"]["agent"]["issues"] = [warning]
        self.assert_review_error(unperformed, "review/report-inconsistent", assess=True)

    def test_issue_codes_intents_duplicates_and_typed_decision_outcome_are_exact(self) -> None:
        report = self.calibrated_report()
        node = self.indexed(self.target("node", "action"))["bounds"]
        issue = self.issue(self.target("node", "action"),
                           {"x": node["x"], "y": node["y"], "width": 5, "height": 5})
        report["reviews"]["agent"] = self.reviewer("agent", issues=[issue])
        unknown = copy.deepcopy(report); unknown["reviews"]["agent"]["issues"][0]["code"] = "visual/future"
        self.assert_review_error(unknown, "review/version-unsupported")
        intent = copy.deepcopy(report); intent["reviews"]["agent"]["issues"][0]["suggested_intent"] = "reroute-edge"
        self.assert_review_error(intent, "review/report-inconsistent")
        duplicate = copy.deepcopy(report); duplicate["reviews"]["agent"]["issues"].append(copy.deepcopy(issue))
        self.assert_review_error(duplicate, "schema/duplicate")
        wrong_outcome = copy.deepcopy(report)
        wrong_outcome["reviews"]["agent"]["issues"][0]["targets"] = [
            {"kind": "outcome", "decision_id": "action", "outcome_id": "go"}]
        self.assert_review_error(wrong_outcome, "review/reference-invalid", assess=True)

    def test_issue_and_preview_count_limits_are_inclusive(self) -> None:
        report = self.base_report()
        report["previews"] = [self.full_preview()]
        report["export"] = self.passed_export()
        prototype = self.issue(self.target("node", "action"),
                               {"x": 1, "y": 1, "width": 1, "height": 1}, severity="warning")
        issues = []
        for index in range(self.review.REVIEW_MAX_ISSUES):
            issue = copy.deepcopy(prototype)
            issue["issue_id"] = f"issue-{index}"
            issues.append(issue)
        report["reviews"]["agent"] = self.reviewer("agent", state="failed", issues=issues)
        self.review.ReviewValidator().report(report)
        receipt = self.review.assess_visual_report(
            report, self.run_record, self.index, self.prepared_sha, self.report_sha)
        self.assertEqual(len(receipt["issues"]), self.review.REVIEW_MAX_ISSUES)
        over = copy.deepcopy(report)
        extra = copy.deepcopy(prototype)
        extra["issue_id"] = "issue-over"
        over["reviews"]["human"] = self.reviewer("human", state="failed", issues=[extra])
        self.assert_review_error(over, "review/resource-limit")

        previews = self.base_report()
        previews["previews"] = [self.full_preview()]
        for index in range(16):
            previews["previews"].append({
                "id": f"cutout-{index}", "kind": "cutout", "name": f"cutouts/cutout-{index}.png",
                "sha256": f"{index + 1:064x}", "bytes": 1, "width": 10, "height": 10,
                "mapping": {"mapping_version": 1, "kind": "cutout", "full_preview_sha256": self.full_sha,
                            "crop": {"x": 0, "y": 0, "width": 10, "height": 10},
                            "scale_x": 1.0, "scale_y": 1.0},
            })
        self.review.ReviewValidator().report(previews)
        previews["previews"].append(copy.deepcopy(previews["previews"][-1]))
        previews["previews"][-1].update(id="cutout-over", name="cutouts/cutout-over.png", sha256="f" * 64)
        self.assert_review_error(previews, "review/resource-limit")

    def test_reference_budget_accepts_exact_limit_and_rejects_one_more(self) -> None:
        report = self.base_report()
        report["previews"] = [self.full_preview()]
        report["export"] = self.passed_export()
        targets = [self.target("node", f"target-{index}") for index in range(32)]
        issues = []
        for index in range(588):
            issue = self.issue(targets[0], {"x": 1, "y": 1, "width": 1, "height": 1},
                               issue_id=f"budget-{index}", severity="warning")
            issue["targets"] = copy.deepcopy(targets)
            issues.append(issue)
        last = self.issue(targets[0], {"x": 1, "y": 1, "width": 1, "height": 1},
                          issue_id="budget-last", severity="warning")
        last["targets"] = copy.deepcopy(targets[:3])
        issues.append(last)
        report["reviews"]["agent"] = self.reviewer("agent", state="failed", issues=issues)
        self.review.ReviewValidator().report(report)
        over = copy.deepcopy(report)
        over["reviews"]["agent"]["reviewer"]["tool_receipt_ids"].append("one-more-reference")
        self.assert_review_error(over, "review/resource-limit")


class E4ReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e4_review_workflow_tests")
        cls.workflow = cls.loaded.review_workflow
        cls.bundle = cls.workflow.context_bundle
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name).resolve()
        cls.spec_data = workflow_spec()
        cls.spec_path = cls.directory / "spec.json"
        cls.spec_path.write_text(json.dumps(cls.spec_data), encoding="utf-8")
        cls.graph_path = cls.directory / "diagram.drawio"
        cls.loaded.document.write_tree(cls.loaded.build.build_tree(cls.spec_data), cls.graph_path)
        cls.graph_raw = cls.graph_path.read_bytes()
        cls.graph_sha = hashlib.sha256(cls.graph_raw).hexdigest()
        tree = cls.loaded.document.read_tree(cls.graph_path)
        pool = cls.loaded.document.find_pool(tree)
        cls.context_data = {
            "context_version": 1,
            "artifact": {
                "sha256": cls.graph_sha,
                "schema_version": pool.get(cls.loaded.contracts.DATA_SCHEMA_VERSION),
                "model_hash_version": pool.get(cls.loaded.contracts.DATA_MODEL_HASH_VERSION),
                "model_hash": pool.get(cls.loaded.contracts.DATA_MODEL_HASH),
            },
            "patterns": {"version": 1, "scopes": [{
                "id": "linear-primary", "role": "primary", "pattern": "linear",
                "nodes": ["start", "step", "end"], "edges": ["enter", "finish"],
                "excluded_edges": [], "declarations": {"entry": "start", "exit": "end"},
            }]},
        }
        cls.context_path = cls.directory / "context.json"
        cls.context_path.write_bytes(cls.bundle.bundle_json(cls.context_data))
        cls.context_raw = cls.context_path.read_bytes()
        cls.context_sha = hashlib.sha256(cls.context_raw).hexdigest()
        cls.sequence = 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def next_path(self, prefix: str) -> Path:
        type(self).sequence += 1
        return self.directory / f"{prefix}-{self.sequence}"

    def assert_code(self, result: subprocess.CompletedProcess[bytes], code: str,
                    exit_code: int = 2) -> dict:
        self.assertEqual(result.returncode, exit_code, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["diagnostics"][0]["code"], code, body)
        return body

    def prepare(self, *, context: bool = False, extra: tuple[str, ...] = ()) -> tuple[dict, Path]:
        output = self.next_path("prepared")
        args = ["review", "prepare", "--input", str(self.graph_path),
                "--expected-input-sha256", self.graph_sha, "--output", str(output)]
        if context:
            args.extend(["--context", str(self.context_path),
                         "--expected-context-sha256", self.context_sha])
        args.extend(extra)
        result = run_tool(*args)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return json.loads(result.stdout), output

    def no_review(self, state: str = "not_run", reason: str | None = None) -> dict:
        return {"state": state, "reason": reason, "reviewer": None,
                "full_image_viewed": False, "viewed_previews": [], "issues": []}

    def report_for(self, prepared: Path) -> dict:
        run = json.loads((prepared / "run.json").read_text())
        completion_raw = (prepared / "completion.json").read_bytes()
        return {
            "visual_report_version": 1, "run_id": run["run_id"],
            "prepared_sha256": hashlib.sha256(completion_raw).hexdigest(),
            "artifact_sha256": self.graph_sha, "context_sha256": run["context"]["sha256"],
            "round_index": 0, "export": None, "previews": [],
            "reviews": {"agent": self.no_review(), "human": self.no_review()},
        }

    def record(self, prepared: Path, report: dict | bytes, *, evidence: Path | None = None,
               output: Path | None = None, context: bool = False) -> tuple[subprocess.CompletedProcess[bytes], Path, Path]:
        evidence = evidence or self.next_path("evidence")
        evidence.mkdir(exist_ok=True)
        report_raw = report if isinstance(report, bytes) else self.bundle.bundle_json(report)
        report_path = evidence / "report.json"
        report_path.write_bytes(report_raw)
        output = output or self.next_path("record")
        prepared_path = prepared / "completion.json"
        args = ["review", "record", "--prepared", str(prepared_path),
                "--expected-prepared-sha256", hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
                "--input", str(self.graph_path), "--expected-input-sha256", self.graph_sha,
                "--evidence-dir", str(evidence), "--report", str(report_path),
                "--expected-report-sha256", hashlib.sha256(report_raw).hexdigest(),
                "--output", str(output)]
        if context:
            args.extend(["--context", str(self.context_path),
                         "--expected-context-sha256", self.context_sha])
        return run_tool(*args), evidence, output

    def actual_reviewer(self, kind: str, *, state: str = "passed", issues=None,
                        full: bool = True, viewed=None, reason=None) -> dict:
        return {
            "state": state, "reason": reason,
            "reviewer": {"kind": kind, "name": None, "host": None,
                         "model": None, "tool_receipt_ids": [f"{kind}-receipt"]},
            "full_image_viewed": full,
            "viewed_previews": (["full"] if viewed is None else viewed),
            "issues": [] if issues is None else issues,
        }

    def test_prepare_commits_exact_immutable_package_and_preserves_original_bytes(self) -> None:
        before = (self.graph_path.read_bytes(), self.graph_path.stat().st_mtime_ns)
        receipt, output = self.prepare()
        self.assertEqual(receipt["states"], {
            "strict_validation": "passed", "preview_export": "not_run",
            "agent_image_review": "not_run", "human_review": "not_run",
        })
        self.assertEqual(receipt["repair_support"], "not_available")
        self.assertEqual(receipt["semantic_context"], None)
        self.assertEqual((self.graph_path.read_bytes(), self.graph_path.stat().st_mtime_ns), before)
        self.assertEqual({str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()},
                         {"completion.json", "objects.json", "run.json", "original/diagram.drawio"})
        run = json.loads((output / "run.json").read_text())
        identifier = uuid.UUID(run["run_id"])
        self.assertEqual(identifier.version, 4)
        self.assertEqual(str(identifier), run["run_id"])
        self.assertEqual((output / "original/diagram.drawio").read_bytes(), self.graph_raw)
        objects = json.loads((output / "objects.json").read_text())
        self.assertEqual(objects["validation"]["visual_review"], "not_available")
        completion = json.loads((output / "completion.json").read_text())
        self.assertEqual([item["name"] for item in completion["members"]],
                         ["objects.json", "original/diagram.drawio", "run.json"])
        self.assertTrue(receipt["delivery"]["committed"])
        self.assertTrue(receipt["delivery"]["complete"])

    def test_prepare_with_e1_context_preserves_context_and_assessment(self) -> None:
        receipt, output = self.prepare(context=True)
        self.assertEqual(receipt["semantic_context"]["gate"], "passed")
        run = json.loads((output / "run.json").read_text())
        self.assertEqual(run["context"], {
            "status": "provided", "sha256": self.context_sha,
            "bytes": len(self.context_raw), "completion_sha256": None,
        })
        self.assertEqual((output / "original/context.json").read_bytes(), self.context_raw)
        self.assertIsNotNone(run["context_assessment"])

    def test_record_not_run_is_success_and_keeps_prepared_and_report_raw(self) -> None:
        _, prepared = self.prepare()
        prepared_before = {path.relative_to(prepared): (path.read_bytes(), path.stat().st_mtime_ns)
                           for path in prepared.rglob("*") if path.is_file()}
        report = self.report_for(prepared)
        result, evidence, output = self.record(prepared, report)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["review"]["states"], {
            "strict_validation": "passed", "preview_export": "not_run",
            "agent_image_review": "not_run", "human_review": "not_run",
        })
        self.assertEqual(body["review"]["trust"], "externally_supplied")
        self.assertFalse(body["review"]["can_repair"])
        self.assertEqual((output / "report.json").read_bytes(), (evidence / "report.json").read_bytes())
        self.assertEqual((output / "prepared.json").read_bytes(), (prepared / "completion.json").read_bytes())
        after = {path.relative_to(prepared): (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in prepared.rglob("*") if path.is_file()}
        self.assertEqual(after, prepared_before)
        self.assertEqual({path.name for path in output.iterdir()},
                         {"report.json", "receipt.json", "prepared.json", "completion.json"})

    def test_valid_not_available_evidence_state_still_records_exit_zero(self) -> None:
        _, prepared = self.prepare()
        report = self.report_for(prepared)
        report["reviews"]["agent"] = self.no_review("not_available", "Viewer unavailable")
        result, _, output = self.record(prepared, report)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["review"]["states"]["agent_image_review"], "not_available")
        self.assertTrue((output / "completion.json").is_file())

    def test_record_validates_actual_png_and_log_bytes_without_claiming_it_viewed_them(self) -> None:
        _, prepared = self.prepare()
        evidence = self.next_path("evidence")
        evidence.mkdir()
        raw = png_bytes(width=20, height=16)
        (evidence / "full.png").write_bytes(raw)
        log_raw = b"renderer output\n"
        (evidence / "export.stdout.txt").write_bytes(log_raw)
        report = self.report_for(prepared)
        full_sha = hashlib.sha256(raw).hexdigest()
        report["previews"] = [{
            "id": "full", "kind": "full", "name": "full.png", "sha256": full_sha,
            "bytes": len(raw), "width": 20, "height": 16, "mapping": None,
        }]
        report["export"] = {
            "state": "passed", "reason": None,
            "renderer": {"name": "draw.io", "version": "31.4.5",
                         "profile_id": "profile-a", "fonts": None},
            "command": ["draw.io", "--export", "diagram.drawio"], "exit_code": 0,
            "input_before_sha256": self.graph_sha, "input_after_sha256": self.graph_sha,
            "full_preview_sha256": full_sha,
            "stdout": {"name": "export.stdout.txt", "sha256": hashlib.sha256(log_raw).hexdigest(),
                       "bytes": len(log_raw)},
            "stderr": None, "calibration": None,
        }
        report["reviews"]["agent"] = self.actual_reviewer("agent")
        result, _, output = self.record(prepared, report, evidence=evidence)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["png_checks"][0]["verification"], "png_integrity_only")
        self.assertEqual(body["review"]["trust"], "externally_supplied")
        self.assertEqual(body["review"]["mapping"],
                         [{"preview_id": "full", "verification": "unverified_spatial"}])
        self.assertEqual(body["review"]["states"]["agent_image_review"], "passed")
        self.assertEqual((output / "full.png").read_bytes(), raw)
        self.assertEqual((output / "export.stdout.txt").read_bytes(), log_raw)
        completion = json.loads((output / "completion.json").read_text())
        self.assertEqual([item["name"] for item in completion["members"]],
                         ["export.stdout.txt", "full.png", "prepared.json", "receipt.json", "report.json"])

    def test_failed_review_with_declared_observation_records_without_exit_one(self) -> None:
        _, prepared = self.prepare()
        evidence = self.next_path("evidence")
        evidence.mkdir()
        raw = png_bytes(width=600, height=600)
        (evidence / "full.png").write_bytes(raw)
        full_sha = hashlib.sha256(raw).hexdigest()
        report = self.report_for(prepared)
        report["previews"] = [{"id": "full", "kind": "full", "name": "full.png",
                               "sha256": full_sha, "bytes": len(raw), "width": 600,
                               "height": 600, "mapping": None}]
        report["export"] = {
            "state": "passed", "reason": None,
            "renderer": {"name": "draw.io", "version": "31.4.5",
                         "profile_id": "profile-a", "fonts": None},
            "command": ["draw.io", "--export", "diagram.drawio"], "exit_code": 0,
            "input_before_sha256": self.graph_sha, "input_after_sha256": self.graph_sha,
            "full_preview_sha256": full_sha, "stdout": None, "stderr": None,
            "calibration": None,
        }
        issue = {
            "issue_id": "observed-warning", "code": "visual/node-text-clipped",
            "severity": "warning", "targets": [{"kind": "node", "id": "step"}],
            "preview_id": "full", "preview_sha256": full_sha,
            "region": {"x": 1.0, "y": 1.0, "width": 5.0, "height": 5.0},
            "observation": "Declared external observation", "suggested_intent": "manual-review",
            "binding": "bound", "uncertainty": None,
        }
        report["reviews"]["agent"] = self.actual_reviewer("agent", state="failed", issues=[issue])
        result, _, _ = self.record(prepared, report, evidence=evidence)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["review"]["states"]["agent_image_review"], "failed")
        self.assertEqual(body["review"]["issues"][0]["binding_status"], "identity_bound")
        self.assertFalse(body["review"]["can_repair"])

    def test_cli_action_dispatch_and_argparse_boundaries_are_distinct(self) -> None:
        for action in ("plan", "repair", "assess"):
            with self.subTest(action=action):
                body = self.assert_code(run_tool("review", action), "input/invalid")
                self.assertEqual((body["operation"], body["action"]), ("review", action))
        body = self.assert_code(run_tool("review", "future"), "review/action-unsupported")
        self.assertEqual((body["operation"], body["action"]), ("review", "future"))
        body = self.assert_code(run_tool("review", "prepare"), "input/invalid")
        self.assertEqual(body["operation"], "review")
        for args in (("review", "prepare", "--unknown"),
                     ("review", "prepare", "--input"), ("reviwe", "prepare")):
            with self.subTest(args=args):
                result = run_tool(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"usage:", result.stderr)

    def test_prepare_rejects_digest_and_context_argument_boundary_errors(self) -> None:
        output = self.next_path("rejected")
        mismatch = run_tool("review", "prepare", "--input", str(self.graph_path),
                            "--expected-input-sha256", "0" * 64, "--output", str(output))
        self.assert_code(mismatch, "delivery/input-sha256-mismatch")
        self.assertFalse(output.exists())
        contextless = run_tool("review", "prepare", "--input", str(self.graph_path),
                               "--expected-input-sha256", self.graph_sha,
                               "--expected-context-sha256", self.context_sha,
                               "--output", str(self.next_path("rejected")))
        self.assert_code(contextless, "input/invalid")

    def test_prepare_no_clobber_and_run_ids_are_fresh(self) -> None:
        first, output = self.prepare()
        second, other = self.prepare()
        self.assertNotEqual(first["run_id"], second["run_id"])
        collision = run_tool("review", "prepare", "--input", str(self.graph_path),
                             "--expected-input-sha256", self.graph_sha, "--output", str(output))
        self.assert_code(collision, "delivery/output-exists")
        self.assertTrue((output / "completion.json").is_file())
        self.assertTrue((other / "completion.json").is_file())

    def test_report_json_duplicate_nan_surrogate_depth_and_size_are_bounded_before_recording(self) -> None:
        _, prepared = self.prepare()
        report = self.report_for(prepared)
        canonical = self.bundle.bundle_json(report)
        duplicate = canonical.replace(b'"round_index": 0,', b'"round_index": 0,\n  "round_index": 0,', 1)
        nan_report = copy.deepcopy(report)
        nan_report["round_index"] = float("nan")
        surrogate_report = copy.deepcopy(report)
        surrogate_report["reviews"]["agent"]["reason"] = "\ud800"
        cases = [
            ("duplicate", duplicate, "context/duplicate-key"),
            ("nan", json.dumps(nan_report, allow_nan=True).encode("utf-8"), "input/json-invalid"),
            ("surrogate", json.dumps(surrogate_report, ensure_ascii=True).encode("utf-8"), "input/json-invalid"),
            ("depth", b"[" * 33 + b"0" + b"]" * 33, "context/resource-limit"),
        ]
        for label, raw, code in cases:
            with self.subTest(label=label):
                result, _, output = self.record(prepared, raw)
                self.assert_code(result, code)
                self.assertFalse(output.exists())

        limit = self.loaded.tool.semantic_context.MAX_BYTES
        exact = canonical + b" " * (limit - len(canonical))
        result, _, output = self.record(prepared, exact)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual((output / "report.json").read_bytes(), exact)
        over = exact + b" "
        result, _, output = self.record(prepared, over)
        self.assert_code(result, "review/resource-limit")
        self.assertFalse(output.exists())

    def test_prepared_package_missing_tampered_and_self_consistent_forged_members_fail_closed(self) -> None:
        for mode, code in (("missing", "delivery/bundle-incomplete"),
                           ("tampered", "delivery/bundle-incomplete"),
                           ("forged", "review/baseline-mismatch")):
            with self.subTest(mode=mode):
                _, prepared = self.prepare()
                objects = prepared / "objects.json"
                if mode == "missing":
                    objects.unlink()
                else:
                    data = json.loads(objects.read_text())
                    data["coordinate_system"] = "forged-coordinate-system"
                    objects.chmod(0o600)
                    objects.write_bytes(self.bundle.bundle_json(data))
                    if mode == "forged":
                        completion_path = prepared / "completion.json"
                        completion = json.loads(completion_path.read_text())
                        entry = next(item for item in completion["members"] if item["name"] == "objects.json")
                        entry["sha256"] = hashlib.sha256(objects.read_bytes()).hexdigest()
                        entry["bytes"] = len(objects.read_bytes())
                        completion_path.chmod(0o600)
                        completion_path.write_bytes(self.bundle.bundle_json(completion))
                report = self.report_for(prepared)
                result, _, output = self.record(prepared, report)
                self.assert_code(result, code)
                self.assertFalse(output.exists())

    def test_record_binds_prepared_report_graph_and_context_digests_independently(self) -> None:
        _, prepared = self.prepare(context=True)
        report = self.report_for(prepared)
        prepared_path = prepared / "completion.json"
        evidence = self.next_path("evidence")
        evidence.mkdir()
        report_raw = self.bundle.bundle_json(report)
        report_path = evidence / "report.json"
        report_path.write_bytes(report_raw)
        base = ["review", "record", "--prepared", str(prepared_path),
                "--expected-prepared-sha256", hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
                "--input", str(self.graph_path), "--expected-input-sha256", self.graph_sha,
                "--context", str(self.context_path), "--expected-context-sha256", self.context_sha,
                "--evidence-dir", str(evidence), "--report", str(report_path),
                "--expected-report-sha256", hashlib.sha256(report_raw).hexdigest(),
                "--output", str(self.next_path("record"))]
        variants = [
            ("prepared", 5, "0" * 64, "delivery/prepared-sha256-mismatch"),
            ("input", 9, "0" * 64, "delivery/input-sha256-mismatch"),
            ("context", 13, "0" * 64, "delivery/context-sha256-mismatch"),
            ("report", 19, "0" * 64, "delivery/report-sha256-mismatch"),
        ]
        for label, position, digest, code in variants:
            with self.subTest(label=label):
                args = list(base)
                args[position] = digest
                args[-1] = str(self.next_path("record-rejected"))
                self.assert_code(run_tool(*args), code)
        missing_context = [item for item in base]
        for flag in ("--context", "--expected-context-sha256"):
            index = missing_context.index(flag)
            del missing_context[index:index + 2]
        missing_context[-1] = str(self.next_path("record-rejected"))
        self.assert_code(run_tool(*missing_context), "review/baseline-mismatch")

    def test_report_and_preview_paths_reject_escape_symlink_and_hardlink_inputs(self) -> None:
        _, prepared = self.prepare()
        report = self.report_for(prepared)
        evidence = self.next_path("evidence")
        evidence.mkdir()
        raw = png_bytes()
        raw_sha = hashlib.sha256(raw).hexdigest()
        report["previews"] = [{"id": "full", "kind": "full", "name": "full.png",
                               "sha256": raw_sha, "bytes": len(raw), "width": 4,
                               "height": 3, "mapping": None}]
        report["export"] = {
            "state": "failed", "reason": "Export evidence is incomplete", "renderer": None,
            "command": None, "exit_code": None, "input_before_sha256": self.graph_sha,
            "input_after_sha256": self.graph_sha, "full_preview_sha256": raw_sha,
            "stdout": None, "stderr": None, "calibration": None,
        }
        report_raw = self.bundle.bundle_json(report)
        outside = self.next_path("outside-report.json")
        outside.write_bytes(report_raw)
        prepared_path = prepared / "completion.json"
        args = ["review", "record", "--prepared", str(prepared_path),
                "--expected-prepared-sha256", hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
                "--input", str(self.graph_path), "--expected-input-sha256", self.graph_sha,
                "--evidence-dir", str(evidence), "--report", str(outside),
                "--expected-report-sha256", hashlib.sha256(report_raw).hexdigest(),
                "--output", str(self.next_path("record-rejected"))]
        self.assert_code(run_tool(*args), "delivery/path-unsafe")

        (evidence / "report.json").write_bytes(report_raw)
        target = self.next_path("png-target")
        target.write_bytes(raw)
        os.symlink(target, evidence / "full.png")
        result, _, output = self.record(prepared, report, evidence=evidence)
        self.assert_code(result, "delivery/path-unsafe")
        self.assertFalse(output.exists())
        (evidence / "full.png").unlink()
        os.link(target, evidence / "full.png")
        result, _, output = self.record(prepared, report, evidence=evidence)
        self.assert_code(result, "delivery/path-unsafe")
        self.assertFalse(output.exists())

        traversal_output = self.directory / "child" / ".." / "record"
        result, _, _ = self.record(prepared, self.report_for(prepared), output=traversal_output)
        self.assert_code(result, "delivery/path-unsafe")

    def test_preview_and_export_log_declarations_must_match_actual_bytes(self) -> None:
        _, prepared = self.prepare()
        evidence = self.next_path("evidence")
        evidence.mkdir()
        raw = png_bytes()
        (evidence / "full.png").write_bytes(raw)
        full_sha = hashlib.sha256(raw).hexdigest()
        report = self.report_for(prepared)
        report["previews"] = [{"id": "full", "kind": "full", "name": "full.png",
                               "sha256": full_sha, "bytes": len(raw), "width": 4,
                               "height": 3, "mapping": None}]
        report["export"] = {
            "state": "passed", "reason": None,
            "renderer": {"name": "draw.io", "version": "31.4.5", "profile_id": "p", "fonts": None},
            "command": ["draw.io", "--export", "diagram.drawio"], "exit_code": 0,
            "input_before_sha256": self.graph_sha, "input_after_sha256": self.graph_sha,
            "full_preview_sha256": full_sha, "stdout": None, "stderr": None, "calibration": None,
        }
        for key, value in (("sha256", "0" * 64), ("bytes", len(raw) + 1), ("width", 5), ("height", 4)):
            with self.subTest(key=key):
                changed = copy.deepcopy(report)
                changed["previews"][0][key] = value
                result, _, output = self.record(prepared, changed, evidence=evidence)
                self.assert_code(result, "review/preview-mismatch")
                self.assertFalse(output.exists())

        log_raw = b"actual log"
        (evidence / "export.stdout.txt").write_bytes(log_raw)
        report["export"]["stdout"] = {
            "name": "export.stdout.txt", "sha256": "0" * 64, "bytes": len(log_raw)}
        result, _, output = self.record(prepared, report, evidence=evidence)
        self.assert_code(result, "review/preview-mismatch")
        self.assertFalse(output.exists())

    def test_prepare_partial_nested_write_and_post_commit_unlink_report_truthful_residue(self) -> None:
        graph = self.next_path("io-diagram.drawio")
        graph.write_bytes(self.graph_raw)
        for mode in ("partial", "post-commit"):
            with self.subTest(mode=mode):
                output = self.next_path(f"io-{mode}")
                args = self.loaded.tool.build_parser().parse_args([
                    "review", "prepare", "--input", str(graph),
                    "--expected-input-sha256", self.graph_sha, "--output", str(output)])
                if mode == "partial":
                    original = self.bundle.bundle_write_member

                    def partial(descriptor, name, data):
                        if name != "diagram.drawio":
                            return original(descriptor, name, data)
                        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o400, dir_fd=descriptor)
                        try:
                            os.write(target, data[:17])
                        finally:
                            os.close(target)
                        raise OSError("injected partial nested write")

                    patcher = mock.patch.object(self.bundle, "bundle_write_member", side_effect=partial)
                else:
                    original_unlink = self.bundle.os.unlink

                    def fail_pending(path, *call_args, **call_kwargs):
                        if path == ".completion.pending":
                            raise OSError("injected pending unlink failure")
                        return original_unlink(path, *call_args, **call_kwargs)

                    patcher = mock.patch.object(self.bundle.os, "unlink", side_effect=fail_pending)
                with patcher, self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                    self.workflow.review_prepare(args)
                self.assertEqual(caught.exception.code, "delivery/io-error")
                delivery = caught.exception.evidence["delivery"]
                self.assertFalse(delivery["complete"])
                if mode == "partial":
                    self.assertFalse(delivery["committed"])
                    self.assertEqual(delivery["failed_member"], "original/diagram.drawio")
                    self.assertIn({"name": "original/diagram.drawio", "bytes": 17}, delivery["residue"])
                    self.assertFalse((output / "completion.json").exists())
                else:
                    self.assertTrue(delivery["committed"])
                    self.assertTrue((output / "completion.json").is_file())
                    self.assertTrue((output / ".completion.pending").is_file())

    def test_record_detects_same_byte_report_inode_replacement_before_commit(self) -> None:
        _, prepared = self.prepare()
        report = self.report_for(prepared)
        evidence = self.next_path("race-evidence")
        evidence.mkdir()
        report_raw = self.bundle.bundle_json(report)
        report_path = evidence / "report.json"
        report_path.write_bytes(report_raw)
        output = self.next_path("race-record")
        prepared_path = prepared / "completion.json"
        args = self.loaded.tool.build_parser().parse_args([
            "review", "record", "--prepared", str(prepared_path),
            "--expected-prepared-sha256", hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
            "--input", str(self.graph_path), "--expected-input-sha256", self.graph_sha,
            "--evidence-dir", str(evidence), "--report", str(report_path),
            "--expected-report-sha256", hashlib.sha256(report_raw).hexdigest(),
            "--output", str(output)])
        original_verify = self.bundle.BundleInput.verify
        calls = 0

        def replace_before_second_verify(item):
            nonlocal calls
            if item.path == report_path:
                calls += 1
                if calls == 2:
                    replacement = evidence / "replacement-report.json"
                    replacement.write_bytes(report_raw)
                    os.replace(replacement, report_path)
            return original_verify(item)

        with mock.patch.object(self.bundle.BundleInput, "verify", autospec=True,
                               side_effect=replace_before_second_verify):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.workflow.review_record(args)
        self.assertEqual(caught.exception.code, "delivery/input-changed")
        delivery = caught.exception.evidence["delivery"]
        self.assertFalse(delivery["committed"])
        self.assertFalse(delivery["complete"])
        self.assertFalse((output / "completion.json").exists())

    def test_concurrent_prepare_has_one_atomic_winner_and_no_overwrite(self) -> None:
        output = self.next_path("concurrent-prepared")
        command = [sys.executable, "-B", str(TOOL), "review", "prepare",
                   "--input", str(self.graph_path), "--expected-input-sha256", self.graph_sha,
                   "--output", str(output)]
        first = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        results = [first.communicate(), second.communicate()]
        codes = [first.returncode, second.returncode]
        self.assertEqual(sorted(codes), [0, 2], [(code, streams[0], streams[1]) for code, streams in zip(codes, results)])
        rejected = json.loads(results[codes.index(2)][0])
        self.assertEqual(rejected["diagnostics"][0]["code"], "delivery/output-exists")
        data, _, members = self.workflow.review_read_package(output / "completion.json", kind="prepared")
        self.assertEqual(data["kind"], "prepared")
        self.assertEqual(set(members), {"objects.json", "original/diagram.drawio", "run.json"})

    def test_prepare_rejects_derived_coordinate_overflow_but_accepts_finite_negative_canvas(self) -> None:
        for label, pool_x, lane_x, succeeds in (
                ("finite-negative", -100.0, -20.0, True),
                ("derived-overflow", 1e308, 1e308, False)):
            with self.subTest(label=label):
                tree = self.loaded.document.read_tree(self.graph_path)
                pool = self.loaded.document.find_pool(tree)
                lanes, _ = self.loaded.document.lane_node_records(
                    self.loaded.document.graph_root(tree), pool)
                pool.find("mxGeometry").set("x", str(pool_x))
                lanes["lane"]["cell"].find("mxGeometry").set("x", str(lane_x))
                path = self.next_path(f"{label}.drawio")
                self.loaded.document.write_tree(tree, path)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                output = self.next_path(f"{label}-prepared")
                result = run_tool("review", "prepare", "--input", str(path),
                                  "--expected-input-sha256", digest, "--output", str(output))
                if succeeds:
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                    objects = json.loads((output / "objects.json").read_text())["objects"]
                    self.assertTrue(all(
                        value is None or all(isinstance(number, (int, float)) and not isinstance(number, bool)
                                             and number == number and abs(number) != float("inf")
                                             for number in value.values())
                        for item in objects for value in (item["bounds"], item["label_bounds"])
                    ))
                else:
                    self.assertEqual(result.returncode, 2, result.stdout.decode(errors="replace"))
                    body = json.loads(result.stdout)
                    self.assertIn(body["diagnostics"][0]["code"], {"schema/type", "review/mapping-invalid"})
                    self.assertFalse(output.exists())

    def test_record_treats_command_and_suggested_intent_as_inert_external_data(self) -> None:
        _, prepared = self.prepare()
        evidence = self.next_path("inert-evidence")
        evidence.mkdir()
        raw = png_bytes(width=600, height=600)
        (evidence / "full.png").write_bytes(raw)
        full_sha = hashlib.sha256(raw).hexdigest()
        marker = self.next_path("must-not-be-created")
        report = self.report_for(prepared)
        report["previews"] = [{"id": "full", "kind": "full", "name": "full.png",
                               "sha256": full_sha, "bytes": len(raw), "width": 600,
                               "height": 600, "mapping": None}]
        report["export"] = {
            "state": "passed", "reason": None,
            "renderer": {"name": "declared renderer", "version": "declared",
                         "profile_id": "declared", "fonts": None},
            "command": [sys.executable, "-c", f"open({str(marker)!r},'w').write('executed')"],
            "exit_code": 0, "input_before_sha256": self.graph_sha,
            "input_after_sha256": self.graph_sha, "full_preview_sha256": full_sha,
            "stdout": None, "stderr": None, "calibration": None,
        }
        issue = {
            "issue_id": "inert-intent", "code": "visual/excessive-detour", "severity": "warning",
            "targets": [{"kind": "edge", "id": "enter"}], "preview_id": "full",
            "preview_sha256": full_sha, "region": {"x": 1, "y": 1, "width": 5, "height": 5},
            "observation": "External declaration only", "suggested_intent": "reroute-edge",
            "binding": "bound", "uncertainty": None,
        }
        report["reviews"]["agent"] = self.actual_reviewer("agent", state="failed", issues=[issue])
        before = (self.graph_path.read_bytes(), self.graph_path.stat().st_mtime_ns)
        result, _, _ = self.record(prepared, report, evidence=evidence)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["review"]["trust"], "externally_supplied")
        self.assertFalse(body["review"]["can_repair"])
        self.assertFalse(marker.exists())
        self.assertEqual((self.graph_path.read_bytes(), self.graph_path.stat().st_mtime_ns), before)

    def test_record_validates_and_copies_full_and_cutout_with_declared_mapping(self) -> None:
        _, prepared = self.prepare()
        evidence = self.next_path("cutout-evidence")
        (evidence / "cutouts").mkdir(parents=True)
        full_raw = png_bytes(width=600, height=600)
        cutout_raw = png_bytes(width=100, height=80)
        (evidence / "full.png").write_bytes(full_raw)
        (evidence / "cutouts/detail.png").write_bytes(cutout_raw)
        full_sha = hashlib.sha256(full_raw).hexdigest()
        cutout_sha = hashlib.sha256(cutout_raw).hexdigest()
        report = self.report_for(prepared)
        mapping = {"mapping_version": 1, "kind": "axis_aligned", "scale_x": 1.0,
                   "scale_y": 1.0, "translate_x": 0.0, "translate_y": 0.0,
                   "calibration_id": "calibration"}
        report["previews"] = [
            {"id": "full", "kind": "full", "name": "full.png", "sha256": full_sha,
             "bytes": len(full_raw), "width": 600, "height": 600, "mapping": mapping},
            {"id": "detail", "kind": "cutout", "name": "cutouts/detail.png",
             "sha256": cutout_sha, "bytes": len(cutout_raw), "width": 100, "height": 80,
             "mapping": {"mapping_version": 1, "kind": "cutout", "full_preview_sha256": full_sha,
                         "crop": {"x": 10, "y": 20, "width": 50, "height": 40},
                         "scale_x": 2.0, "scale_y": 2.0}},
        ]
        objects = json.loads((prepared / "objects.json").read_text())["objects"]
        by_key = {self.loaded.review_evidence.review_target_key(item["target"]): item for item in objects}
        anchors = []
        for target, anchor in (({"kind": "pool", "id": "main"}, "top_left"),
                               ({"kind": "pool", "id": "main"}, "top_right"),
                               ({"kind": "node", "id": "step"}, "center")):
            x, y = self.loaded.review_evidence.review_anchor(
                by_key[self.loaded.review_evidence.review_target_key(target)]["bounds"], anchor)
            anchors.append({"target": target, "anchor": anchor, "pixel": {"x": x, "y": y}})
        report["export"] = {
            "state": "passed", "reason": None,
            "renderer": {"name": "draw.io", "version": "31.4.5", "profile_id": "p", "fonts": None},
            "command": ["draw.io", "--export", "diagram.drawio"], "exit_code": 0,
            "input_before_sha256": self.graph_sha, "input_after_sha256": self.graph_sha,
            "full_preview_sha256": full_sha, "stdout": None, "stderr": None,
            "calibration": {"calibration_version": 1, "id": "calibration", "profile_id": "p",
                            "artifact_sha256": self.graph_sha, "preview_sha256": full_sha,
                            "method": "externally_observed_anchors", "anchors": anchors,
                            "tolerance_px": 2.0},
        }
        issue = {
            "issue_id": "cutout-observation", "code": "visual/crowding", "severity": "note",
            "targets": [{"kind": "node", "id": "step"}], "preview_id": "detail",
            "preview_sha256": cutout_sha, "region": {"x": 1, "y": 1, "width": 5, "height": 5},
            "observation": "Ambiguous partial view", "suggested_intent": "manual-review",
            "binding": "ambiguous", "uncertainty": "The cutout does not uniquely identify the object",
        }
        report["reviews"]["agent"] = self.actual_reviewer(
            "agent", state="failed", issues=[issue], full=False, viewed=["detail"],
            reason="Only a cutout was reviewed")
        result, _, output = self.record(prepared, report, evidence=evidence)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual({item["preview_id"] for item in body["png_checks"]}, {"full", "detail"})
        self.assertIn({"preview_id": "detail", "verification": "declared_transform_and_bounds"},
                      body["review"]["mapping"])
        self.assertEqual(body["review"]["issues"][0]["binding_status"], "ambiguous")
        self.assertEqual((output / "cutouts/detail.png").read_bytes(), cutout_raw)

    def test_record_partial_member_write_never_creates_completion_marker(self) -> None:
        _, prepared = self.prepare()
        report = self.report_for(prepared)
        evidence = self.next_path("partial-record-evidence")
        evidence.mkdir()
        report_raw = self.bundle.bundle_json(report)
        report_path = evidence / "report.json"
        report_path.write_bytes(report_raw)
        output = self.next_path("partial-record")
        prepared_path = prepared / "completion.json"
        args = self.loaded.tool.build_parser().parse_args([
            "review", "record", "--prepared", str(prepared_path),
            "--expected-prepared-sha256", hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
            "--input", str(self.graph_path), "--expected-input-sha256", self.graph_sha,
            "--evidence-dir", str(evidence), "--report", str(report_path),
            "--expected-report-sha256", hashlib.sha256(report_raw).hexdigest(),
            "--output", str(output)])
        original = self.bundle.bundle_write_member

        def partial_receipt(descriptor, name, data):
            if name != "receipt.json":
                return original(descriptor, name, data)
            target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o400, dir_fd=descriptor)
            try:
                os.write(target, data[:9])
            finally:
                os.close(target)
            raise OSError("injected partial receipt")

        with mock.patch.object(self.bundle, "bundle_write_member", side_effect=partial_receipt):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.workflow.review_record(args)
        self.assertEqual(caught.exception.code, "delivery/io-error")
        delivery = caught.exception.evidence["delivery"]
        self.assertEqual(delivery["failed_member"], "receipt.json")
        self.assertIn({"name": "receipt.json", "bytes": 9}, delivery["residue"])
        self.assertFalse(delivery["committed"])
        self.assertFalse((output / "completion.json").exists())

    def test_prepare_and_record_preserve_bound_provenance_manifest_without_upgrading_source_gate(self) -> None:
        source = self.next_path("provenance-source")
        template = self.next_path("provenance-template.json")
        template.write_text(json.dumps({"context_template_version": 1, "provenance": {
            "version": 1, "sources": [], "facts": [], "bindings": [], "history": [],
        }}), encoding="utf-8")
        graph = source / "diagram.drawio"
        context = source / "context.json"
        manifest = source / "completion.json"
        built = run_tool("build", "--spec", str(self.spec_path), "--context", str(template),
                         "--output", str(graph), "--context-output", str(context),
                         "--completion-manifest", str(manifest), "--strict")
        self.assertEqual(built.returncode, 0, built.stderr.decode(errors="replace"))
        graph_raw, context_raw, manifest_raw = graph.read_bytes(), context.read_bytes(), manifest.read_bytes()
        graph_sha = hashlib.sha256(graph_raw).hexdigest()
        context_sha = hashlib.sha256(context_raw).hexdigest()
        prepared = self.next_path("provenance-prepared")
        result = run_tool("review", "prepare", "--input", str(graph),
                          "--expected-input-sha256", graph_sha, "--context", str(context),
                          "--expected-context-sha256", context_sha,
                          "--input-completion-manifest", str(manifest), "--output", str(prepared))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["semantic_context"]["source_gate"], "incomplete")
        run = json.loads((prepared / "run.json").read_text())
        self.assertEqual(run["context"]["completion_sha256"], hashlib.sha256(manifest_raw).hexdigest())
        self.assertEqual((prepared / "original/diagram.drawio").read_bytes(), graph_raw)
        self.assertEqual((prepared / "original/context.json").read_bytes(), context_raw)
        self.assertEqual((prepared / "original/completion.json").read_bytes(), manifest_raw)

        prepared_manifest = prepared / "completion.json"
        report = {
            "visual_report_version": 1, "run_id": run["run_id"],
            "prepared_sha256": hashlib.sha256(prepared_manifest.read_bytes()).hexdigest(),
            "artifact_sha256": graph_sha, "context_sha256": context_sha, "round_index": 0,
            "export": None, "previews": [],
            "reviews": {"agent": self.no_review(), "human": self.no_review()},
        }
        evidence = self.next_path("provenance-evidence")
        evidence.mkdir()
        report_raw = self.bundle.bundle_json(report)
        report_path = evidence / "report.json"
        report_path.write_bytes(report_raw)
        recorded = self.next_path("provenance-record")
        result = run_tool(
            "review", "record", "--prepared", str(prepared_manifest),
            "--expected-prepared-sha256", hashlib.sha256(prepared_manifest.read_bytes()).hexdigest(),
            "--input", str(graph), "--expected-input-sha256", graph_sha,
            "--context", str(context), "--expected-context-sha256", context_sha,
            "--input-completion-manifest", str(manifest), "--evidence-dir", str(evidence),
            "--report", str(report_path), "--expected-report-sha256", hashlib.sha256(report_raw).hexdigest(),
            "--output", str(recorded))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["semantic_context"]["source_gate"], "incomplete")
        self.assertEqual(body["review"]["states"]["preview_export"], "not_run")
        self.assertTrue((recorded / "completion.json").is_file())
        self.assertEqual((graph.read_bytes(), context.read_bytes(), manifest.read_bytes()),
                         (graph_raw, context_raw, manifest_raw))

    def test_review_workflow_never_calls_build_patch_route_or_context_mutation_entrypoints(self) -> None:
        prepared = self.next_path("read-only-prepared")
        prepare_args = self.loaded.tool.build_parser().parse_args([
            "review", "prepare", "--input", str(self.graph_path),
            "--expected-input-sha256", self.graph_sha, "--output", str(prepared)])
        forbidden = (
            mock.patch.object(self.loaded.document, "write_tree", side_effect=AssertionError("writer called")),
            mock.patch.object(self.loaded.build, "build_tree", side_effect=AssertionError("build called")),
            mock.patch.object(self.loaded.roundtrip, "patch_tree", side_effect=AssertionError("patch called")),
            mock.patch.object(self.loaded.routing, "plan_route_batch", side_effect=AssertionError("route called")),
            mock.patch.object(self.loaded.tool.context_workflow, "run_context_workflow",
                              side_effect=AssertionError("context mutation called")),
        )
        with forbidden[0], forbidden[1], forbidden[2], forbidden[3], forbidden[4]:
            receipt, code = self.workflow.run_review_workflow(prepare_args)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["action"], "prepare")

        report = self.report_for(prepared)
        evidence = self.next_path("read-only-evidence")
        evidence.mkdir()
        report_raw = self.bundle.bundle_json(report)
        report_path = evidence / "report.json"
        report_path.write_bytes(report_raw)
        prepared_path = prepared / "completion.json"
        record_args = self.loaded.tool.build_parser().parse_args([
            "review", "record", "--prepared", str(prepared_path),
            "--expected-prepared-sha256", hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
            "--input", str(self.graph_path), "--expected-input-sha256", self.graph_sha,
            "--evidence-dir", str(evidence), "--report", str(report_path),
            "--expected-report-sha256", hashlib.sha256(report_raw).hexdigest(),
            "--output", str(self.next_path("read-only-record"))])
        forbidden = (
            mock.patch.object(self.loaded.document, "write_tree", side_effect=AssertionError("writer called")),
            mock.patch.object(self.loaded.build, "build_tree", side_effect=AssertionError("build called")),
            mock.patch.object(self.loaded.roundtrip, "patch_tree", side_effect=AssertionError("patch called")),
            mock.patch.object(self.loaded.routing, "plan_route_batch", side_effect=AssertionError("route called")),
            mock.patch.object(self.loaded.tool.context_workflow, "run_context_workflow",
                              side_effect=AssertionError("context mutation called")),
        )
        with forbidden[0], forbidden[1], forbidden[2], forbidden[3], forbidden[4]:
            receipt, code = self.workflow.run_review_workflow(record_args)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["action"], "record")
        self.assertFalse(receipt["review"]["can_repair"])

    def test_prepare_refuses_legacy_recoverable_and_unsafe_xml_without_migration(self) -> None:
        legacy = self.loaded.document.read_tree(self.graph_path)
        pool = self.loaded.document.find_pool(legacy)
        pool.set(self.loaded.contracts.DATA_SCHEMA_VERSION, "2")
        legacy_path = self.next_path("legacy.drawio")
        self.loaded.document.write_tree(legacy, legacy_path)

        recoverable = self.loaded.document.read_tree(self.graph_path)
        recoverable_pool = self.loaded.document.find_pool(recoverable)
        del recoverable_pool.attrib[self.loaded.contracts.DATA_MODEL_HASH]
        recoverable_path = self.next_path("recoverable.drawio")
        self.loaded.document.write_tree(recoverable, recoverable_path)

        unsafe_path = self.next_path("unsafe.drawio")
        unsafe_path.write_bytes(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><mxfile>&e;</mxfile>')
        for label, path, expected in (
                ("legacy", legacy_path, "context/artifact-unsupported"),
                ("recoverable", recoverable_path, "context/artifact-unsupported"),
                ("unsafe", unsafe_path, "context/artifact-unsupported")):
            with self.subTest(label=label):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                output = self.next_path(f"{label}-rejected")
                result = run_tool("review", "prepare", "--input", str(path),
                                  "--expected-input-sha256", digest, "--output", str(output))
                self.assert_code(result, expected)
                self.assertFalse(output.exists())

    def test_record_rejects_every_non_object_prepared_root_without_output(self) -> None:
        roots = {
            "empty-array": b"[]\n", "array": b"[1]\n", "null": b"null\n",
            "string": b'"text"\n', "integer": b"1\n", "float": b"1.5\n",
            "true": b"true\n", "false": b"false\n",
        }
        for name, raw in roots.items():
            with self.subTest(name=name):
                prepared = self.next_path("invalid-prepared")
                prepared.mkdir()
                manifest = prepared / "completion.json"
                manifest.write_bytes(raw)
                before = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
                output = self.next_path("invalid-record")
                result = run_tool(
                    "review", "record", "--prepared", str(manifest),
                    "--expected-prepared-sha256", hashlib.sha256(raw).hexdigest(),
                    "--output", str(output),
                )
                self.assert_code(result, "schema/type")
                self.assertFalse(output.exists())
                self.assertEqual((manifest.read_bytes(), manifest.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
