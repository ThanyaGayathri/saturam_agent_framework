#!/usr/bin/env python3
"""Fail if the published `egpt` wheel contains anything from product/, frontend/ or deploy/.

A regression guard on a property the repository already has: the wheel is built from
libs/agno with `packages.find include = ["egpt*"]`, so root-level directories are structurally
unreachable. Its value is that it fails the day someone adds a `package-dir`, a `MANIFEST.in`,
or moves retail code under libs/. Runs in CI's test-and-build after `python -m build`, and by
hand from cookbook/94_retail/R02_wheel_contents.py.

Usage:
    python scripts/check_wheel_contents.py libs/agno/dist/egpt-*.whl
    python scripts/check_wheel_contents.py --build        # build into a temp dir first
"""

from __future__ import annotations

import argparse
import glob
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = REPO_ROOT / "libs/agno"
FORBIDDEN = ("egpt_retail", "product/", "frontend/", "deploy/", "cookbook/")


def check_wheel(wheel: Path) -> List[str]:
    """Every member must live under egpt/ or the dist-info; none may name a forbidden tree."""
    problems: List[str] = []
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    top_level = sorted({n.split("/", 1)[0] for n in names})
    for name in names:
        if not (name.startswith("egpt/") or ".dist-info/" in name):
            problems.append(f"unexpected member outside egpt/ and dist-info: {name}")
        if any(marker in name for marker in FORBIDDEN):
            problems.append(f"forbidden path in wheel: {name}")
    print(f"{wheel.name}: {len(names)} members; top level {top_level}")
    return problems


def build_wheel(out_dir: Path) -> Path:
    """Build libs/agno into out_dir, leaving the source tree as it was.

    setuptools writes an intermediate `build/lib/egpt/` into the source tree even when `--outdir`
    points elsewhere, and mypy then reports `Duplicate module named "egpt"` on every validate.sh
    run until someone deletes it. Found the first time R02_wheel_contents.py ran before validate.

    `build/lib` is removed unconditionally, not only when `build/` did not already exist: this
    invocation writes `build/lib/egpt` regardless of prior state, so gating the cleanup on
    whether `build/` pre-existed skipped cleanup in exactly the case it was meant to handle --
    a `build/` directory already there (CI's own build step, an earlier cookbook run) is the
    normal case, not the exception, and left the stale module behind every time.
    """
    stale_lib = FRAMEWORK / "build" / "lib"
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(out_dir),
                str(FRAMEWORK),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        # capture_output hides setuptools' own error from the console; check=True's
        # CalledProcessError carries only the command line, which is useless for diagnosing a
        # build failure. Print what the build actually said before re-raising.
        sys.stderr.write(e.stdout or "")
        sys.stderr.write(e.stderr or "")
        raise
    finally:
        shutil.rmtree(stale_lib, ignore_errors=True)
    wheels = sorted(out_dir.glob("egpt-*.whl"))
    if not wheels:
        raise SystemExit(f"no egpt wheel produced in {out_dir}")
    return wheels[-1]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", nargs="*", help="Wheel paths or globs.")
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build libs/agno into a temp dir and check that.",
    )
    args = parser.parse_args(argv)

    problems: List[str] = []
    if args.build:
        with tempfile.TemporaryDirectory() as tmp:
            problems += check_wheel(build_wheel(Path(tmp)))
    paths = [Path(p) for pattern in args.wheels for p in glob.glob(pattern)]
    if not args.build and not paths:
        parser.error("give a wheel path or --build")
    for wheel in paths:
        problems += check_wheel(wheel)

    if problems:
        print("wheel contents: FAIL")
        for problem in problems:
            print("  -", problem)
        return 1
    print(
        "wheel contents: only egpt/ and its dist-info; nothing from product/, frontend/, deploy/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
