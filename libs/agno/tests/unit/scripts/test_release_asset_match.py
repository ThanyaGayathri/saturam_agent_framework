"""Tests for scripts/release_naming.py (shared by publish/install shell scripts)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "scripts"))

from release_naming import (  # noqa: E402
    assets_complete,
    classify_assets,
    is_first_party_requirement,
    normalize_tag,
    release_tag_for_version,
    wheel_dist_name,
)


def test_wheel_dist_name_hyphen_to_underscore() -> None:
    assert wheel_dist_name("egpt-infra") == "egpt_infra"
    assert wheel_dist_name("egpt") == "egpt"
    assert wheel_dist_name("egptctl") == "egptctl"


def test_egpt_infra_assets_complete() -> None:
    assets = [
        "egpt_infra-0.1.0-py3-none-any.whl",
        "egpt_infra-0.1.0.tar.gz",
    ]
    assert assets_complete(assets, "egpt-infra", "0.1.0")
    has_wheel, has_sdist = classify_assets(assets, "egpt-infra", "0.1.0")
    assert has_wheel and has_sdist


def test_hyphenated_name_does_not_match_underscore_artifacts() -> None:
    # Literal egpt-infra-* must NOT be treated as present when only egpt_infra exists
    # if someone forgot to normalize — classify uses normalization, so this is True.
    assets = ["egpt_infra-0.1.0-py3-none-any.whl", "egpt_infra-0.1.0.tar.gz"]
    assert assets_complete(assets, "egpt-infra", "0.1.0")


def test_does_not_match_longer_version_prefix() -> None:
    assets = ["egpt-0.1.10-py3-none-any.whl", "egpt-0.1.10.tar.gz"]
    assert not assets_complete(assets, "egpt", "0.1.0")
    has_wheel, has_sdist = classify_assets(assets, "egpt", "0.1.0")
    assert not has_wheel and not has_sdist


def test_wheel_only_incomplete() -> None:
    assets = ["egpt_infra-0.1.0-py3-none-any.whl"]
    assert not assets_complete(assets, "egpt-infra", "0.1.0")
    has_wheel, has_sdist = classify_assets(assets, "egpt-infra", "0.1.0")
    assert has_wheel and not has_sdist


def test_tag_normalization() -> None:
    assert normalize_tag("v0.1.0") == "0.1.0"
    assert normalize_tag("0.1.0") == "0.1.0"
    assert release_tag_for_version("0.1.0") == "v0.1.0"
    assert release_tag_for_version("v0.1.0") == "v0.1.0"


def test_first_party_requirement_detection() -> None:
    assert is_first_party_requirement("egpt-infra>=0.1.0")
    assert is_first_party_requirement("egpt_infra>=0.1.0")
    assert is_first_party_requirement("egpt")
    assert not is_first_party_requirement("httpx>=0.27")
    assert not is_first_party_requirement("pydantic")
