"""Tests for scripts/brand_audit.py (the EGPT-0.12 brand gate, aicoe-tracker#149).

The gate's job is to fail on brand leakage in shipped code. These tests pin the part
that is easy to get wrong: an allowlist that quietly grows until it stops catching the
regressions it exists to catch. Three real ones motivated the DENY layer -- an
over-broad `keep-internal-identifier` allowed `"User-Agent": "agno-sofya"`, an
unscoped CORS rule allowed a pyproject `Homepage = "https://agno.com"`, and a trailing
`# legacy` comment laundered an install hint through the migration-prose escape.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "scripts"))

import brand_audit  # noqa: E402

CORS_LINE = '        "https://agno.com",'


def test_current_tree_is_clean():
    """The real tree passes. This is also the zero-false-positive guard: widening a
    DENY pattern until it catches legitimate code fails here first."""
    hits = brand_audit.collect_hits()
    assert hits, "expected the audit to find known references; git grep returned nothing"

    for hit in hits:
        verdict = brand_audit.classify(hit)
        assert verdict is not None, f"unclassified brand reference at {hit.path}:{hit.line_no}: {hit.text.strip()}"


def test_self_test_cases_all_pass():
    assert brand_audit.self_test_failures() == []


@pytest.mark.parametrize(
    ("path", "text", "expected"),
    brand_audit.SELF_TEST_CASES,
    ids=[f"{rule}-{i}" for i, (_, _, rule) in enumerate(brand_audit.SELF_TEST_CASES)],
)
def test_deny_catches_known_regressions(path, text, expected):
    assert brand_audit.deny_reason(path, text) == expected


def test_deny_outranks_keep_rules():
    """`keep-internal-identifier` matches `agno-sofya`; the deny layer must still win."""
    line = '            "User-Agent": "agno-sofya",'
    path = "libs/agno/egpt/integrations/tools/sofya/sofya.py"

    assert brand_audit.deny_reason(path, line) == "outbound-identity"
    assert brand_audit.classify(brand_audit.Hit(path, 83, line)) is None


def test_deny_outranks_migration_prose():
    """A trailing `# legacy` must not launder an install hint through MIGRATION_DOC."""
    path = "libs/agno/egpt/scheduler/cron.py"
    bare = 'raise ImportError("pip install agno[scheduler]")'
    excused = bare + "  # legacy, pre-rename"

    assert brand_audit.MIGRATION_DOC.search(excused), "precondition: the escape hatch would otherwise fire"
    assert brand_audit.deny_reason(path, excused) == "install-hint"
    assert brand_audit.classify(brand_audit.Hit(path, 20, excused)) is None
    assert brand_audit.classify(brand_audit.Hit(path, 20, bare)) is None


def test_interpolation_strip_spares_variable_names():
    """An f-string interpolation names a variable; the value it renders is branded."""
    line = '    "user-agent": f"{agno_api_settings.app_name}/{agno_api_settings.app_version}",'
    assert brand_audit.deny_reason("libs/agno/egpt/api/api.py", line) is None


def test_interpolation_strip_does_not_swallow_toml_inline_tables():
    """Braces containing quotes are TOML, not an f-string. Stripping them would make
    the packaging-contact rule silently dead in every pyproject."""
    line = '  {name = "Agno Team", email = "hello@agno.com"}'
    assert brand_audit.deny_reason("libs/agno/pyproject.toml", line) == "packaging-contact"


def test_upstream_host_sites_are_exempt():
    """These files name upstream hosts deliberately -- CORS defaults and the telemetry
    blocklist, which has to name a host in order to block it."""
    for path in brand_audit.UPSTREAM_HOST_SITES:
        assert brand_audit.deny_reason(path, CORS_LINE) is None, path


def test_exemption_is_exact_not_a_prefix():
    """`os/settings.py` must not exempt a neighbour that merely starts the same way."""
    assert brand_audit.deny_reason("libs/agno/egpt/os/settings_v2.py", CORS_LINE) == "brand-url"
    assert brand_audit.deny_reason("libs/agno/egpt/os/settings.py.bak", CORS_LINE) == "brand-url"


def test_exempt_entries_are_tuples_not_bare_strings():
    """`("path")` without a trailing comma is a string, and iterating it compares
    characters -- the exemption would silently stop applying."""
    for name, _, exempt in brand_audit.DENY_RULES:
        assert isinstance(exempt, tuple), name


def test_exempt_paths_still_exist():
    """A rename that leaves an exemption dangling should be noticed here, not by a
    deny firing months later in a file nobody connected to this list."""
    for name, _, exempt in brand_audit.DENY_RULES:
        for path in exempt:
            assert (ROOT / path).exists(), f"{name} exempts a path that no longer exists: {path}"


def test_self_test_case_paths_are_current():
    """Every exemplar must name the file the code it quotes actually lives in.

    Each entry is the real pre-rebrand text from the file named, and that path is what
    `self_test_failures` feeds to `deny_reason` as the value matched against a rule's
    exempt paths. An exemplar left pointing at a moved module would exercise a future
    path-scoped exemption against a location the code no longer occupies.

    Three extractions in a row shipped one -- EGPT-1.6 (805f3d22), 1.7 (dacda79d) and 1.8 --
    each caught in review rather than by a check, so it gets a check.

    Existence alone is too weak to catch the shape 1.8 produced. 1.6 and 1.7 deleted the old
    path outright, but 1.8 left a deprecation shim behind, so the file is still there while
    the code that justified the entry is not. The shim docstring marker is the discriminator;
    every shim from 1.4 onward carries it.
    """
    entries = [
        *((path, "SELF_TEST_CASES") for path, _text, _rule in brand_audit.SELF_TEST_CASES),
        *((path, "SELF_TEST_CASES_DOCS") for path, _text, _rule in brand_audit.SELF_TEST_CASES_DOCS),
        *((path, "SELF_TEST_ALLOWED_DOCS") for path, _text in brand_audit.SELF_TEST_ALLOWED_DOCS),
    ]
    assert len(entries) >= 30, (
        f"only {len(entries)} exemplars collected; the lists moved or were renamed, which makes this guard vacuous"
    )

    # Every offender is reported at once: a single extraction typically strands several, and
    # failing on the first would hide the rest until the next run.
    stale = []
    for path, origin in entries:
        target = ROOT / path
        if not target.is_file():
            stale.append(f"{origin}: {path} -- no such file")
        elif "Deprecated compatibility shim" in target.read_text(encoding="utf-8"):
            stale.append(f"{origin}: {path} -- deprecation shim; name the target module instead")

    assert not stale, (
        "Brand-audit exemplars name files the code no longer occupies:\n  "
        + "\n  ".join(stale)
        + "\nRetarget each to where the quoted line lives now. The text stays verbatim "
        "pre-rebrand; only the path moves."
    )


def test_shipped_paths_cover_every_lib_pyproject():
    """Packaging metadata is acceptance criterion 1; a new lib must not escape it."""
    for pyproject in sorted(ROOT.glob("libs/*/pyproject.toml")):
        rel = pyproject.relative_to(ROOT).as_posix()
        assert rel in brand_audit.SHIPPED_PATHS, rel


def test_upstream_repo_url_is_caught_by_the_residue_net():
    """No DENY rule covers the repository URL -- the KEEP-residue logic is what catches
    it. Pinned so broadening `keep-upstream-citation` to a bare `agno-agi/agno` cannot
    reopen the hole without failing here."""
    line = 'Repository = "https://github.com/agno-agi/agno"'
    path = "libs/agno/pyproject.toml"

    assert brand_audit.deny_reason(path, line) is None
    assert brand_audit.classify(brand_audit.Hit(path, 439, line)) is None


# --- The docs tree (EGPT-0.13, aicoe-tracker#150) ----------------------------


def test_docs_tree_is_clean():
    """Cookbooks, scripts and repo docs pass. Also the zero-false-positive guard for
    the two docs-only DENY rules: widening either one fails here first."""
    hits = brand_audit.collect_hits(brand_audit.DOCS_PATHS, brand_audit.DOCS_EXEMPT_PATHSPECS)
    assert hits, "expected the docs audit to find known references; git grep returned nothing"

    findings = [
        hit
        for hit in hits
        if brand_audit.classify(
            hit, brand_audit.DOCS_DENY_RULES, brand_audit.DOCS_PRESTRIP, brand_audit.DOCS_KEEP_RULES
        )
        is None
    ]
    assert findings == [], "\n".join(f"{h.path}:{h.line_no}: {h.text.strip()}" for h in findings)


@pytest.mark.parametrize("path,line,expected", brand_audit.SELF_TEST_CASES_DOCS)
def test_docs_deny_catches_known_regressions(path, line, expected):
    """Each entry is real pre-0.13 text. A rule that stops catching one has been
    widened past its purpose."""
    assert brand_audit.deny_reason(path, line, brand_audit.DOCS_DENY_RULES, brand_audit.DOCS_PRESTRIP) == expected


@pytest.mark.parametrize("path,line", brand_audit.SELF_TEST_ALLOWED_DOCS)
def test_docs_allowed_exemplars_stay_allowed(path, line):
    """The inverse pin: third-party distributions, agnohq images, local libs/agno
    paths and upstream issue citations must not become findings."""
    assert brand_audit.deny_reason(path, line, brand_audit.DOCS_DENY_RULES, brand_audit.DOCS_PRESTRIP) is None
    assert (
        brand_audit.classify(
            brand_audit.Hit(path, 0, line),
            brand_audit.DOCS_DENY_RULES,
            brand_audit.DOCS_PRESTRIP,
            brand_audit.DOCS_KEEP_RULES,
        )
        is not None
    )


def test_docs_exempt_pathspecs_are_negations():
    """A pathspec that lost its `:!` prefix would *restrict* the scan to the rebrand
    tooling instead of excluding it -- and the docs tree would stop being audited."""
    for spec in brand_audit.DOCS_EXEMPT_PATHSPECS:
        assert spec.startswith(":!"), spec
        assert (ROOT / spec[2:]).exists(), f"exempts a path that no longer exists: {spec}"


def test_docs_rules_extend_shipped_rules_without_mutating_them():
    """The docs pass must never loosen the shipped gate: same objects, in order,
    plus its own additions."""
    assert brand_audit.DOCS_DENY_RULES[: len(brand_audit.DENY_RULES)] == brand_audit.DENY_RULES
    assert len(brand_audit.DOCS_DENY_RULES) > len(brand_audit.DENY_RULES)


def test_shipped_pass_defaults_are_unchanged_by_the_docs_pass():
    """Calling deny_reason/classify without the docs arguments must behave exactly as
    it did before EGPT-0.13 -- the shipped tree is judged by DENY_RULES alone."""
    stale_import = "from agno.agent import Agent"
    # A docs-only rule, applied to shipped code through the default path: still None.
    assert brand_audit.deny_reason("libs/agno/egpt/agent/agent.py", stale_import) is None
    assert brand_audit.deny_reason("cookbook/x/README.md", stale_import, brand_audit.DOCS_DENY_RULES) == "stale-import"


# --- Regressions from the EGPT-0.13 review -----------------------------------
#
# Every one of these was a string the sweep rewrote that should have stayed frozen.
# The audit passed anyway, because a gate that greps for "agno" is blind to text that
# was rebranded *incorrectly*. These pin the invariants the rules now encode.


@pytest.mark.parametrize(
    "line",
    [
        'SET "AGNO_DIR=%REPO_ROOT%\\libs\\agno"',  # .bat
        '$AgnoDir = Join-Path $RepoRoot "libs\\agno"',  # .ps1
        'AGNO_DIR="${REPO_ROOT}/libs/agno"',  # .sh
    ],
)
def test_directory_name_keep_covers_both_separators(line):
    """The Windows scripts spell it `libs\\agno`. A POSIX-only rule let a sweep rewrite
    them to `libs\\egpt` -- a directory that does not exist -- with the gate still green.

    Asserted on the rule itself: the line as a whole may be categorised by whichever
    KEEP matches first (AGNO_DIR is also on it), so what matters is that the directory
    reference is covered at all."""
    directory_rules = [p for name, p in brand_audit.KEEP_RULES if name == "keep-directory-name"]
    assert any(p.search(line) for p in directory_rules), line

    hit = brand_audit.Hit("scripts/validate.bat", 12, line)
    assert (
        brand_audit.classify(hit, brand_audit.DOCS_DENY_RULES, brand_audit.DOCS_PRESTRIP, brand_audit.DOCS_KEEP_RULES)
        is not None
    )


@pytest.mark.parametrize(
    "line",
    [
        "`egpt connect` and `egpt status` default `--server-name` to `egpt`. It was `agno` before EGPT-0.12.",
        '# then delete the stale "agno" entry from the client\'s config, e.g.',
        "claude mcp remove --scope user agno",
        "Pass `--server-name agno` to keep writing under the old key instead",
    ],
)
def test_legacy_mcp_entry_name_stays_allowed(line):
    """egptctl still reads the `agno` MCP key. Rewriting these to `egpt` made the
    documented command delete the entry `egpt connect` had just written."""
    path = "libs/egptctl/README.md"
    assert brand_audit.deny_reason(path, line, brand_audit.DOCS_DENY_RULES, brand_audit.DOCS_PRESTRIP) is None
    assert (
        brand_audit.classify(
            brand_audit.Hit(path, 0, line),
            brand_audit.DOCS_DENY_RULES,
            brand_audit.DOCS_PRESTRIP,
            brand_audit.DOCS_KEEP_RULES,
        )
        is not None
    )


def test_provenance_source_row_is_allowed_but_only_there():
    """PROVENANCE.md must name the project this repo was forked from; no other file
    gets to carry an upstream clone URL."""
    row = "| Source project | https://github.com/agno-agi/agno |"
    assert brand_audit.deny_reason("PROVENANCE.md", row, brand_audit.DOCS_DENY_RULES, brand_audit.DOCS_PRESTRIP) is None
    assert (
        brand_audit.classify(
            brand_audit.Hit("PROVENANCE.md", 5, row),
            brand_audit.DOCS_DENY_RULES,
            brand_audit.DOCS_PRESTRIP,
            brand_audit.DOCS_KEEP_RULES,
        )
        == "keep-provenance"
    )
    # The same URL in a README is still a clone-url finding.
    assert (
        brand_audit.deny_reason(
            "README.md",
            "git clone https://github.com/agno-agi/agno.git",
            brand_audit.DOCS_DENY_RULES,
            brand_audit.DOCS_PRESTRIP,
        )
        == "clone-url"
    )


def test_agent_instruction_files_are_in_scope():
    """These tell a coding agent which repo to act on. Left unscanned, they pointed at
    agno-agi/agno, so `gh api ... -X PATCH` would have targeted the upstream repo."""
    for path in ("AGENTS.md", "CLAUDE.md", "PROVENANCE.md"):
        assert path in brand_audit.DOCS_PATHS


def test_captured_output_rule_is_docs_only():
    """It was added to the global KEEP list, which widened the shipped pass too --
    contrary to the stated design that docs rules never loosen the shipped gate."""
    names = [name for name, _ in brand_audit.KEEP_RULES]
    assert "keep-captured-output" not in names
    assert "keep-captured-output" in [name for name, _ in brand_audit.DOCS_KEEP_RULES]
