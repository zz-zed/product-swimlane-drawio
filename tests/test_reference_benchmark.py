import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "tools" / "reference_benchmark.py"


class ReferenceBenchmarkTests(unittest.TestCase):
    def test_report_keeps_geometry_and_omits_cell_labels(self) -> None:
        source = """<mxfile><diagram name="Page"><mxGraphModel><root>
        <mxCell id="0"/><mxCell id="1" parent="0"/>
        <mxCell id="pool" vertex="1" parent="1" style="swimlane;"><mxGeometry x="0" y="0" width="400" height="500" as="geometry"/></mxCell>
        <mxCell id="lane-a" vertex="1" parent="pool" style="swimlane;" value="Sensitive lane"><mxGeometry x="0" y="36" width="200" height="464" as="geometry"/></mxCell>
        <mxCell id="lane-b" vertex="1" parent="pool" style="swimlane;" value="Other lane"><mxGeometry x="200" y="36" width="200" height="464" as="geometry"/></mxCell>
        <mxCell id="node-a" vertex="1" parent="lane-a" value="Private step"><mxGeometry x="40" y="80" width="120" height="40" as="geometry"/></mxCell>
        <mxCell id="node-b" vertex="1" parent="lane-b" value="Another step"><mxGeometry x="40" y="180" width="120" height="40" as="geometry"/></mxCell>
        <mxCell id="edge-a" edge="1" parent="pool" source="node-a" target="node-b" value="Private outcome" style="edgeStyle=orthogonalEdgeStyle;"><mxGeometry relative="1" as="geometry"/></mxCell>
        </root></mxGraphModel></diagram></mxfile>"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reference.drawio"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(BENCHMARK), str(path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            report = json.loads(result.stdout)
            rendered = json.dumps(report)
            self.assertNotIn("Sensitive lane", rendered)
            self.assertNotIn("Private step", rendered)
            self.assertNotIn("Private outcome", rendered)
            page = report["references"][0]["pages"][0]
            self.assertEqual(page["lanes"], 2)
            self.assertEqual(page["nodes"], 2)
            self.assertEqual(page["edges"], 1)
            self.assertEqual(page["orthogonal_edge_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
