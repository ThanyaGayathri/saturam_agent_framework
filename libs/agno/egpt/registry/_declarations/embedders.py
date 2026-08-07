"""Built-in embedder declarations.

One row per embedder:

    key -> (module, class_name)

Deliberately narrow. Only the framework default is declared, because it is the only
embedder core constructs by name: everything else is instantiated by the caller that wants
it (cookbooks, the control plane, user code) and needs no seam. This mirrors the tools
table's sparseness rather than the models table's completeness.

Known open debt, recorded rather than silently deferred: ADR 0001's extraction prerequisite
6 wants *every* embedder reachable by name so memory Mode A <-> B stays a config swap
(SM-R3/SM-R4). That needs the other 17 rows and a caller that resolves by string. Each row
is one line; the work is deciding the keys, not writing them.
"""

from egpt.registry.discovery import SOURCE_BUILTIN, register

_ROWS = {
    "openai": ("egpt.integrations.knowledge.embedder.openai", "OpenAIEmbedder"),
}


def register_all() -> None:
    """Register every row. Called by the discovery seam, and safe to call again."""
    for key, (module, class_name) in _ROWS.items():
        register("embedder", key, module=module, attr=class_name, source=SOURCE_BUILTIN, override=True)
