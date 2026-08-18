"""The import-linter contracts hold with zero known debt (EGPT-1.11, aicoe-tracker#52).

This file used to be the known-debt ledger: `libs/agno/pyproject.toml`'s `[tool.importlinter]`
contracts carry no `ignore_imports` for layering debt (a stakeholder comment on
aicoe-tracker#52 explicitly rejected masking), so while debt existed, this test pinned the
exact violating edges -- failing on any NEW edge, and failing when a KNOWN edge was cleared so
its entry got deleted. The sets emptied in three waves:

  - A8 (aicoe-tracker#157, EGPT-1.7a): context connectors moved to `egpt.integrations.context.*`,
    guarded by `tests/unit/context/test_context_layout.py`.
  - A13 (aicoe-tracker#158, EGPT-1.7b): channel adapters moved to `egpt.integrations.channels.*`,
    guarded by `tests/unit/os/interfaces/test_channels_layout.py`.
  - EGPT-1.7c (aicoe-tracker#180): the toolkit/model-provider modules were retyped against
    `egpt.registry.protocols` (`ToolHandleLike`/`ChatRunnableLike`/`SessionAgentLike`), the
    guardrails image helpers moved to core (`egpt.utils.media`), and the memory-substrate
    family was resolved by the seam decision recorded in ADR 0001: `egpt.memory` left the
    "integrations must not import ..." contract in favor of the module-precise
    `tests/unit/memory/test_memory_layout.py` guard, `MemoryProviderConfigError` moved to
    `egpt.memory.errors`, and `ProviderMode` moved to core (`egpt.metrics`).

What remains is the terminal guard the original docstring promised: the contracts must report
**zero** violations, which is also what lets the `lint-imports` CI job run as a required check
(see .github/workflows/ci.yml). If this test fails, fix the import -- type against a protocol
in `egpt.registry.protocols`, route construction through `egpt.run.utils.construct_runnable` /
`egpt.tools.utils.construct_toolkit` / Registry, or hand the value in through a port's method
arguments -- rather than reintroducing a tracked-debt list.
"""

from importlinter import configuration
from importlinter.application import use_cases

from tests.unit._layer_guards import package_root


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


def test_no_known_violations_remain():
    actual = _actual_violations()

    assert not actual, (
        "New import-linter violation(s) of the integrations boundary:\n"
        + "\n".join(f"  {importer} -> {imported}" for importer, imported in sorted(actual))
        + "\nResolve the import (type against a protocol in egpt.registry.protocols, route "
        "construction through egpt.run.utils.construct_runnable / "
        "egpt.tools.utils.construct_toolkit / Registry, or hand the value in through a port's "
        "method arguments) -- the known-debt ledger this file used to hold is retired, and "
        "these contracts are expected to stay clean."
    )
