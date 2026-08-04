"""Tests for scripts/templates/rebrand_codemod.py and the rebrand_template.sh leak check.

The fixtures below are trimmed but faithful copies of the upstream
agno-agi/agentos-<flavor> deploy scripts. Faithfulness matters: several of these
scripts echo the deploy command in a banner before running it, so a naive
str.replace on the command mangles the banner too. Synthetic one-line fixtures
hide that class of bug.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "scripts" / "templates"))

import rebrand_codemod  # noqa: E402
from rebrand_codemod import (  # noqa: E402
    patch_compose,
    patch_docker_deploy,
    patch_dockerfile,
    patch_fly_deploy,
    patch_railway_deploy,
)

REBRAND_SH = ROOT / "scripts" / "templates" / "rebrand_template.sh"

RAILWAY_PUSH_LINE = 'railway variables --set "GITHUB_TOKEN=${GITHUB_TOKEN}" --service agent-os > /dev/null'

COLORS = """\
ORANGE='\\033[38;5;208m'
DIM='\\033[2m'
BOLD='\\033[1m'
NC='\\033[0m'
"""

DOCKER_PREFLIGHT_BLOCK = """\
if ! command -v docker &> /dev/null || ! docker info &> /dev/null; then
    echo "Docker not running. The image is built locally and pushed to ECR."
    exit 1
fi
"""

AWS_SCRIPT = f"""\
#!/bin/bash
set -e

{COLORS}
# Preflight
{DOCKER_PREFLIGHT_BLOCK}
IMAGE="123.dkr.ecr.us-east-1.amazonaws.com/egpt-os:latest"

echo ""
echo -e "${{ORANGE}}▸${{NC}} ${{BOLD}}Building and pushing image (linux/amd64)${{NC}}"
docker build --platform linux/amd64 -t "$IMAGE" .
docker push "$IMAGE"
"""

GCP_SCRIPT = AWS_SCRIPT.replace("pushed to ECR", "pushed to Artifact Registry")

# The banner on the second-to-last line echoes the deploy command. It must survive.
FLY_SCRIPT = f"""\
#!/bin/bash
set -e

{COLORS}
# Preflight
if command -v flyctl &> /dev/null; then
    FLY=flyctl
elif command -v fly &> /dev/null; then
    FLY=fly
else
    echo "flyctl not found. Install: https://fly.io/docs/flyctl/install/"
    exit 1
fi

echo ""
echo -e "${{DIM}}> ${{FLY}} deploy --ha=false${{NC}}"
echo ""
"$FLY" deploy --ha=false
"""

RAILWAY_PREFLIGHT_BLOCK = """\
# Preflight
if ! command -v railway &> /dev/null; then
    echo "Railway CLI not found. Install: https://docs.railway.com/cli#installing-the-cli"
    exit 1
fi
"""

RAILWAY_UP = f"""\
#!/bin/bash
set -e

{COLORS}
{RAILWAY_PREFLIGHT_BLOCK}
echo ""
echo -e "${{ORANGE}}▸${{NC}} ${{BOLD}}Deploying application${{NC}}"
echo ""
railway up --service agent-os -d
"""

# `railway up --service agent-os -d` appears TWICE here: in the banner and as the
# real command. This is the fixture that catches a count-blind replace.
RAILWAY_REDEPLOY = f"""\
#!/bin/bash
set -e

{COLORS}
{RAILWAY_PREFLIGHT_BLOCK}
echo ""
echo -e "${{ORANGE}}▸${{NC}} ${{BOLD}}Redeploying agent-os${{NC}}"
echo ""
echo -e "${{DIM}}> railway up --service agent-os -d${{NC}}"
echo ""
railway up --service agent-os -d
"""

DOCKERFILE = "FROM agnohq/python:3.12\nRUN uv pip sync requirements.txt --system\n"

COMPOSE = """\
services:
  api:
    build:
      context: .
      dockerfile: Dockerfile
"""


@pytest.fixture(autouse=True)
def _clear_warnings():
    """rebrand_codemod.warnings is module-global; isolate it per test."""
    rebrand_codemod.warnings.clear()
    yield
    rebrand_codemod.warnings.clear()


def _build_flavor(root: Path, flavor: str) -> Path:
    """Lay out a minimal upstream-shaped checkout for one flavor."""
    scripts = root / "scripts" / flavor
    scripts.mkdir(parents=True)
    if flavor in ("aws", "gcp"):
        body = AWS_SCRIPT if flavor == "aws" else GCP_SCRIPT
        (scripts / "up.sh").write_text(body)
        (scripts / "redeploy.sh").write_text(body)
    elif flavor == "fly":
        (scripts / "up.sh").write_text(FLY_SCRIPT)
        (scripts / "redeploy.sh").write_text(FLY_SCRIPT)
    elif flavor == "railway":
        (scripts / "up.sh").write_text(RAILWAY_UP)
        (scripts / "redeploy.sh").write_text(RAILWAY_REDEPLOY)
    return root


def _assert_valid_bash(path: Path) -> None:
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, f"{path.name} is not valid bash:\n{result.stderr}"


# --------------------------------------------------------------------------
# rebrand_template.sh check subcommand
# --------------------------------------------------------------------------


def test_check_dir_rejects_nonexistent() -> None:
    result = subprocess.run([str(REBRAND_SH), "check", "/nonexistent/path"], capture_output=True, text=True)
    assert result.returncode != 0, f"expected failure, got stdout={result.stdout!r}"
    assert "Not a readable directory" in result.stderr


def test_check_dir_passes_clean() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run([str(REBRAND_SH), "check", tmpdir], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "No upstream-brand leakage found" in result.stdout


# --------------------------------------------------------------------------
# Deploy-path patches: validate and forward GITHUB_TOKEN
# --------------------------------------------------------------------------


@pytest.mark.parametrize("flavor", ["aws", "gcp"])
def test_docker_flavors_validate_and_forward(tmp_path: Path, flavor: str) -> None:
    _build_flavor(tmp_path, flavor)
    patch_docker_deploy(tmp_path, flavor)

    assert rebrand_codemod.warnings == []
    for script in ("up.sh", "redeploy.sh"):
        path = tmp_path / "scripts" / flavor / script
        text = path.read_text()
        assert text.count('if [[ -z "${GITHUB_TOKEN:-}" ]]; then') == 1, "preflight guard missing/duplicated"
        assert text.count("--secret id=github_token,env=GITHUB_TOKEN") == 1
        assert "docker build --platform linux/amd64 -t" not in text, "unpatched build command left behind"
        _assert_valid_bash(path)


def test_fly_validates_forwards_and_keeps_banner(tmp_path: Path) -> None:
    _build_flavor(tmp_path, "fly")
    patch_fly_deploy(tmp_path)

    assert rebrand_codemod.warnings == []
    for script in ("up.sh", "redeploy.sh"):
        path = tmp_path / "scripts" / "fly" / script
        text = path.read_text()
        assert text.count('if [[ -z "${GITHUB_TOKEN:-}" ]]; then') == 1
        assert text.count('--build-secret "github_token=${GITHUB_TOKEN}"') == 1
        # The banner echoes ${FLY}, not "$FLY", so it must be untouched.
        assert 'echo -e "${DIM}> ${FLY} deploy --ha=false${NC}"' in text
        _assert_valid_bash(path)


def test_railway_validates_forwards_and_keeps_banner(tmp_path: Path) -> None:
    _build_flavor(tmp_path, "railway")
    patch_railway_deploy(tmp_path)

    assert rebrand_codemod.warnings == []
    for script in ("up.sh", "redeploy.sh"):
        path = tmp_path / "scripts" / "railway" / script
        text = path.read_text()
        assert text.count('if [[ -z "${GITHUB_TOKEN:-}" ]]; then') == 1, "preflight guard missing/duplicated"
        # Count the full line, not "railway variables --set": upstream up.sh already
        # pushes nine other variables that way, so the loose substring proves nothing.
        assert text.count(RAILWAY_PUSH_LINE) == 1, "variable push missing/duplicated"
        assert text.count("railway up --service agent-os -d") >= 1
        _assert_valid_bash(path)

    # Regression: the redeploy banner echoes the deploy command verbatim. An
    # occurrence-blind replace injects the guard into that echo string.
    redeploy = (tmp_path / "scripts" / "railway" / "redeploy.sh").read_text()
    assert 'echo -e "${DIM}> railway up --service agent-os -d${NC}"' in redeploy


def test_railway_pushes_variable_before_deploy(tmp_path: Path) -> None:
    _build_flavor(tmp_path, "railway")
    patch_railway_deploy(tmp_path)

    text = (tmp_path / "scripts" / "railway" / "up.sh").read_text()
    assert text.index(RAILWAY_PUSH_LINE) < text.index("railway up --service agent-os -d")


# --------------------------------------------------------------------------
# Upstream drift must surface as a warning, never a silent no-op
# --------------------------------------------------------------------------


def test_drifted_build_command_warns(tmp_path: Path) -> None:
    _build_flavor(tmp_path, "aws")
    up = tmp_path / "scripts" / "aws" / "up.sh"
    up.write_text(up.read_text().replace("docker build --platform linux/amd64 -t", "docker build --pull -t"))

    patch_docker_deploy(tmp_path, "aws")

    assert rebrand_codemod.warnings, "a renamed upstream command must warn, not silently no-op"
    assert any("forward github_token to docker build" in w for w in rebrand_codemod.warnings)


def test_missing_deploy_script_warns(tmp_path: Path) -> None:
    (tmp_path / "scripts" / "railway").mkdir(parents=True)
    (tmp_path / "scripts" / "railway" / "up.sh").write_text(RAILWAY_UP)
    # redeploy.sh deliberately absent

    patch_railway_deploy(tmp_path)

    assert any("redeploy.sh" in w and "not found" in w for w in rebrand_codemod.warnings)


# --------------------------------------------------------------------------
# Railway's ARG fallback, and the compose wiring that has to match it
# --------------------------------------------------------------------------


def test_railway_dockerfile_uses_arg_not_secret_mount(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text(DOCKERFILE)
    patch_dockerfile(tmp_path, "railway")

    text = (tmp_path / "Dockerfile").read_text()
    assert "ARG GITHUB_TOKEN" in text
    assert "--mount=type=secret" not in text, "Railway's builder cannot consume secret mounts"
    assert rebrand_codemod.warnings == []


@pytest.mark.parametrize("flavor", ["docker", "aws", "gcp", "fly"])
def test_non_railway_dockerfile_uses_secret_mount(tmp_path: Path, flavor: str) -> None:
    (tmp_path / "Dockerfile").write_text(DOCKERFILE)
    patch_dockerfile(tmp_path, flavor)

    text = (tmp_path / "Dockerfile").read_text()
    assert "--mount=type=secret,id=github_token" in text
    assert "ARG GITHUB_TOKEN" not in text
    assert rebrand_codemod.warnings == []


def test_railway_compose_passes_build_arg(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text(COMPOSE)
    patch_compose(tmp_path, "railway")

    text = (tmp_path / "compose.yaml").read_text()
    # A secrets: block would leave ARG unset and break `docker compose build`.
    assert "args:" in text and "- GITHUB_TOKEN" in text
    assert "secrets:" not in text
    assert rebrand_codemod.warnings == []


def test_non_railway_compose_passes_build_secret(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text(COMPOSE)
    patch_compose(tmp_path, "docker")

    text = (tmp_path / "compose.yaml").read_text()
    assert "secrets:" in text and "- github_token" in text
    assert "environment: GITHUB_TOKEN" in text
    assert rebrand_codemod.warnings == []
