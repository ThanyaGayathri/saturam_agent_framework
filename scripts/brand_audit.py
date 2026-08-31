#!/usr/bin/env python3
"""Brand audit gate for shipped code (EGPT-0.12, aicoe-tracker#149).

Greps the shipped packages and their packaging metadata for `agno` / `agentos` and
fails on anything that is not in the documented KEEP set below. The KEEP set is the
point of this script: it is the difference between "we decided to leave that" and
"nobody noticed".

    ./scripts/brand_audit.py            # fail on unexpected hits
    ./scripts/brand_audit.py --summary  # also show what was allowed, by category

Two layers, in order:

1. DENY_RULES -- the surfaces named in the ticket's acceptance criteria (install
   hints, outbound identity headers, packaging contact and URLs). A hit here is
   always a finding. It cannot be silenced by adding a KEEP rule, because an
   over-broad KEEP rule is exactly how these leaked back in before.
2. KEEP_RULES -- the allowlist. A line passes only when subtracting every KEEP
   pattern removes every mention of the brand.

The exemplars in SELF_TEST_CASES are re-checked on every run, so loosening a rule
until it stops catching a known regression fails the gate immediately.

Two trees are scanned:

* SHIPPED_PATHS -- the packages and packaging metadata that go into a wheel.
* DOCS_PATHS -- cookbooks, setup scripts and repo docs (EGPT-0.13, #150). Not
  shipped, but they are the onboarding path: a README that says `pip install agno`
  installs a package we do not own, and a snippet that says `from agno.agent import`
  teaches a namespace this repo does not install. Both get two extra DENY rules.

Benchmarks and libs/*/tests stay out of scope: they are not a brand surface. The
rebrand tooling itself (this file, the codemod templates) is excluded by pathspec --
it has to quote the strings it exists to catch.
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
    # Packaging metadata ships to whoever installs the wheels (acceptance criterion 1).
    "libs/agno/pyproject.toml",
    "libs/agno_infra/pyproject.toml",
    "libs/egptctl/pyproject.toml",
]

# EGPT-0.13 (aicoe-tracker#150): the docs tree. Not shipped in a wheel, but every
# path here is a user-facing setup or learning surface, and the 0.12 gate excluded
# it by design -- which is how `pip install agno` survived in 53 cookbook READMEs.
DOCS_PATHS = [
    "cookbook",
    "scripts",
    "README.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    ".cursorrules",
    "libs/agno/README.md",
    "libs/agno_infra/README.md",
    "libs/egptctl/README.md",
    "libs/agno/migrations/README.md",
    # Agent-facing instructions. These tell a coding agent which repo to act on, so a
    # stale org here is a live wrong-repo command, not cosmetics (EGPT-0.13 review).
    "AGENTS.md",
    "CLAUDE.md",
    "PROVENANCE.md",
]

# PROVENANCE.md records which project this repo was forked from; naming
# agno-agi/agno there is the whole point of the file, not a stale clone
# instruction. Exact match, as with UPSTREAM_HOST_SITES.
PROVENANCE_SITES = ("PROVENANCE.md",)

# The rebrand tooling necessarily quotes the strings it exists to catch, so scanning
# it would flag its own exemplars. Path-scoped, in the same spirit as
# UPSTREAM_HOST_SITES below.
DOCS_EXEMPT_PATHSPECS = [
    ":!scripts/brand_audit.py",
    ":!scripts/rebrand_namespace.py",
    ":!scripts/templates",
]

# Stripped before the DENY pass, so an install line is judged on the package it
# names rather than on incidental brand text:
#   * real third-party distributions whose names embed the brand, and
#   * references to the frozen libs/agno directory, including the shell and
#     PowerShell variables that hold it. `pip install -e "${AGNO_DIR}"` installs
#     this repo's own sources -- the opposite of an upstream install hint.
DOCS_PRESTRIP = re.compile(
    r"openinference[-_.]instrumentation[-_.]agno|lightrag[-_]agno"
    r"|libs[/\\]agno(_infra)?|%AGNO(_INFRA)?_DIR%|\$\{?AGNO(_INFRA)?_DIR\}?|\$Agno(Infra)?Dir",
    re.I,
)

# An f-string interpolation holds an identifier, not brand text: the value in
# `"user-agent": f"{agno_api_settings.app_name}/..."` is branded at runtime even though
# the variable is not. Only quote-free braces are stripped, so a TOML inline table
# (`{name = "Agno Team", email = "hello@agno.com"}`) stays intact and stays in scope.
INTERPOLATION = re.compile(r"\{[^{}\n'\"]*\}")

# The only files that legitimately name upstream agno hosts: the two CORS default
# allowlists (#149 lists them as an explicit DEFER) and the telemetry blocklist, which
# has to name the hosts in order to block them. Every other file is held to the rule.
# Membership is exact -- `os/settings_v2.py` is not covered by `os/settings.py`.
UPSTREAM_HOST_SITES = (
    "libs/agno/egpt/os/settings.py",
    "libs/agno/egpt/os/utils.py",
    "libs/agno/egpt/api/settings.py",
)

# Surfaces the ticket exists to protect. A match here is always a finding: these are
# the exact shapes that shipped as "Agno" through EGPT-0.4-0.10 while the code paths
# underneath were already renamed. Entries are (name, pattern, exempt_paths).
#
# These patterns only ever see lines that already matched `agno|agentos`, so they can
# be generous about surrounding text without becoming noisy.
DENY_RULES: List[tuple] = [
    # `pip install agno...` sends users to a package we do not own -- on public PyPI
    # `egpt` is an unrelated project, so hints must name the real dependency instead.
    # The window covers `pip install -U agno` and `uv pip install 'agno[slack]'`.
    ("install-hint", re.compile(r"pip install[^\n]{0,24}?\bagno", re.I), ()),
    # Identity we put on the wire. `user[-_]agent` catches both the header spelling
    # and the module-constant spelling (`USER_AGENT = "agno-..."`).
    (
        "outbound-identity",
        re.compile(
            r"(user[-_]agent|x-source|driverinfo\(name)\s*[\"':=(]?[^\n]{0,80}?agno",
            re.I,
        ),
        (),
    ),
    # `headers["user-agent"] += "; Origin/agno"`. Its own rule rather than a branch of
    # the alternation above, where the anchor is consumed and a second `agno` demanded.
    ("outbound-identity", re.compile(r"origin/agno", re.I), ()),
    # Author/maintainer contact in published wheel metadata.
    ("packaging-contact", re.compile(r"@agno\.com", re.I), ()),
    # Project URLs and doc links. Exempt only where the hosts are deliberately named.
    (
        "brand-url",
        re.compile(r"https?://(www\.|docs\.|os\.|app\.)?agno\.com", re.I),
        UPSTREAM_HOST_SITES,
    ),
]

# The docs tree carries every shipped-code rule plus two of its own. Both describe
# instructions a reader would follow and find broken, which is what makes them DENY
# rather than KEEP candidates.
DOCS_DENY_RULES: List[tuple] = DENY_RULES + [
    # A snippet that imports `agno` teaches a namespace this repo does not install:
    # the distribution is `egpt`. Cookbook .py was codemodded in EGPT-0.4, but the
    # markdown around it and .cursorrules were not.
    (
        "stale-import",
        re.compile(r"\bfrom agno(\.[\w.]+)? import\b|^\s*import agno\b"),
        (),
    ),
    # Clone and setup instructions must point at this repo. Citations of upstream
    # issues stay -- they are factually about upstream (see keep-upstream-citation).
    (
        "clone-url",
        re.compile(r"github\.com/agno-agi/(?!agno/issues/)"),
        PROVENANCE_SITES,
    ),
]

# Regressions this gate must catch, re-verified on every run. Each entry is the real
# pre-rebrand text from the file named, so a rule that stops catching one of these is
# a rule that has been widened past its purpose. See EGPT-0.12.
SELF_TEST_CASES = [
    (
        "libs/agno/egpt/integrations/tools/sofya/sofya.py",
        '"User-Agent": "agno-sofya",',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/integrations/knowledge/loaders/github.py",
        '"User-Agent": "Agno-Knowledge",',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/integrations/tools/perplexity/perplexity.py",
        '"X-Source": "agno",',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/integrations/db/mongo/mongo.py",
        'DriverInfo(name="Agno", version=v)',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/integrations/tools/apify/apify.py",
        'headers["user-agent"] += "; Origin/agno"',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/integrations/models/google/utils.py",
        '"User-Agent": "Mozilla/5.0 (compatible; agno/1.0)"',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/scheduler/cron.py",
        'raise ImportError("pip install agno[scheduler]")',
        "install-hint",
    ),
    # The escape hatch for migration prose must not launder an install hint.
    (
        "libs/agno/egpt/integrations/_utils/streamlit.py",
        'raise ImportError("pip install agno")  # legacy',
        "install-hint",
    ),
    (
        "libs/agno/pyproject.toml",
        '{name = "Agno Team", email = "hello@agno.com"}',
        "packaging-contact",
    ),
    ("libs/agno/pyproject.toml", 'Homepage = "https://agno.com"', "brand-url"),
    # Evasions of an earlier, narrower draft of these rules. Each one shipped past the
    # first version of DENY, so each stays pinned here.
    (
        "libs/agno/egpt/integrations/tools/sofya/sofya.py",
        'USER_AGENT = "agno-sofya"',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/integrations/tools/apify/apify.py",
        'user_agent = "Origin/agno"',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/integrations/models/google/utils.py",
        '"User-Agent": "Mozilla/5.0 (Macintosh; X 10_15_7) agno/1.0"',
        "outbound-identity",
    ),
    (
        "libs/agno/egpt/integrations/db/clickhouse/README.md",
        "pip install -U agno",
        "install-hint",
    ),
    (
        "libs/agno/egpt/integrations/db/clickhouse/README.md",
        "uv pip install 'agno[clickhouse]'",
        "install-hint",
    ),
    (
        "libs/agno/pyproject.toml",
        'Documentation = "https://docs.agno.com"',
        "brand-url",
    ),
]

# The same idea for the docs tree: real pre-EGPT-0.13 text, re-checked every run.
SELF_TEST_CASES_DOCS = [
    (
        "cookbook/00_quickstart/README.md",
        "git clone https://github.com/agno-agi/agno.git",
        "clone-url",
    ),
    (
        "cookbook/90_models/cloudflare/README.md",
        "https://github.com/agno-agi/agno/blob/main/CONTRIBUTING.md",
        "clone-url",
    ),
    (
        "cookbook/93_components/README.md",
        "from agno.agent import Agent",
        "stale-import",
    ),
    (".cursorrules", "from agno.models.openai import OpenAIResponses", "stale-import"),
    (
        "cookbook/02_agents/README.md",
        "uv pip install -U openai ddgs agno",
        "install-hint",
    ),
    (
        "cookbook/05_agent_os/scheduler/README.md",
        "pip install agno[scheduler]",
        "install-hint",
    ),
    (
        "scripts/perf_setup.sh",
        "uv pip install -U agno langgraph langchain_openai",
        "install-hint",
    ),
    (".cursorrules", "Docs: https://docs.agno.com", "brand-url"),
    (
        "CODE_OF_CONDUCT.md",
        "reported to the community leaders at help@agno.com.",
        "packaging-contact",
    ),
    (
        "cookbook/00_quickstart/run.py",
        "2. Visit https://os.agno.com in your browser",
        "brand-url",
    ),
]

# The mirror image: lines that must stay ALLOWED. A widened DENY rule or a deleted
# KEEP rule shows up here as a failure instead of as a silent false positive.
SELF_TEST_ALLOWED_DOCS = [
    ("cookbook/scripts/run_pgvector.sh", "docker run -d ... agnohq/pgvector:16"),
    (
        "cookbook/00_quickstart/requirements.txt",
        "openinference-instrumentation-agno==0.1.34",
    ),
    (
        "cookbook/07_knowledge/04_advanced/README.md",
        "For Graph RAG: `pip install lightrag-agno`",
    ),
    ("scripts/dev_setup.sh", 'AGNO_DIR="${REPO_ROOT}/libs/agno"'),
    ("scripts/demo_setup.sh", "uv pip install -e libs/egptctl -e libs/agno[demo]"),
    ("cookbook/91_tools/mcp/github.py", "https://github.com/agno-agi/agno/issues/123"),
]

# Shipped-code lines that must be KEEP-covered (not denied).
# Pins the keep-compat-shim rule against accidental narrowing.
SELF_TEST_ALLOWED_SHIPPED = [
    (
        "libs/agno/egpt/tracing/_agno_compat.py",
        '        sys.modules["agno"] = egpt',
    ),
    (
        "libs/agno/egpt/tracing/_agno_compat.py",
        '            alias = "agno." + mod_name[5:]',
    ),
]

# Every entry is a documented decision, not a convenience. Adding one means deciding
# the brand leak is acceptable; say why in the comment.
KEEP_RULES: List[tuple] = [
    # -- The provenance record ----------------------------------------------------
    # PROVENANCE.md states which project this repo was forked from. Naming it is the
    # file's purpose; the row is matched exactly so no other upstream URL is allowed.
    (
        "keep-provenance",
        re.compile(r"Source project \| https://github\.com/agno-agi/agno"),
    ),
    # -- English words that merely contain the substring (agnostic, diagnose, ...) -
    ("false-positive", re.compile(r"agnostic", re.I)),
    ("false-positive", re.compile(r"[a-z]agno[a-z]", re.I)),
    # -- Third-party package and class names we do not own -----------------------
    (
        "third-party",
        re.compile(r"openinference[-_.]instrumentation[-_.]agno|AgnoInstrumentor"),
    ),
    # sys.modules aliasing in tracing/_agno_compat.py and setup.py (#200).
    # Narrowly scoped to patterns that only appear in the compat shim and its
    # call site — avoids accidentally absorbing un-rebranded stale imports
    # or other agno references elsewhere in the tree.
    (
        "keep-compat-shim",
        re.compile(
            # Code patterns unique to _agno_compat.py:
            r'sys\.modules\["agno"\]\s*='  # sys.modules["agno"] = egpt
            r'|"agno\."\s*\+\s*mod_name'  # alias = "agno." + mod_name[5:]
            r'|sys\.modules\.get\("agno"\)'  # existing = sys.modules.get("agno")
            r"|agno→egpt"  # log_debug message
            r'|k\s*==\s*"agno"'  # k == "agno" in _reset_for_testing
            r'|\.startswith\("agno\.'  # k.startswith("agno.") in _reset_for_testing
            # Docstring/comment patterns with rst backtick quoting:
            r"|``agno``\s*→\s*``egpt``"  # rst-quoted arrow
            r"|Production-side\s+``agno``"  # module docstring title
            r"|``agno\.\*``"  # "strips all ``agno.*`` entries"
            r"|``agno``\s+package"  # "a real ``agno`` package"
            r"|seed's\s+``agno``"  # "the seed's ``agno`` package"
            r"|alias\s+``agno``\s*→"  # "alias ``agno`` → ``egpt``"
            r"|``sys\.modules\[\"agno"  # "sys.modules["agno.*"]" in docstrings
            # setup.py comment about the alias call:
            r"|Register\s+agno\.\*\s*→\s*egpt\.\*"
            r"|hardcoded\s+``from\s+agno\."
            r"|``from\s+agno\.\w"
            # _agno_compat.py prose that won't appear in normal code:
            r"|clobbering\s+a\s+foreign"
            r"|real\s+agno\s+package"
            r"|agno→egpt\s+aliasing"
            r"|sys\.modules\['agno'\]\s+already"
        ),
    ),
    # `lightrag-agno` is a real PyPI package by another author (cookbook Graph RAG).
    ("third-party", re.compile(r"lightrag[-_]agno", re.I)),
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
    (
        "keep-legacy-env",
        re.compile(
            r"\bAGNO_[A-Z0-9_]+|\bAGENTOS_[A-Z0-9_]+|\bAGNO_\b|\bLEGACY_ENV_PREFIX\b"
        ),
    ),
    # -- Persistence identity: renaming re-points deployments at empty tables ------
    # Covers the documented `agno_*` legacy table prefix and its prose in db/README.
    (
        "keep-persistence-identity",
        re.compile(r"agno_json_db|LEGACY_TABLE_PREFIX|agno_\*|agno_\b|agno_\""),
    ),
    # Container-level namespaces (schema, db name, key prefix, bucket path, docker
    # network). Renaming silently orphans existing data one level up, so they are
    # deliberately frozen -- see libs/agno/egpt/db/README.md.
    (
        "keep-persistence-identity",
        re.compile(
            r"(db_schema|db_name|db_prefix|database|network|prefix|prefix_suffix)\s*[:=].{0,12}\"agno/?\""
        ),
    ),
    (
        "keep-persistence-identity",
        re.compile(r"db_schema|\"agno/\"|or \"agno\"|get\(\"database\", \"agno\"\)"),
    ),
    # Mongo keeps its legacy database name for the same reason.
    ("keep-persistence-identity", re.compile(r"db_name.{0,40}else \"agno\"")),
    # -- Local cache directory and logger namespace. Renaming the logger breaks
    # operator logging configs that filter on `agno.*`; the cache dir is keyed to it.
    (
        "defer-log-namespace",
        re.compile(
            r"\.agno\b|logging\.getLogger|\"agno\.\{|agno\.\{logger_name\}|name \"agno\."
        ),
    ),
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
    (
        "defer-otel-attributes",
        re.compile(r"agno\.(run|team|agent|workflow|session|user)\.[a-z_]+"),
    ),
    # -- Paths into the private specs/ repo, which keeps its upstream layout -------
    ("keep-specs-path", re.compile(r"specs/agno/")),
    # -- Real credential prefix minted by the server; renaming breaks live tokens --
    ("keep-token-prefix", re.compile(r"agno_pat_")),
    # -- The `ag` infra CLI command name; rename deferred past Epic 0 --------------
    ("defer-infra-cli", re.compile(r"\bag infra\b|\bag ws\b")),
    # -- The legacy MCP server entry name -----------------------------------------
    # egptctl still reads this key (LEGACY_MCP_SERVER_NAME = "agno" in
    # egptctl/clients/base.py), and the upgrade note has to name it to say which stale
    # entry to remove. Rewriting it in prose made the documented command delete the
    # entry `egpt connect` had just written (EGPT-0.13 review, finding 2). Scoped to
    # the exact phrasings so a bare `agno` elsewhere is still a finding.
    (
        "keep-legacy-mcp-entry",
        re.compile(
            r"was `agno` before|\"agno\" entry|--server-name agno|--scope user agno"
        ),
    ),
    # -- The libs/agno directory name (not user- or wire-facing) ------------------
    # Both separators: the .bat/.ps1 scripts spell it `libs\agno`, and a rule that
    # only knew the POSIX form let a sweep rewrite them to a directory that does not
    # exist while the audit stayed green (EGPT-0.13 review, finding 1).
    ("keep-directory-name", re.compile(r"libs[/\\]agno\b")),
]


# Docs-only KEEP rules. Kept separate from KEEP_RULES so the shipped pass is judged
# by exactly the rules it had before EGPT-0.13.
DOCS_KEEP_RULES: List[tuple] = KEEP_RULES + [
    # -- Verbatim captured output pasted into cookbook TEST_LOG.md ----------------
    # Python traceback frames from real runs, including the developer's workspace
    # path. Rewriting them would falsify the record the log exists to preserve, and
    # a pasted traceback is not a brand surface anyone reads for instructions.
    ("keep-captured-output", re.compile(r'File "/[^"]*"')),
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


def collect_hits(
    paths: List[str] = SHIPPED_PATHS, excludes: List[str] = []
) -> List[Hit]:
    proc = subprocess.run(
        ["git", "grep", "-inE", "agno|agentos", "--", *paths, *excludes],
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


def deny_reason(
    path: str,
    text: str,
    rules: List[tuple] = DENY_RULES,
    prestrip: re.Pattern | None = None,
) -> str | None:
    """Return the name of the DENY rule this line violates, or None.

    Checked before everything else, including the migration-prose escape hatch: a
    protected surface is a finding no matter what else the line says.

    `rules`/`prestrip` default to the shipped-code behaviour; the docs pass passes
    its own so that widening the docs rules can never loosen the shipped gate.
    """
    stripped = INTERPOLATION.sub("", text)
    if prestrip is not None:
        stripped = prestrip.sub("", stripped)
    for name, pattern, exempt_paths in rules:
        # Compared element-wise rather than with `in`: were an exempt tuple ever written
        # without its trailing comma it would be a plain string, and `in` would match on
        # substrings and exempt far more than intended. This way it exempts nothing.
        if any(path == exempt for exempt in exempt_paths):
            continue
        if pattern.search(stripped):
            return name
    return None


def classify(
    hit: Hit,
    rules: List[tuple] = DENY_RULES,
    prestrip: re.Pattern | None = None,
    keep_rules: List[tuple] | None = None,
) -> str | None:
    """Return the KEEP category for this line, or None if it is a finding."""
    keep_rules = KEEP_RULES if keep_rules is None else keep_rules
    if deny_reason(hit.path, hit.text, rules, prestrip) is not None:
        return None
    if MIGRATION_DOC.search(hit.text):
        return "keep-migration-doc"

    residue = hit.text
    for _, pattern in keep_rules:
        residue = pattern.sub("", residue)
    # A line is allowed only when stripping the KEEP patterns removes every mention.
    if re.search(r"agno|agentos", residue, re.I):
        return None
    for category, pattern in keep_rules:
        if pattern.search(hit.text):
            return category
    return "keep-uncategorised"


def self_test_failures() -> List[str]:
    """Check that each known regression is still caught by the rule that should catch it."""
    failures = []
    for path, text, expected in SELF_TEST_CASES:
        actual = deny_reason(path, text)
        if actual != expected:
            got = actual or "nothing"
            failures.append(
                f"{path}: {text.strip()[:80]!r}\n      expected {expected}, caught by {got}"
            )

    for path, text, expected in SELF_TEST_CASES_DOCS:
        actual = deny_reason(path, text, DOCS_DENY_RULES, DOCS_PRESTRIP)
        if actual != expected:
            got = actual or "nothing"
            failures.append(
                f"{path}: {text.strip()[:80]!r}\n      expected {expected}, caught by {got}"
            )

    # And the inverse: a rule widened until it swallows a legitimate line.
    for path, text in SELF_TEST_ALLOWED_DOCS:
        denied = deny_reason(path, text, DOCS_DENY_RULES, DOCS_PRESTRIP)
        if denied is not None:
            failures.append(
                f"{path}: {text.strip()[:80]!r}\n      expected to be allowed, denied by {denied}"
            )
            continue
        if (
            classify(
                Hit(path, 0, text), DOCS_DENY_RULES, DOCS_PRESTRIP, DOCS_KEEP_RULES
            )
            is None
        ):
            failures.append(
                f"{path}: {text.strip()[:80]!r}\n      expected a KEEP rule to cover it, none did"
            )
    # Shipped-code KEEP coverage (same logic as docs, but uses shipped DENY/KEEP).
    for path, text in SELF_TEST_ALLOWED_SHIPPED:
        denied = deny_reason(path, text)
        if denied is not None:
            failures.append(
                f"{path}: {text.strip()[:80]!r}\n      expected to be allowed, denied by {denied}"
            )
            continue
        if classify(Hit(path, 0, text), DENY_RULES, None, KEEP_RULES) is None:
            failures.append(
                f"{path}: {text.strip()[:80]!r}\n      expected a KEEP rule to cover it, none did"
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary", action="store_true", help="show allowed hits by category"
    )
    args = parser.parse_args()

    regressions = self_test_failures()
    if regressions:
        print(
            f"brand audit SELF-TEST FAILED: {len(regressions)} known regression(s) no longer caught\n"
        )
        for failure in regressions:
            print(f"  {failure}")
        print(
            "\nA DENY rule in scripts/brand_audit.py has been widened past its purpose. "
            "Tighten it rather than removing the exemplar."
        )
        return 1

    passes = [
        ("shipped", SHIPPED_PATHS, [], DENY_RULES, None, KEEP_RULES),
        (
            "docs",
            DOCS_PATHS,
            DOCS_EXEMPT_PATHSPECS,
            DOCS_DENY_RULES,
            DOCS_PRESTRIP,
            DOCS_KEEP_RULES,
        ),
    ]

    denied = []
    findings = []
    allowed = Counter()

    for label, paths, excludes, rules, prestrip, keeps in passes:
        hits = collect_hits(paths, excludes)
        pass_allowed = Counter()

        for hit in hits:
            reason = deny_reason(hit.path, hit.text, rules, prestrip)
            if reason is not None:
                denied.append((reason, hit))
                continue
            category = classify(hit, rules, prestrip, keeps)
            if category is None:
                findings.append(hit)
            else:
                pass_allowed[category] += 1

        allowed.update(pass_allowed)
        if args.summary:
            print(
                f"{label}: scanned {len(hits)} matching lines across {len(paths)} path(s)"
            )
            for category, count in sorted(pass_allowed.items(), key=lambda kv: -kv[1]):
                print(f"  {count:5d}  {category}")
            print()

    if denied or findings:
        print(
            f"brand audit FAILED: {len(denied) + len(findings)} unexpected agno/agentos reference(s)\n"
        )

    if denied:
        print(
            f"  Protected surfaces ({len(denied)}) -- these must be rebranded, not allowlisted:"
        )
        for reason, hit in denied:
            print(f"    [{reason}] {hit.path}:{hit.line_no}: {hit.text.strip()[:120]}")
        print(
            "\n  These surfaces are acceptance criteria of aicoe-tracker#149: install hints,\n"
            "  outbound identity headers, and published packaging metadata. Adding a KEEP rule\n"
            "  will not silence them.\n"
        )

    if findings:
        print(f"  Unclassified ({len(findings)}):")
        for hit in findings:
            print(f"    {hit.path}:{hit.line_no}: {hit.text.strip()[:140]}")
        print(
            "\n  Either rebrand the reference, or add it to KEEP_RULES in scripts/brand_audit.py "
            "with a comment explaining the decision."
        )

    if denied or findings:
        return 1

    print(f"brand audit passed: {sum(allowed.values())} known reference(s), 0 findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
