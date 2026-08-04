"""Tests for scripts/templates/rebrand_codemod.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "scripts" / "templates"))

from rebrand_codemod import (  # noqa: E402
    patch_aws_deploy,
    patch_dockerfile_railway,
    patch_fly_deploy,
    patch_gcp_deploy,
    patch_railway_deploy,
    warnings,
)


def test_check_dir_rejects_nonexistent() -> None:
    script = ROOT / "scripts" / "templates" / "rebrand_template.sh"
    result = subprocess.run(
        [str(script), "check", "/nonexistent/path"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, f"Expected failure on nonexistent dir, got: {result.stdout}"
    assert "Not a readable directory" in result.stderr


def test_check_dir_passes_clean() -> None:
    script = ROOT / "scripts" / "templates" / "rebrand_template.sh"
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [str(script), "check", tmpdir],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Expected success on clean dir, got: {result.stderr}"
        assert "No upstream-brand leakage found" in result.stdout


def test_aws_deploy_patch_adds_secret_flag(tmp_path: Path) -> None:
    warnings.clear()
    aws_dir = tmp_path / "scripts" / "aws"
    aws_dir.mkdir(parents=True)

    up_sh = aws_dir / "up.sh"
    up_sh.write_text('docker build --platform linux/amd64 -t "$IMAGE" .\n')

    patch_aws_deploy(tmp_path)

    content = up_sh.read_text()
    assert "--secret id=github_token,env=GITHUB_TOKEN" in content
    assert len(warnings) == 1
    assert "GITHUB_TOKEN" in warnings[0]
    warnings.clear()


def test_gcp_deploy_patch_adds_secret_flag(tmp_path: Path) -> None:
    warnings.clear()
    gcp_dir = tmp_path / "scripts" / "gcp"
    gcp_dir.mkdir(parents=True)

    redeploy_sh = gcp_dir / "redeploy.sh"
    redeploy_sh.write_text('docker build --platform linux/amd64 -t "$IMAGE" .\n')

    patch_gcp_deploy(tmp_path)

    content = redeploy_sh.read_text()
    assert "--secret id=github_token,env=GITHUB_TOKEN" in content
    assert len(warnings) == 1
    warnings.clear()


def test_fly_deploy_patch_adds_build_secret(tmp_path: Path) -> None:
    warnings.clear()
    fly_dir = tmp_path / "scripts" / "fly"
    fly_dir.mkdir(parents=True)

    redeploy_sh = fly_dir / "redeploy.sh"
    redeploy_sh.write_text('"$FLY" deploy --ha=false\n')

    patch_fly_deploy(tmp_path)

    content = redeploy_sh.read_text()
    assert '--build-secret "github_token=${GITHUB_TOKEN}"' in content
    assert len(warnings) == 1
    warnings.clear()


def test_railway_dockerfile_uses_arg(tmp_path: Path) -> None:
    warnings.clear()
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM agnohq/python:3.12\nRUN uv pip sync requirements.txt --system\n")

    patch_dockerfile_railway(tmp_path)

    content = dockerfile.read_text()
    assert "ARG GITHUB_TOKEN" in content
    assert "RUN GITHUB_TOKEN=" in content
    assert "--mount=type=secret" not in content
    warnings.clear()


def test_railway_deploy_patch_sets_variable(tmp_path: Path) -> None:
    warnings.clear()
    railway_dir = tmp_path / "scripts" / "railway"
    railway_dir.mkdir(parents=True)

    up_sh = railway_dir / "up.sh"
    up_sh.write_text("railway up --service agent-os -d\n")

    patch_railway_deploy(tmp_path)

    content = up_sh.read_text()
    assert 'railway variables --set "GITHUB_TOKEN=${GITHUB_TOKEN}"' in content
    assert "GITHUB_TOKEN is required" in content
    assert len(warnings) == 1
    warnings.clear()
