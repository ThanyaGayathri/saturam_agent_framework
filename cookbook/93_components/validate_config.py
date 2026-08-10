"""
Validating a Component Config
=============================

Every config written through the components API is validated before it is persisted: an
agent definition must name a resolvable model, may only use fields the agent understands,
and has to actually hydrate. This example calls the same validator the API calls, so you can
see the field-level errors without running a server.

Run:
    .venvs/demo/bin/python cookbook/93_components/validate_config.py
"""

from egpt.integrations.models.openai import OpenAIResponses
from egpt.integrations.tools.duckduckgo import DuckDuckGoTools
from egpt.os.routers.components.validation import validate_component_config
from egpt.registry import Registry
from egpt.registry.schema import ConfigValidationError

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# Tools, knowledge and schema classes cannot be serialized, so a config references them by
# name and the Registry supplies the live objects. Reference checks are skipped entirely when
# there is no registry -- an unknown name and an unconfigured process look the same.
registry = Registry(
    name="Cookbook Registry",
    tools=[DuckDuckGoTools()],
    models=[OpenAIResponses(id="gpt-5.5")],
)


# ---------------------------------------------------------------------------
# Create a Valid Agent Definition
# ---------------------------------------------------------------------------
VALID_CONFIG = {
    "model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"},
    "name": "Research Agent",
    "description": "Searches the web and summarizes what it finds.",
    "instructions": ["Cite every source.", "Say so when you are unsure."],
    "tools": [{"name": "web_search"}],
    "markdown": True,
}


def show(title: str, config: dict) -> None:
    """Validate a config and print either the stored result or every field-level error."""
    print(f"\n{title}")
    print("-" * len(title))
    try:
        stored = validate_component_config(config, "agent", registry=registry)
    except ConfigValidationError as error:
        for item in error.errors:
            print(f"  {item.path:<24} [{item.code}] {item.message}")
        return
    print(f"  accepted; stored as schema_version {stored['schema_version']}")


# ---------------------------------------------------------------------------
# Run the Validator
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    show("A valid definition", dict(VALID_CONFIG))

    # The model is the one field an agent definition cannot do without: it is what the
    # definition is hydrated around.
    no_model = {key: value for key, value in VALID_CONFIG.items() if key != "model"}
    show("Missing the model", no_model)

    # from_dict builds its kwargs from an explicit list, so an unrecognized key would be
    # silently dropped and the agent would run without the setting you thought you set.
    show("A typo in a field name", {**VALID_CONFIG, "instruction": "singular"})

    # A tool is stored by name; if nothing in the registry can supply its entrypoint, the
    # agent would load with a tool it cannot call.
    show(
        "A tool nothing can supply", {**VALID_CONFIG, "tools": [{"name": "web_serch"}]}
    )

    # Several problems come back at once, so you fix a config in one pass.
    show(
        "Several problems at once",
        {
            "model": {"id": "gpt-5.5", "provider": "acme"},
            "instruction": "singular",
            "num_history_runs": -1,
        },
    )
