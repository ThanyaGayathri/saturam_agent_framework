"""Built-in toolkit declarations.

One row per stable toolkit name:

    name -> (module, class_name)

This table is deliberately *not* a mirror of the ~130 toolkits in the integrations layer. A
row exists only for a toolkit an inward layer must construct by name -- the Slack context
provider builds ``SlackTools`` with a per-agent capability set that no composition root can
pre-instantiate, and (EGPT-1.7a) the filesystem context provider stays in orchestration
(ADR 0001) while ``FileTools`` is a concrete integration. Everything else is imported
directly by the caller that wants it (cookbooks, the control plane, user code) and needs no
seam.

The sparseness is a contract, not a gap. A row per toolkit would be a drift surface per
toolkit with no consumer, and the "every row resolves" guard would need all ~130 optional
SDKs installed to be worth anything.
"""

from egpt.registry.discovery import SOURCE_BUILTIN, register

_ROWS = {
    "file": ("egpt.integrations.tools.file", "FileTools"),
    "slack": ("egpt.integrations.tools.slack", "SlackTools"),
}


def register_all() -> None:
    """Register every row. Called by the discovery seam, and safe to call again."""
    for name, (module, class_name) in _ROWS.items():
        register("tool", name, module=module, attr=class_name, source=SOURCE_BUILTIN, override=True)
