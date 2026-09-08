"""R2 -- the framework stays publishable: the egpt wheel carries nothing from product/.

The package's evidence: "The built wheel contains no file from product/ or frontend/, which is
the check that keeps the framework publishable." Builds libs/agno into a temporary directory
and lists what the wheel holds.

This passes today because `packages.find include = ["egpt*"]` scopes the wheel and root-level
directories are structurally unreachable. It is a regression guard on a property we have, and
its value is the day it fails.

Run:
    .venv/bin/python cookbook/94_retail/R02_wheel_contents.py
"""

import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_wheel_contents import build_wheel, check_wheel  # noqa: E402

print("R2 -- wheel contents")
print("=" * 78)

with tempfile.TemporaryDirectory() as tmp:
    print()
    print("1. Build libs/agno")
    print("-" * 78)
    # build_wheel() removes the `build/lib` tree setuptools leaves in libs/agno; left behind, it
    # makes validate.sh's mypy report a duplicate `egpt` module on every run until deleted.
    wheel = build_wheel(Path(tmp))
    print("   wheel:", wheel.name, f"({wheel.stat().st_size // 1024} KB)")
    print(
        "   libs/agno/build left behind:",
        (REPO_ROOT / "libs" / "agno" / "build").exists(),
    )

    print()
    print("2. What it contains")
    print("-" * 78)
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    top = sorted({n.split("/", 1)[0] for n in names})
    print("   members :", len(names))
    print("   top-level:", top)
    retail_hits = [
        n
        for n in names
        if "egpt_retail" in n or n.startswith(("product/", "frontend/", "deploy/"))
    ]
    print("   product/frontend/deploy members:", retail_hits or "none")
    package_data = [
        n for n in names if n.endswith((".json", ".html")) and n.startswith("egpt/")
    ]
    print(
        "   shipped data files:",
        len(package_data),
        "(registry templates, console page)",
    )

    print()
    print("3. The check that CI runs")
    print("-" * 78)
    problems = check_wheel(wheel)

print()
print("=" * 78)
if problems:
    print("Wheel is NOT clean:")
    for problem in problems:
        print("  -", problem)
    raise SystemExit(1)
print(
    "The egpt wheel carries only egpt/ and its dist-info. The framework stays publishable."
)
