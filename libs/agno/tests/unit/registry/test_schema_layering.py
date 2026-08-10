"""Guards on the layering the config validator is built around (EGPT-2.2).

`egpt/registry/schema.py` is core. It validates configs *for* agents, teams and workflows
without naming any of them: the component contract arrives as a `ComponentSpec` built by an
orchestration-owned factory. That is not a stylistic preference -- ADR 0001 makes
core -> orchestration a forbidden edge, and EGPT-1.11 will enforce it with import-linter.

The obvious shortcut, a function-local `from egpt.agent import Agent` inside the validator,
does not dodge that check: import-linter's graph is built from the AST and records imports
inside function bodies exactly like module-level ones. This guard fails on it today rather
than leaving it to be discovered when 1.11 lands.
"""

import json
import subprocess
import sys
import textwrap

import pytest

from tests.unit._layer_guards import imported_modules, package_root

# Layers the validator must not reach into, and why each one matters:
# - orchestration owns the component classes it validates (the whole point of ComponentSpec)
# - api sits above core entirely
# - integrations is what the discovery seam exists to keep out of core
_FORBIDDEN_PREFIXES = (
    "egpt.agent",
    "egpt.team",
    "egpt.workflow",
    "egpt.os",
    "egpt.integrations",
)


@pytest.mark.parametrize("module_name", ["schema.py", "policy.py"])
def test_the_validator_names_no_orchestration_module(module_name):
    """Both halves of the validator are core and must stay there.

    `policy.py` is the sharper case of the two: its whole job is to recognise toolkits that live
    in `egpt.integrations.tools.*`, and it does that by comparing module paths as *strings*. The
    table it compares against reads exactly like an import list, so this guard is what keeps
    someone from "tidying it up" into real imports.
    """
    path = package_root() / "egpt" / "registry" / module_name
    assert path.is_file(), f"validator not found: {path}"

    offenders = sorted(name for name in imported_modules(path) if name.startswith(_FORBIDDEN_PREFIXES))

    assert not offenders, (
        f"egpt/registry/{module_name} imports {offenders}. Core must not reach into orchestration "
        "or the api layer -- the component contract is injected as a ComponentSpec instead. "
        "A function-local import does not help: import-linter reads the AST, not the runtime."
    )


def test_importing_the_validator_pulls_in_no_vendor_sdk():
    """Importing the schema must stay as cheap as importing the seam it sits next to.

    A subprocess because under pytest `sys.modules` is already full: an in-process assertion
    would pass or fail on test ordering rather than on the property.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import json
                import sys

                import egpt.registry.schema  # noqa: F401

                sdks = ["anthropic", "boto3", "chromadb", "cohere", "google", "groq", "openai"]
                print(json.dumps(sorted(name for name in sdks if name in sys.modules)))
                """
            ),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, f"subprocess failed:\n{result.stdout}\n{result.stderr}"
    leaked = json.loads(result.stdout.strip().splitlines()[-1])
    assert leaked == [], (
        f"Importing egpt.registry.schema imported vendor SDKs: {leaked}. Reference checking "
        "must answer from the declaration table (discovery.get), never by loading a provider."
    )
