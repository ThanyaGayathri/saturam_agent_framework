"""Release smoke: verify first-party closure from GitHub Release assets.

Install from release assets only (never public PyPI for egpt*)::

    ./scripts/install_from_github_release.sh egpt
    python examples/hello_agent.py

Public PyPI has an unrelated ``egpt==0.0.1`` (tuu.ai). Our private version is
``0.1.0+``. This script validates brand identity, Agent construction, and that
egptctl + egpt-infra co-install without clobbering ``egpt``.
"""

from importlib.metadata import metadata

from egpt import PRODUCT_NAME, PRODUCT_OS_NAME, __version__
from egpt.agent import Agent
from egpt.brand import DEFAULT_OS_ID, ENV_PREFIX


def main() -> None:
    meta = metadata("egpt")
    summary = meta.get("Summary") or ""
    if "agentic" not in summary.lower():
        raise SystemExit(
            f"Installed egpt does not look like Saturam EGPT (Summary={summary!r}). "
            "Install from GitHub Release assets via "
            "./scripts/install_from_github_release.sh egpt"
        )
    if PRODUCT_NAME != "EGPT" or PRODUCT_OS_NAME != "EgptOS":
        raise SystemExit(f"Unexpected brand constants: {PRODUCT_NAME!r} / {PRODUCT_OS_NAME!r}")
    if ENV_PREFIX != "EGPT" or DEFAULT_OS_ID != "egpt-os":
        raise SystemExit(f"Unexpected brand IDs: {ENV_PREFIX!r} / {DEFAULT_OS_ID!r}")

    # First-party closure (installed by install_from_github_release.sh).
    import egptctl  # noqa: F401
    import egpt.docker  # noqa: F401
    import egpt.infra  # noqa: F401

    agent = Agent(
        name="hello-agent",
        instructions="You are a helpful assistant.",
        markdown=True,
    )
    print(f"{PRODUCT_NAME} {__version__}")
    print(f"Agent ready: {agent.name}")
    print("Smoke OK: Saturam egpt + egptctl + egpt-infra identity verified")


if __name__ == "__main__":
    main()
