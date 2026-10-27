"""CLI helpers shared by package and diagram regression tests.

This module contains no TestCase classes, so importing it cannot duplicate tests.
"""

import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "product-swimlane-drawio"
TOOL = SKILL / "scripts" / "drawio_swimlane.py"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "tools"))
from release_check import read_tool_version
from swimlane_loader import load_skill_modules

TOOL_VERSION = read_tool_version(ROOT)


def run_tool(
    *args: str,
    check: bool = True,
    bind_patch_baseline: bool = True,
) -> subprocess.CompletedProcess[str]:
    command_args = list(args)
    if (
        bind_patch_baseline
        and command_args
        and command_args[0] == "patch"
        and "--expected-input-sha256" not in command_args
    ):
        input_index = command_args.index("--input") + 1
        input_path = Path(command_args[input_index])
        command_args.extend(
            ["--expected-input-sha256", hashlib.sha256(input_path.read_bytes()).hexdigest()]
        )
    result = subprocess.run(
        [sys.executable, "-B", str(TOOL), *command_args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result



def load_tool_module():
    return load_skill_modules(TOOL, module_name="drawio_swimlane_under_test").tool
