"""`egpt status`: orientation — what OS is running, how it is secured, what is connected."""

from typing import Optional

import typer

from egptctl import CLI_NAME
from egptctl.clients import build_adapters
from egptctl.clients.base import DEFAULT_MCP_SERVER_NAME, LEGACY_MCP_SERVER_NAME
from egptctl.commands._common import handle_cli_error
from egptctl.console import emit_json, print_info, print_success, print_warning
from egptctl.discovery import discover
from egptctl.errors import CLIError


def status(
    url: Optional[str] = typer.Option(None, "--url", help="EgptOS base URL (default: autodiscover on localhost)."),
    server_name: str = typer.Option(
        DEFAULT_MCP_SERVER_NAME, "--server-name", help="MCP server entry name to look for in clients."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit a single JSON document for machine consumption."),
) -> None:
    """Show the discovered EgptOS and which coding agents are connected to it."""
    try:
        os_info = discover(url)
    except CLIError as e:
        raise handle_cli_error(e, json_output)

    # EGPT-0.12 moved the default entry name from "agno" to "egpt" as a clean break, so an
    # entry written by an older version is left in place rather than migrated. Nothing else
    # tells the operator it is still there, pointing at whatever OS it was written for.
    # Asking for the old name explicitly means it is the wanted entry, not a leftover.
    check_legacy = server_name != LEGACY_MCP_SERVER_NAME

    adapters = build_adapters()
    clients = []
    for adapter in adapters.values():
        detected = adapter.detect()
        entry = adapter.read_existing(server_name) if detected else None
        legacy = adapter.read_existing(LEGACY_MCP_SERVER_NAME) if detected and check_legacy else None
        clients.append(
            {
                "client": adapter.key,
                "detected": detected,
                "configured": entry is not None,
                "location": entry.location if entry else None,
                "legacy_entry": legacy.location if legacy else None,
            }
        )

    if json_output:
        emit_json({"os": os_info.public_dict(), "clients": clients})
        return

    version = " (egpt " + os_info.version + ")" if os_info.version else ""
    print_success("EgptOS: " + os_info.base_url + version)
    mcp = os_info.mcp_url if os_info.mcp_enabled else "disabled"
    print_info("  MCP: " + mcp)
    print_info("  Auth mode: " + os_info.auth_mode)
    print_info("")
    for c in clients:
        if not c["detected"]:
            print_info("  " + str(c["client"]) + ": not detected")
        elif c["configured"]:
            print_info("  " + str(c["client"]) + ": configured (" + str(c["location"]) + ")")
        else:
            print_info("  " + str(c["client"]) + ": detected, not connected (run: " + CLI_NAME + " connect)")

    stale = [c for c in clients if c["legacy_entry"]]
    if stale:
        print_info("")
        print_warning('  An "' + LEGACY_MCP_SERVER_NAME + '" MCP entry from before the rename is still present:')
        for c in stale:
            print_warning("    " + str(c["client"]) + ": " + str(c["legacy_entry"]))
        print_warning("  It is not used and not removed automatically. Delete it from each client, e.g.:")
        print_warning("    claude mcp remove --scope user " + LEGACY_MCP_SERVER_NAME)
