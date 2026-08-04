#!/usr/bin/env python3
"""Brand audit gate for shipped code (EGPT-0.12, aicoe-tracker#149).

Greps the three shipped packages for `agno` / `agentos` and fails on anything that
is not in the documented KEEP set below. The KEEP set is the point of this script:
it is the difference between "we decided to leave that" and "nobody noticed".

    ./scripts/brand_audit.py            # fail on unexpected hits
    ./scripts/brand_audit.py --summary  # also show what was allowed, by category

Scope is *shipped* code only. Tests, cookbooks and benchmarks are excluded: they do
not ship in a wheel and are not a brand surface.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import List, NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent

SHIPPED_PATHS = [
    "libs/agno/egpt",
    "libs/agno_infra/egpt",
    "libs/egptctl/egptctl",
]

# Every entry is a documented decision, not a convenience. Adding one means deciding
# the brand leak is acceptable; say why in the comment.
KEEP_RULES: List[tuple] = [
    # -- English words that merely contain the substring (agnostic, diagnose, ...) -
    ("false-positive", re.compile(r"agnostic", re.I)),
    ("false-positive", re.compile(r"[a-z]agno[a-z]", re.I)),
    # -- Third-party package and class names we do not own -----------------------
    ("third-party", re.compile(r"openinference[-_.]instrumentation[-_.]agno|AgnoInstrumentor")),
    # -- Upstream infrastructure with no Saturam equivalent (DEFER, see #149) -----
    # Live, working resources: agnohq/* images back `ag infra up` and this repo's own
    # system-test compose file; agno-public hosts the sample data the demos download.
    ("defer-upstream-assets", re.compile(r"agnohq|agno-public")),
    # -- Defensive telemetry blocklist: these hosts must stay listed to stay blocked
    ("keep-telemetry-blocklist", re.compile(r"os-api\.agno\.com|api-stg\.agno\.com")),
    # -- CORS default allowlist (EGPT-0.12 explicitly out of scope; own decision) --
    ("defer-cors-allowlist", re.compile(r"https://(app|os|os-stg|www)?\.?agno\.com")),
    ("defer-cors-allowlist", re.compile(r'"(www\.)?agno\.com"')),
    # -- Factually correct citation of an upstream bug ----------------------------
    ("keep-upstream-citation", re.compile(r"github\.com/agno-agi/agno/issues/")),
    # -- Deprecated public aliases kept for one release (intentional back-compat) --
    (
        "keep-deprecated-alias",
        re.compile(
            r"\b(AgentOS[A-Za-z]*|AgnoError|AgnoLogger|AgnoCliConfig|AgnoAPISettings"
            r"|AgnoMetadata[A-Za-z]*|agentos_client|agentos_config|agentos_docker"
            r"|agentos_aws|agentos_railway|agentos_fly|agentos_gcp)\b"
        ),
    ),
    # -- Legacy env prefix, honoured for one release (see egpt.brand.LEGACY_ENV_PREFIX)
    ("keep-legacy-env", re.compile(r"\bAGNO_[A-Z0-9_]+|\bAGENTOS_[A-Z0-9_]+|\bAGNO_\b|\bLEGACY_ENV_PREFIX\b")),
    # -- Persistence identity: renaming re-points deployments at empty tables ------
    # Covers the documented `agno_*` legacy table prefix and its prose in db/README.
    ("keep-persistence-identity", re.compile(r"agno_json_db|LEGACY_TABLE_PREFIX|agno_\*|agno_\b|agno_\"")),
    # Container-level namespaces (schema, db name, key prefix, bucket path, docker
    # network). Renaming silently orphans existing data one level up, so they are
    # deliberately frozen -- see libs/agno/egpt/db/README.md.
    (
        "keep-persistence-identity",
        re.compile(r"(db_schema|db_name|db_prefix|database|network|prefix|prefix_suffix)\s*[:=].{0,12}\"agno/?\""),
    ),
    ("keep-persistence-identity", re.compile(r"db_schema|\"agno/\"|or \"agno\"|get\(\"database\", \"agno\"\)")),
    # Mongo keeps its legacy database name for the same reason.
    ("keep-persistence-identity", re.compile(r"db_name.{0,40}else \"agno\"")),
    # -- Local cache directory and logger namespace. Renaming the logger breaks
    # operator logging configs that filter on `agno.*`; the cache dir is keyed to it.
    ("defer-log-namespace", re.compile(r"\.agno\b|logging\.getLogger|\"agno\.\{|agno\.\{logger_name\}|name \"agno\.")),
    # -- `[tool.agno]` pyproject section read from existing infra projects --------
    ("keep-config-key", re.compile(r"\[?tool\.?\"?agno|\"agno\"\s*tool|get\(\"agno\"")),
    # -- Wire field on GET /, consumed by egptctl discovery. Renaming breaks every
    # existing client; tracked as a follow-up (additive `egpt_version`).
    ("defer-wire-api-name", re.compile(r"agno_version|the agno framework")),
    # -- Legacy `-t` template values still accepted by `ag infra create` ----------
    ("keep-legacy-alias", re.compile(r"\"agentos-(docker|aws|railway|fly|gcp)\"")),
    # -- Internal, non-user-facing identifiers: locals, dict keys, redis keys ------
    ("keep-internal-identifier", re.compile(r"_?agno[_-][a-z0-9_]+\b")),
    ("keep-internal-identifier", re.compile(r"agno:[a-z:_]+")),
    ("keep-internal-identifier", re.compile(r"[A-Z0-9_]*AGNO[A-Z0-9_]*")),
    ("keep-internal-identifier", re.compile(r"\bAgno[A-Z]\w*")),
    # `_agno` is the reserved metadata key stripped from user-supplied metadata.
    ("keep-internal-identifier", re.compile(r"_agno\b|``_agno``|\bagno_infra\b")),
    ("keep-internal-identifier", re.compile(r"agentos_client|_warned_agentos\w*")),
    # -- Live MCP tool name and public Literal values. Renaming `get_agentos_config`
    # breaks every connected MCP client mid-session; `preserve_agentos` is a public
    # API option value. Both are back-compat surface, tracked as a follow-up.
    ("defer-wire-api-name", re.compile(r"get_agentos_config|preserve_agentos")),
    # `protocol="agentos"` is the wire value of the REST transport, not a brand string
    ("keep-internal-identifier", re.compile(r"\"agentos\"|'agentos'")),
    # -- OpenTelemetry span attribute names. Wire-facing, but renaming silently
    # breaks every existing dashboard and saved query; tracked as a follow-up
    # alongside the `agno_version` field (see #149 "out of scope").
    ("defer-otel-attributes", re.compile(r"agno\.(run|team|agent|workflow|session|user)\.[a-z_]+")),
    # -- Paths into the private specs/ repo, which keeps its upstream layout -------
    ("keep-specs-path", re.compile(r"specs/agno/")),
    # -- Real credential prefix minted by the server; renaming breaks live tokens --
    ("keep-token-prefix", re.compile(r"agno_pat_")),
    # -- The `ag` infra CLI command name; rename deferred past Epic 0 --------------
    ("defer-infra-cli", re.compile(r"\bag infra\b|\bag ws\b")),
    # -- The libs/agno directory name (not user- or wire-facing) ------------------
    ("keep-directory-name", re.compile(r"libs/agno\b")),
]


# Line-level allow: prose that DOCUMENTS a deferred or legacy name. These lines exist
# *because* the old name does -- stripping it would delete the migration note that makes
# the decision discoverable. Matched against the whole line, not subtracted from it.
MIGRATION_DOC = re.compile(
    r"legacy|renamed|earlier versions|clean break|intentionally kept|upstream"
    r"|not migrated|pre-rename|deprecat",
    re.I,
)


class Hit(NamedTuple):
    path: str
    line_no: int
    text: str


def collect_hits() -> List[Hit]:
    proc = subprocess.run(
        ["git", "grep", "-inE", "agno|agentos", "--", *SHIPPED_PATHS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # git grep exits 1 when there are no matches at all, which is a clean result.
    if proc.returncode not in (0, 1):
        raise SystemExit(f"git grep failed: {proc.stderr.strip()}")

    hits = []
    for raw in proc.stdout.splitlines():
        path, _, rest = raw.partition(":")
        line_no, _, text = rest.partition(":")
        hits.append(Hit(path, int(line_no), text))
    return hits


def classify(hit: Hit) -> str | None:
    """Return the KEEP category for this line, or None if it is a finding."""
    if MIGRATION_DOC.search(hit.text):
        return "keep-migration-doc"

    residue = hit.text
    for _, pattern in KEEP_RULES:
        residue = pattern.sub("", residue)
    # A line is allowed only when stripping the KEEP patterns removes every mention.
    if re.search(r"agno|agentos", residue, re.I):
        return None
    for category, pattern in KEEP_RULES:
        if pattern.search(hit.text):
            return category
    return "keep-uncategorised"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="store_true", help="show allowed hits by category")
    args = parser.parse_args()

    hits = collect_hits()
    findings = []
    allowed = Counter()

    for hit in hits:
        category = classify(hit)
        if category is None:
            findings.append(hit)
        else:
            allowed[category] += 1

    if args.summary:
        print(f"scanned {len(hits)} matching lines across {len(SHIPPED_PATHS)} shipped packages")
        for category, count in sorted(allowed.items(), key=lambda kv: -kv[1]):
            print(f"  {count:5d}  {category}")
        print()

    if findings:
        print(f"brand audit FAILED: {len(findings)} unexpected agno/agentos reference(s)\n")
        for hit in findings:
            print(f"  {hit.path}:{hit.line_no}: {hit.text.strip()[:140]}")
        print(
            "\nEither rebrand the reference, or add it to KEEP_RULES in scripts/brand_audit.py "
            "with a comment explaining the decision."
        )
        return 1

    print(f"brand audit passed: {sum(allowed.values())} known reference(s), 0 findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
