"""Tracks the known integrations-boundary import-linter debt (EGPT-1.11, aicoe-tracker#52).

`libs/agno/pyproject.toml`'s `[tool.importlinter]` contracts encode the integrations column of
ADR 0001's allowed-import matrix (core/orchestration/services/api <-> integrations) with no
`ignore_imports` for the violations below -- `lint-imports` genuinely fails against `main` today.
That is deliberate: a stakeholder comment on aicoe-tracker#52 explicitly rejected using
`ignore_imports` to mask this debt, since that would let 1.11 "complete" while writing the
violations permanently into the gate that exists to catch them.

Instead, this test tracks the exact known set of violating edges, grouped by the ticket that
owns clearing them. It fails loudly in two directions:
  - a NEW edge appears that isn't in the known set -- catches regressions immediately, as part
    of the required unit-test job (unlike the raw `lint-imports` CI job, which is intentionally
    non-blocking until this set is empty -- see .github/workflows/ci.yml).
  - a KNOWN edge disappears -- prompts deleting that entry here when the owning ticket lands,
    the same floor-count discipline `test_toolkit_layout.py`/`test_knowledge_layout.py` already
    use for their `_INTEGRATION_IMPORT_ALLOWLIST`.

Once this set is empty, the `lint-imports` CI job should be promoted to a required check and
this file can be deleted (or reduced to a `test_no_known_violations_remain` guard).
"""

from importlinter import configuration
from importlinter.application import use_cases

from tests.unit._layer_guards import package_root

# A8 (aicoe-tracker#157, EGPT-1.7a) is CLEARED: the context connectors moved to
# egpt.integrations.context.*, and libs/agno/tests/unit/context/test_context_layout.py now
# guards that boundary directly (including integrations.context.* -> egpt.agent/team/workflow,
# via the RunnableLike protocol + construct_runnable seam that PR introduced). egpt.context is
# excluded from this file's contracts' module lists for the same reason egpt.db/models/etc. are
# -- see the scope note in pyproject.toml's [tool.importlinter] section.

# A13 (aicoe-tracker#158, EGPT-1.7b) is CLEARED: the Slack channel adapter moved to
# egpt.integrations.channels.slack.*, and libs/agno/tests/unit/os/interfaces/test_channels_layout.py
# now guards that boundary directly (shim parity + integrations.channels.* -> egpt.agent/team/
# workflow, via the AsyncRunnableLike protocol this ticket introduced).

# Newly found while implementing EGPT-1.11 -- not owned by any existing ticket. Filed as
# aicoe-tracker#180 (EGPT-1.7c: integrations reference orchestration types directly): ~20
# toolkit/model-provider integrations import egpt.agent/egpt.team at module level (mostly
# for callback-style human-in-the-loop parameters), which is the same class of problem #157/#158
# already flag for context connectors and the Slack adapter -- integrations that construct or
# reference orchestration types can't relocate cleanly until the sub-agent seam those two
# tickets leave "OPEN" is decided.
_NEW_INTEGRATIONS_REFERENCE_ORCHESTRATION = {
    ("egpt.integrations._utils.mcp", "egpt.agent"),
    ("egpt.integrations._utils.mcp", "egpt.team.team"),
    ("egpt.integrations._utils.streamlit", "egpt.agent"),
    ("egpt.integrations.discord.client", "egpt.agent.agent"),
    ("egpt.integrations.discord.client", "egpt.team.team"),
    ("egpt.integrations.models._utils.llama", "egpt.agent"),
    ("egpt.integrations.models.ollama.chat", "egpt.agent"),
    ("egpt.integrations.tools.antigravity.antigravity", "egpt.agent"),
    ("egpt.integrations.tools.antigravity.antigravity", "egpt.team"),
    ("egpt.integrations.tools.brightdata.brightdata", "egpt.agent"),
    ("egpt.integrations.tools.cartesia.cartesia", "egpt.agent"),
    ("egpt.integrations.tools.cartesia.cartesia", "egpt.team.team"),
    ("egpt.integrations.tools.daytona.daytona", "egpt.agent"),
    ("egpt.integrations.tools.daytona.daytona", "egpt.team"),
    ("egpt.integrations.tools.desi_vocal.desi_vocal", "egpt.agent"),
    ("egpt.integrations.tools.desi_vocal.desi_vocal", "egpt.team.team"),
    ("egpt.integrations.tools.e2b.e2b", "egpt.agent"),
    ("egpt.integrations.tools.e2b.e2b", "egpt.team.team"),
    ("egpt.integrations.tools.eleven_labs.eleven_labs", "egpt.agent"),
    ("egpt.integrations.tools.eleven_labs.eleven_labs", "egpt.team.team"),
    ("egpt.integrations.tools.fal.fal", "egpt.agent"),
    ("egpt.integrations.tools.fal.fal", "egpt.team.team"),
    ("egpt.integrations.tools.giphy.giphy", "egpt.agent"),
    ("egpt.integrations.tools.giphy.giphy", "egpt.team.team"),
    ("egpt.integrations.tools.lumalab.lumalab", "egpt.agent"),
    ("egpt.integrations.tools.mcp.mcp", "egpt.agent"),
    ("egpt.integrations.tools.mcp.mcp", "egpt.team.team"),
    ("egpt.integrations.tools.mcp.multi_mcp", "egpt.agent"),
    ("egpt.integrations.tools.mcp.multi_mcp", "egpt.team.team"),
    ("egpt.integrations.tools.models.azure_openai", "egpt.agent"),
    ("egpt.integrations.tools.models.gemini", "egpt.agent"),
    ("egpt.integrations.tools.models.groq", "egpt.agent"),
    ("egpt.integrations.tools.models.nebius", "egpt.agent"),
    ("egpt.integrations.tools.openai.openai", "egpt.agent"),
    ("egpt.integrations.tools.openai.openai", "egpt.team.team"),
    ("egpt.integrations.tools.opencv.opencv", "egpt.agent"),
    ("egpt.integrations.tools.replicate.replicate", "egpt.agent"),
    ("egpt.integrations.tools.replicate.replicate", "egpt.team.team"),
}

# Newly found while implementing EGPT-1.11 -- also not owned by any existing ticket, filed under
# the same follow-up as above. The guard's own comment already flags this as ADR 0001 debt: it
# uses a function-local import specifically because a module-level one would be an even more
# obvious violation, but import-linter's graph records function-local imports exactly like
# module-level ones (see the docstring on tests/unit/registry/test_schema_layering.py).
_NEW_SERVICES_REFERENCE_INTEGRATIONS = {
    ("egpt.guardrails.openai", "egpt.integrations.models._utils.openai"),
}

# Landed on main (feat/SM-G1, the neo4j graph-memory substrate) after aicoe-tracker#180 was
# filed, while this PR was in flight -- same class of problem (an integrations-layer module
# needs a higher-layer module's domain types/config), just integrations -> services instead of
# integrations -> orchestration. Tracked here rather than filing a fourth ticket; noted on #180
# as an additional instance of the same seam decision.
_NEW_INTEGRATIONS_REFERENCE_SERVICES = {
    ("egpt.integrations.memory.graph.neo4j.cypher", "egpt.memory.types"),
    ("egpt.integrations.memory.graph.neo4j.neo4j", "egpt.memory.config"),
    ("egpt.integrations.memory.graph.neo4j.neo4j", "egpt.memory.tiers"),
    ("egpt.integrations.memory.graph.neo4j.neo4j", "egpt.memory.types"),
}

_KNOWN_VIOLATIONS = (
    _NEW_INTEGRATIONS_REFERENCE_ORCHESTRATION
    | _NEW_SERVICES_REFERENCE_INTEGRATIONS
    | _NEW_INTEGRATIONS_REFERENCE_SERVICES
)


def _actual_violations():
    """The exact set of (importer, imported) edges the import-linter contracts currently flag.

    Uses import-linter's Python API rather than parsing `lint-imports` text output, which is
    not meant to be machine-readable and reflows unpredictably at typical terminal widths.
    `allow_indirect_imports = true` on both contracts (see pyproject.toml) means every reported
    chain is exactly one hop, so `chain[0]` is the whole edge.
    """
    config_path = package_root() / "pyproject.toml"
    configuration.configure()
    user_options = use_cases.read_user_options(config_filename=str(config_path))
    use_cases._register_contract_types(user_options)
    report = use_cases.create_report(user_options, cache_dir=None)

    violations = set()
    for _contract, check in report.get_contracts_and_checks():
        for chain_data in check.metadata.get("invalid_chains", []):
            for chain in chain_data["chains"]:
                assert len(chain) == 1, (
                    f"expected a direct edge (allow_indirect_imports=true), got a multi-hop chain: {chain}"
                )
                hop = chain[0]
                violations.add((hop["importer"], hop["imported"]))
    return violations


def test_integrations_boundary_violations_match_the_known_set():
    actual = _actual_violations()

    new_violations = actual - _KNOWN_VIOLATIONS
    assert not new_violations, (
        "New import-linter violation(s) of the integrations boundary, not in the known-debt "
        "list tracked by this test:\n"
        + "\n".join(f"  {importer} -> {imported}" for importer, imported in sorted(new_violations))
        + "\nResolve the import (route it through egpt.tools.utils.construct_toolkit, "
        "Registry, or an equivalent seam) rather than adding it here -- this list is a tracked "
        "debt ceiling, not an allowlist to extend."
    )

    resolved_violations = _KNOWN_VIOLATIONS - actual
    assert not resolved_violations, (
        "Known import-linter violation(s) no longer reproduce -- remove them from this test's "
        "known-debt sets (and, if every entry in a ticket's set is now empty, close/update the "
        "corresponding aicoe-tracker issue):\n"
        + "\n".join(f"  {importer} -> {imported}" for importer, imported in sorted(resolved_violations))
    )
