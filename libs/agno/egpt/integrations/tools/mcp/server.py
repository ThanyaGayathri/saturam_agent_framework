"""FastMCP server-side registration primitives (EGPT-1.7).

The MCP *client* in this package consumes external MCP servers; this module is the other
direction -- the adapter EgptOS uses to expose itself over MCP. It was extracted from
``egpt/os/mcp.py`` so the ``fastmcp`` import stops originating in the api layer (ADR 0001
rule 4, inventory A12). The control plane still owns the tool surface: it calls these from
``build_mcp_server()``, which is its composition root.

Nothing here imports from ``egpt.os``. The MCP config is read structurally -- any object
carrying ``enable_builtin_tools`` / ``include_tags`` / ``exclude_tags`` / ``tools`` works --
and the built-in tag set and the user-id injector are passed in by the caller, so
``MCP_BUILTIN_TAGS`` stays the single source of truth in ``egpt/os/config.py`` and the
request-scoped injector stays with the request layer that can read it.
"""

from typing import Any, Callable, Iterable, Optional

from fastmcp import FastMCP


def enabled_builtin_tags(config: Optional[Any], builtin_tags: Iterable[str]) -> set:
    """Resolve which built-in tool tags should be registered, given the MCP config.

    Returns the full set of built-in tags when no config is provided, preserving the
    default behavior (all built-in tools registered).
    """
    if config is None:
        return set(builtin_tags)
    if not config.enable_builtin_tools:
        return set()
    # An explicitly empty include_tags set means "no built-in tools", so test against
    # None rather than truthiness.
    enabled = set(config.include_tags) if config.include_tags is not None else set(builtin_tags)
    if config.exclude_tags:
        enabled -= set(config.exclude_tags)
    return enabled


def builtin_tool_registrar(mcp: FastMCP, config: Optional[Any], builtin_tags: Iterable[str]):
    """Return a drop-in replacement for ``mcp.tool`` that scopes the built-in tools.

    When a tool's tags are enabled by the config, the tool is registered as usual.
    Otherwise the decorator is a no-op (the function is returned unregistered), so
    scoping happens at registration time without depending on FastMCP tool-removal APIs.
    """
    enabled_tags = enabled_builtin_tags(config, builtin_tags)

    def register(*args: Any, **kwargs: Any):
        tags = kwargs.get("tags") or set()
        if tags & enabled_tags:
            return mcp.tool(*args, **kwargs)

        def _skip(fn: Any) -> Any:
            return fn

        return _skip

    return register


def register_custom_tools(
    mcp: FastMCP,
    config: Optional[Any],
    wrap: Optional[Callable[[Callable], Callable]] = None,
) -> None:
    """Register any user-provided custom tools on the MCP server."""
    if config is None or not config.tools:
        return
    for tool in config.tools:
        register_custom_tool(mcp, tool, wrap=wrap)


def register_custom_tool(
    mcp: FastMCP,
    tool: Any,
    wrap: Optional[Callable[[Callable], Callable]] = None,
) -> None:
    """Register a single custom tool, supporting plain callables and EGPT tools/Functions.

    ``wrap`` decorates the entrypoint before registration. The control plane passes its
    user-id injector here; the default is identity so this module stays usable without a
    request context.
    """
    from fastmcp.tools import Tool

    if wrap is None:

        def wrap(fn: Callable) -> Callable:
            return fn

    # EGPT tool / Function: a callable ``entrypoint`` plus name/description metadata.
    entrypoint = getattr(tool, "entrypoint", None)
    if callable(entrypoint):
        name = getattr(tool, "name", None) or getattr(entrypoint, "__name__", None)
        description = getattr(tool, "description", None)
        mcp.add_tool(Tool.from_function(wrap(entrypoint), name=name, description=description))
        return

    # Plain callable: name/description inferred from ``__name__``/docstring.
    if callable(tool):
        mcp.add_tool(Tool.from_function(wrap(tool)))
        return

    raise TypeError(
        f"Cannot register MCP tool of type {type(tool).__name__!r}; expected a callable or an EGPT tool/Function."
    )
