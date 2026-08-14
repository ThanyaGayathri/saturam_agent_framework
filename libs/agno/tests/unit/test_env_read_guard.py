"""EGPT-1.10: no new raw environment reads outside the branded-env chokepoint.

Scans every module under ``egpt/`` except ``egpt/integrations/`` (third-party SDK
adapters, whose whole job is reading names like ``OPENAI_API_KEY`` -- allowlisting
those ~300 sites would produce a table nobody maintains; see EGPT-1.10 decision 1)
for direct ``os.getenv`` / ``from os import getenv`` / ``os.environ`` access.

The allowlist below is a ratchet, not a permanent list: it is checked in both
directions. An access with no entry fails the guard ("route it through
egpt.settings, or add an entry with a reason"). An entry with no matching access
*also* fails ("the backlog shrank -- delete this entry"). Migrating a file out of
the allowlist is therefore a required part of the diff that migrates it, and the
allowlist's length is a readable backlog metric of what is left.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Set, Tuple

import pytest

from tests.unit._layer_guards import _parse, package_root

# --------------------------------------------------------------------------- #
# Detector
# --------------------------------------------------------------------------- #

_ENVIRON_MUTATORS = {"setdefault", "update"}
_ENVIRON_READERS = {"get"}


def _const_str(node: ast.expr) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def find_env_accesses(path: Path) -> Set[str]:
    """Every environment access `path` makes, as a set of markers.

    A plain string is the literal env var name (``"OS_SECURITY_KEY"``). A write is
    prefixed ``<write:...>``. A non-literal (caller-supplied) name is ``<dynamic>``
    or ``<write:<dynamic>>``. Capturing the whole environ mapping (e.g. for a
    subprocess call) is ``<environ>``.
    """
    tree = _parse(path)

    os_module_aliases: Set[str] = set()
    getenv_aliases: Set[str] = set()
    environ_aliases: Set[str] = set()
    star_import = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "os":
                    os_module_aliases.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "*":
                    star_import = True
                elif alias.name in ("getenv", "getenvb"):
                    getenv_aliases.add(alias.asname or alias.name)
                elif alias.name == "environ":
                    environ_aliases.add(alias.asname or alias.name)

    if star_import:
        # `from os import *` binds getenv/environ under their own names; a module
        # doing this and shadowing them differently is not a pattern in this tree.
        getenv_aliases.update({"getenv", "getenvb"})
        environ_aliases.add("environ")

    if not (os_module_aliases or getenv_aliases or environ_aliases):
        return set()  # fast path: module never imports anything os-env-shaped

    def is_environ_expr(node: ast.expr) -> bool:
        if isinstance(node, ast.Name) and node.id in environ_aliases:
            return True
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id in os_module_aliases
        )

    def is_os_attr_call(func: ast.expr, names: Set[str]) -> bool:
        return (
            isinstance(func, ast.Attribute)
            and func.attr in names
            and isinstance(func.value, ast.Name)
            and func.value.id in os_module_aliases
        )

    # Subscripts assigned to (`environ["X"] = v`) or deleted (`del environ["X"]`) are
    # writes; every other use is a read.
    write_subscript_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        else:
            targets = []
        for t in targets:
            if isinstance(t, ast.Subscript) and is_environ_expr(t.value):
                write_subscript_ids.add(id(t))

    def arg0_name(call: ast.Call) -> str:
        if call.args:
            return _const_str(call.args[0]) or "<dynamic>"
        return "<dynamic>"

    found: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id in getenv_aliases) or is_os_attr_call(
                func, {"getenv", "getenvb"}
            ):
                found.add(arg0_name(node))
            elif is_os_attr_call(func, {"putenv", "unsetenv"}):
                found.add(f"<write:{arg0_name(node)}>")
            elif isinstance(func, ast.Attribute) and is_environ_expr(func.value):
                if func.attr in _ENVIRON_READERS:
                    found.add(arg0_name(node))
                elif func.attr == "pop":
                    found.add(f"<write:{arg0_name(node)}>")
                elif func.attr in _ENVIRON_MUTATORS:
                    found.add(f"<write:{arg0_name(node)}>")
                elif func.attr == "copy":
                    found.add("<environ>")
        elif isinstance(node, ast.Subscript) and is_environ_expr(node.value):
            name = _const_str(node.slice) or "<dynamic>"
            found.add(f"<write:{name}>" if id(node) in write_subscript_ids else name)
        elif isinstance(node, ast.Expr) and is_environ_expr(node.value):
            found.add("<environ>")  # bare `os.environ` statement (e.g. for inspection)

    return found


def _scan(root: Path, exclude_dirs: FrozenSet[str]) -> Tuple[int, Set[Tuple[str, str]]]:
    checked = 0
    found: Set[Tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in exclude_dirs:
            continue
        checked += 1
        for name in find_env_accesses(path):
            found.add((rel.as_posix(), name))
    return checked, found


# --------------------------------------------------------------------------- #
# Allowlist -- an exact inventory of what is not yet migrated, and why.
# --------------------------------------------------------------------------- #

ALLOWED_ENV_ACCESS: Dict[Tuple[str, str], str] = {
    # The branded-env chokepoint itself. get_from_env's `key` argument is caller-supplied
    # (a legacy, non-branded escape hatch), so it is `<dynamic>` to the detector.
    ("utils/env.py", "<dynamic>"): "the deprecated get_from_env escape hatch reads an arbitrary caller-supplied key",
    # EGPT-1.7a moved the context connectors' provider.py files (gmail, gdrive, calendar,
    # slack, web, wiki's git backend) into egpt/integrations/context/*, which this scan
    # already excludes wholesale -- their raw getenv() calls no longer need an entry here,
    # the same way every other integrations-layer SDK credential read does not.
    # -- third-party provider credentials: SDK/provider convention owns the name, per
    # -- EGPT-1.10 decision 1. Not migrated this PR.
    ("knowledge/reader/reader_factory.py", "FIRECRAWL_API_KEY"): "Firecrawl API key",
    ("knowledge/reader/reader_factory.py", "TAVILY_API_KEY"): "Tavily API key",
    ("cloud/aws/base.py", "AWS_REGION"): "AWS credential/region, standard AWS SDK env var",
    ("cloud/aws/base.py", "AWS_PROFILE"): "AWS credential profile, standard AWS SDK env var",
    ("guardrails/openai.py", "OPENAI_API_KEY"): "OpenAI API key, standard OpenAI SDK env var",
    ("agents/antigravity/agent.py", "GEMINI_API_KEY"): "Gemini API key, standard Google SDK env var",
    ("tools/streamlit/components.py", "<dynamic>"): "caller-supplied env var name (password_env_var parameter)",
    ("tools/streamlit/components.py", "OPENAI_API_KEY"): "OpenAI API key, standard OpenAI SDK env var",
    ("tools/streamlit/components.py", "<write:OPENAI_API_KEY>"): "seeds OPENAI_API_KEY from a Streamlit UI prompt",
    # -- outbound provider credentials on the chat interfaces: rotated in the provider's
    # -- own console (BotFather, Meta), not "ours" in the sense EGPT-1.10 decision 1 draws
    # -- the line -- see the inbound-vs-outbound split in the plan.
    ("os/interfaces/telegram/router.py", "TELEGRAM_TOKEN"): "outbound Telegram bot token, rotated in BotFather",
    ("os/interfaces/whatsapp/helpers.py", "WHATSAPP_ACCESS_TOKEN"): "outbound Meta Cloud API token",
    ("os/interfaces/whatsapp/helpers.py", "WHATSAPP_PHONE_NUMBER_ID"): "Meta Cloud API phone number id",
    ("os/interfaces/whatsapp/whatsapp.py", "WHATSAPP_ENCRYPTION_KEY"): "data-at-rest key, not a serving-auth setting",
    # -- deliberately raw, not a migration gap --
    # egpt/integrations/context/wiki/git_ops.py's os.environ.copy() (EGPT-1.7a moved it there)
    # needs no entry here for the same reason: the integrations/ exclusion already covers it.
    (
        "os/interfaces/telegram/security.py",
        "APP_ENV",
    ): "deprecated dev-mode bypass predating EGPT-1.10; read raw so it can carry its "
    "own FutureWarning pointing at EGPT_TELEGRAM_SKIP_SECRET_VALIDATION -- tracked for removal",
}

_EXCLUDE_DIRS: FrozenSet[str] = frozenset({"integrations"})


def _egpt_root() -> Path:
    return package_root() / "egpt"


def test_no_unaccounted_raw_env_reads_outside_integrations():
    checked_files, found = _scan(_egpt_root(), _EXCLUDE_DIRS)

    # Non-vacuity floors: a scan root that goes stale (renamed/emptied) or a detector
    # that silently stops matching would otherwise report zero offenders and PASS.
    # Lowered from 20 by EGPT-1.7a: moving the eight context connectors into
    # egpt/integrations/context/* removed 14 outside-integrations reads at once (they were
    # never a detector gap -- the scan already excludes egpt/integrations/ wholesale).
    assert checked_files > 900, f"only {checked_files} files scanned; the scan root is probably stale"
    assert len(found) >= 10, f"only {len(found)} env accesses detected; the detector is probably broken"

    unexpected = sorted(found - set(ALLOWED_ENV_ACCESS))
    stale = sorted(set(ALLOWED_ENV_ACCESS) - found)

    assert not unexpected, (
        "raw environment read(s) outside egpt/integrations/ with no recorded decision:\n"
        + "\n".join(f"  {path}: {name}" for path, name in unexpected)
        + "\nRoute them through egpt.settings (resolve/resolve_bool/require), or add an "
        "ALLOWED_ENV_ACCESS entry with a reason."
    )
    assert not stale, (
        "ALLOWED_ENV_ACCESS entries with no matching read -- the backlog shrank, delete these:\n"
        + "\n".join(f"  {path}: {name}" for path, name in stale)
    )


def test_settings_sources_stays_a_leaf():
    """egpt.settings.sources must import nothing from egpt.settings, egpt.os.*, or egpt.api.*.

    egpt/os/settings.py and egpt/api/settings.py both import egpt.settings.sources; if
    sources.py imported back from either side (or from the egpt.settings package that
    composes them), that would be a cycle.
    """
    path = _egpt_root() / "settings" / "sources.py"
    assert path.is_file(), f"expected {path} to exist"

    banned_prefixes = ("egpt.settings", "egpt.os.", "egpt.api.")
    offenders = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "egpt.settings" or any(node.module.startswith(p) for p in banned_prefixes):
                offenders.append(f"{path.name}:{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == p.rstrip(".") or alias.name.startswith(p) for p in banned_prefixes):
                    offenders.append(f"{path.name}:{node.lineno}: import {alias.name}")

    assert not offenders, "egpt.settings.sources must stay a leaf module:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------- #
# Mutation verification: the detector itself, on synthetic sources.
# --------------------------------------------------------------------------- #


def _detect(tmp_path: Path, src: str) -> Set[str]:
    path = tmp_path / "mod.py"
    path.write_text(src)
    return find_env_accesses(path)


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("import os\nx = os.getenv('A')\n", {"A"}),
        ("from os import getenv\nx = getenv('A')\n", {"A"}),  # the majority form in this tree
        ("from os import getenv as g\nx = g('A')\n", {"A"}),  # aliased import
        ("import os as _o\nx = _o.getenv('A')\n", {"A"}),  # aliased module
        ("import os.path\nx = os.getenv('A')\n", {"A"}),  # submodule import still binds `os`
        ("from os import environ\nx = environ['A']\n", {"A"}),
        ("from os import environ as e\nx = e.get('A')\n", {"A"}),
        ("import os\nos.environ['A'] = 'v'\n", {"<write:A>"}),
        ("import os\ndel os.environ['A']\n", {"<write:A>"}),
        ("import os\nos.environ.pop('A')\n", {"<write:A>"}),
        ("import os\nos.environ.setdefault('A', 'v')\n", {"<write:A>"}),
        ("import os\nx = os.environ.copy()\n", {"<environ>"}),
        ("import os\nos.putenv('A', 'v')\n", {"<write:A>"}),
        ("import os\nNAME = 'A'\nx = os.getenv(NAME)\n", {"<dynamic>"}),
        (
            "def f():\n    from os import getenv\n    return getenv('A')\n",
            {"A"},
        ),  # function-local import (cloud/aws/base.py's actual shape)
        ("from mymod import getenv\nx = getenv('A')\n", set()),  # must NOT fire on an unrelated getenv
        ("import os\nx = 1\n", set()),  # os imported but unused for env access
        ("x = 1\n", set()),  # no os import at all
    ],
)
def test_detector_catches_every_import_form(tmp_path: Path, src: str, expected: Set[str]):
    assert _detect(tmp_path, src) == expected
