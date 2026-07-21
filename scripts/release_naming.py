#!/usr/bin/env python3
"""Shared helpers for EGPT GitHub Release publish/install scripts.

Wheel/sdist filenames use PEP 427/625 normalization: hyphens in the
distribution name become underscores (egpt-infra -> egpt_infra).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


FIRST_PARTY_DISTS = ("egpt", "egptctl", "egpt-infra")
FIRST_PARTY_NAMES = frozenset(FIRST_PARTY_DISTS)


def wheel_dist_name(dist_name: str) -> str:
    """Normalize a PyPI distribution name to its wheel/sdist filename form."""
    return dist_name.replace("-", "_")


def normalize_tag(tag: str) -> str:
    """Strip a leading ``v`` from a release tag for version comparison."""
    tag = tag.strip()
    if tag.startswith("v") or tag.startswith("V"):
        return tag[1:]
    return tag


def release_tag_for_version(version: str) -> str:
    """Canonical release tag is always ``v`` + PEP 440 version."""
    return f"v{normalize_tag(version)}"


def read_project_version(pyproject_path: Path) -> str:
    """Read ``[project].version`` from a pyproject.toml (Python 3.9+ safe)."""
    data = pyproject_path.read_bytes()
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # Python < 3.11
            import tomli as tomllib  # type: ignore[no-redef]
        return str(tomllib.loads(data.decode("utf-8"))["project"]["version"])
    except ModuleNotFoundError:
        pass
    except Exception:
        pass

    text = data.decode("utf-8")
    match = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise ValueError(f"Could not read version from {pyproject_path}")
    return match.group(1)


def wheel_filename_re(dist_name: str, version: str) -> re.Pattern[str]:
    wheel = wheel_dist_name(dist_name)
    return re.compile(rf"^{re.escape(wheel)}-{re.escape(version)}-.+\.whl$")


def sdist_filename_re(dist_name: str, version: str) -> re.Pattern[str]:
    wheel = wheel_dist_name(dist_name)
    return re.compile(rf"^{re.escape(wheel)}-{re.escape(version)}\.tar\.gz$")


def classify_assets(assets: Iterable[str], dist_name: str, version: str) -> Tuple[bool, bool]:
    """Return (has_wheel, has_sdist) for the given dist/version among asset names."""
    wheel_re = wheel_filename_re(dist_name, version)
    sdist_re = sdist_filename_re(dist_name, version)
    has_wheel = False
    has_sdist = False
    for name in assets:
        if wheel_re.fullmatch(name):
            has_wheel = True
        elif sdist_re.fullmatch(name):
            has_sdist = True
    return has_wheel, has_sdist


def assets_complete(assets: Iterable[str], dist_name: str, version: str) -> bool:
    has_wheel, has_sdist = classify_assets(assets, dist_name, version)
    return has_wheel and has_sdist


def missing_artifact_suffixes(assets: Iterable[str], dist_name: str, version: str) -> List[str]:
    """Return which of ('wheel', 'sdist') are missing."""
    has_wheel, has_sdist = classify_assets(assets, dist_name, version)
    missing: List[str] = []
    if not has_wheel:
        missing.append("wheel")
    if not has_sdist:
        missing.append("sdist")
    return missing


def is_first_party_requirement(req: str) -> bool:
    """True if a requirement string names a first-party EGPT distribution."""
    name = re.split(r"[<>=!~;\[]", req.strip(), maxsplit=1)[0].strip().lower()
    name = name.replace("_", "-")
    return name in {n.lower() for n in FIRST_PARTY_NAMES}


def third_party_requires_from_wheel(wheel_path: Path) -> List[str]:
    """Extract install_requires from a wheel, excluding first-party dists."""
    from zipfile import ZipFile

    requires: List[str] = []
    with ZipFile(wheel_path) as zf:
        meta_names = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        if not meta_names:
            return requires
        text = zf.read(meta_names[0]).decode("utf-8", errors="replace")
    for line in text.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        req = line[len("Requires-Dist:") :].strip()
        # Skip extras / environment markers that don't apply to base install by
        # keeping only unconditional requires (no semicolon) for the base set.
        # Extra-specific deps are handled when the extra is requested separately.
        if ";" in req:
            # Keep marker-guarded deps that are not extra-gated.
            if "extra ==" in req or 'extra ==' in req:
                continue
        if is_first_party_requirement(req):
            continue
        requires.append(req)
    return requires


def _cmd_wheel_name(args: argparse.Namespace) -> int:
    print(wheel_dist_name(args.dist_name))
    return 0


def _cmd_version(args: argparse.Namespace) -> int:
    print(read_project_version(Path(args.pyproject)))
    return 0


def _cmd_tag(args: argparse.Namespace) -> int:
    print(release_tag_for_version(args.version))
    return 0


def _cmd_normalize_tag(args: argparse.Namespace) -> int:
    print(normalize_tag(args.tag))
    return 0


def _cmd_assets_status(args: argparse.Namespace) -> int:
    assets = [line.strip() for line in sys.stdin if line.strip()]
    has_wheel, has_sdist = classify_assets(assets, args.dist_name, args.version)
    if has_wheel and has_sdist:
        print("complete")
    elif has_wheel:
        print("wheel_only")
    elif has_sdist:
        print("sdist_only")
    else:
        print("missing")
    return 0


def _cmd_third_party_requires(args: argparse.Namespace) -> int:
    reqs: List[str] = []
    seen = set()
    for wheel in args.wheels:
        for req in third_party_requires_from_wheel(Path(wheel)):
            if req not in seen:
                seen.add(req)
                reqs.append(req)
    for req in reqs:
        print(req)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("wheel-name", help="Normalize dist name for wheel filenames")
    p.add_argument("dist_name")
    p.set_defaults(func=_cmd_wheel_name)

    p = sub.add_parser("version", help="Read version from pyproject.toml")
    p.add_argument("pyproject")
    p.set_defaults(func=_cmd_version)

    p = sub.add_parser("tag", help="Canonical v-prefixed release tag for a version")
    p.add_argument("version")
    p.set_defaults(func=_cmd_tag)

    p = sub.add_parser("normalize-tag", help="Strip leading v from a tag")
    p.add_argument("tag")
    p.set_defaults(func=_cmd_normalize_tag)

    p = sub.add_parser("assets-status", help="Read asset names on stdin; print status")
    p.add_argument("dist_name")
    p.add_argument("version")
    p.set_defaults(func=_cmd_assets_status)

    p = sub.add_parser("third-party-requires", help="Print third-party Requires-Dist from wheels")
    p.add_argument("wheels", nargs="+")
    p.set_defaults(func=_cmd_third_party_requires)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
