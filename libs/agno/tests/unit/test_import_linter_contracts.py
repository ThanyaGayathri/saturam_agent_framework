"""Tracks the known integrations-boundary import-linter debt (EGPT-1.11, aicoe-tracker#52).

`libs/agno/pyproject.toml`'s `[tool.importlinter]` contracts encode the integrations column of
ADR 0001's allowed-import matrix (core/orchestration/services/api <-> integrations) with no
`ignore_imports` for the violations below -- `lint-imports` genuinely fails against `main` today.
That is deliberate: a stakeholder comment on aicoe-tracker#52 explicitly rejected using
`ignore_imports` to mask this debt, since that would let 1.11 "complete" while writing the
violations permanently into the gate that exists to catch them.

Instead, this test tracks the exact known set of violating edges, grouped by the ticket that
owns clearing them. After EGPT-1.7c (aicoe-tracker#180, first phase) cleared the toolkit/
model-provider and guardrails sets, the only remaining family is the memory-substrate one
below -- deliberately deferred until the in-flight SM epic settles, so the seam decision is
made once rather than re-litigated under six open PRs. It fails loudly in two directions:
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

# The EGPT-1.7c toolkit/model-provider set (aicoe-tracker#180) is CLEARED: the ~24 modules that
# imported egpt.agent/egpt.team directly now type their framework-injected handles against the
# structural protocols that ticket added to egpt.registry.protocols (ToolHandleLike,
# ChatRunnableLike, SessionAgentLike), and the two modules that only wanted re-exported value
# types (Message, RunOutput) import them from their canonical core homes
# (egpt.models.message, egpt.run.agent). Injection is by parameter name
# (egpt/tools/function.py), so the annotations were free to become structural.

# The EGPT-1.7c guardrails edge (also aicoe-tracker#180) is CLEARED: the OpenAI image
# wire-format helpers (process_image/images_to_message and their _process_* closure) moved from
# egpt.integrations.models._utils.openai to egpt.utils.media -- core, next to
# resolve_image_mime_type, which they already depended on -- so egpt.guardrails.openai now
# imports them from core at module level (its old function-local import was recorded by
# import-linter's graph exactly like a module-level one). The integrations module re-imports
# them, keeping the provider adapters' and shims' import paths working as integrations -> core.

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
    # The vector half of the same seam (feat/SM-V1, aicoe-tracker#95), landing after this file
    # did and for the identical reason: a `VectorSubstrate` backend is addressed through
    # `egpt.memory`'s ports, so it needs that package's envelope types, its tier policy and its
    # config error to implement them at all. One decision, two backends -- listing only the graph
    # one would make the debt look narrower than it is.
    ("egpt.integrations.memory.vector.pgvector.pgvector", "egpt.memory.config"),
    ("egpt.integrations.memory.vector.pgvector.pgvector", "egpt.memory.tiers"),
    ("egpt.integrations.memory.vector.pgvector.pgvector", "egpt.memory.types"),
    ("egpt.integrations.memory.vector.pgvector.sql", "egpt.memory.types"),
    # SM-G6 (aicoe-tracker#104), the episodic subgraph: same decision again, one module further
    # in. `egpt.memory.graph.episodic` holds the rules a `MENTIONS` edge has to satisfy -- the
    # entity node id, the mention order, and what a recall may filter on -- and they live there
    # precisely so every graph backend applies one copy. A backend that reimplemented them to
    # stay inside the boundary would be the drift the shared module exists to prevent, so this is
    # the same debt as the four rows above rather than a new one.
    ("egpt.integrations.memory.graph.neo4j.cypher", "egpt.memory.graph.episodic"),
    ("egpt.integrations.memory.graph.neo4j.neo4j", "egpt.memory.graph.episodic"),
    # SM-V2 (aicoe-tracker#96) adds two more edges out of the *same* module, for the same reason
    # the four above exist, and they are listed rather than routed away because the alternative is
    # worse in both cases:
    #
    # `egpt.memory.embedding` is the memory seam's own module -- it decides which embedder a
    # deployment mode gets, and the substrate implements that seam. It sits beside `memory.config`
    # / `memory.tiers` / `memory.types` in every respect; routing it through a fifth indirection
    # would move the edge, not remove it.
    #
    # `egpt.providers.model_provider` supplies `ProviderMode`, which is the mode vocabulary
    # `memory.config` deliberately reuses instead of defining a second `DeploymentMode` (ADR 0003
    # section B.5, and the rationale is written out in `memory/config.py`'s docstring). The
    # boundary-shaped dodge is to re-export `ProviderMode` from a module the substrate already
    # imports -- which satisfies the linter, keeps the identical runtime dependency, and hides it
    # from exactly the list that exists to show it. Both belong to the seam decision #180 tracks.
    ("egpt.integrations.memory.vector.pgvector.pgvector", "egpt.memory.embedding"),
    ("egpt.integrations.memory.vector.pgvector.pgvector", "egpt.providers.model_provider"),
}

_KNOWN_VIOLATIONS = _NEW_INTEGRATIONS_REFERENCE_SERVICES


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
        + "\nResolve the import (type against a protocol in egpt.registry.protocols, or route "
        "construction through egpt.run.utils.construct_runnable / "
        "egpt.tools.utils.construct_toolkit / Registry) rather than adding it here -- this "
        "list is a tracked debt ceiling, not an allowlist to extend."
    )

    resolved_violations = _KNOWN_VIOLATIONS - actual
    assert not resolved_violations, (
        "Known import-linter violation(s) no longer reproduce -- remove them from this test's "
        "known-debt sets (and, if every entry in a ticket's set is now empty, close/update the "
        "corresponding aicoe-tracker issue):\n"
        + "\n".join(f"  {importer} -> {imported}" for importer, imported in sorted(resolved_violations))
    )
