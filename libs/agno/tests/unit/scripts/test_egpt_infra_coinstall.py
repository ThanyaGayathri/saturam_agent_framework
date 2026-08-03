"""Regression: egpt-infra must not clobber egpt/__init__.py on co-install."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]


def _pip_install_target(target: Path, wheel: Path) -> None:
    if shutil.which("uv"):
        cmd = [
            "uv",
            "pip",
            "install",
            "-q",
            "--no-deps",
            "--python",
            sys.executable,
            "--target",
            str(target),
            str(wheel),
        ]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--target", str(target), str(wheel)]
    subprocess.run(cmd, check=True)


@pytest.mark.skipif(sys.version_info < (3, 10), reason="build tooling expects modern Python")
def test_egpt_infra_coinstall_preserves_egpt_init(tmp_path: Path) -> None:
    """Build both wheels and install egpt then egpt-infra; PRODUCT_NAME must survive."""
    build = tmp_path / "build"
    build.mkdir()
    target = tmp_path / "site"
    target.mkdir()

    for pkg in (ROOT / "libs" / "agno", ROOT / "libs" / "agno_infra"):
        subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(build), str(pkg)],
            check=True,
            cwd=str(ROOT),
        )

    egpt_wheels = [
        w for w in sorted(build.glob("egpt-*.whl")) if w.name.startswith("egpt-") and not w.name.startswith("egpt_")
    ]
    infra_wheels = sorted(build.glob("egpt_infra-*.whl"))
    assert egpt_wheels, f"egpt wheel missing in {list(build.iterdir())}"
    assert infra_wheels, f"egpt_infra wheel missing in {list(build.iterdir())}"

    with zipfile.ZipFile(infra_wheels[0]) as zf:
        names = zf.namelist()
    assert "egpt/__init__.py" not in names, "egpt-infra must not ship egpt/__init__.py"
    assert "egpt/py.typed" not in names, "egpt-infra must not ship egpt/py.typed"

    _pip_install_target(target, egpt_wheels[0])
    _pip_install_target(target, infra_wheels[0])

    env = os.environ.copy()
    env["PYTHONPATH"] = str(target) + os.pathsep + env.get("PYTHONPATH", "")
    probe = (
        "from egpt import PRODUCT_NAME, __version__; "
        "import egpt.infra; "
        "import egpt.docker; "
        "assert PRODUCT_NAME == 'EGPT', PRODUCT_NAME; "
        "print(__version__, PRODUCT_NAME)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "EGPT" in result.stdout

    init_py = target / "egpt" / "__init__.py"
    assert init_py.is_file()
    assert init_py.stat().st_size > 0
