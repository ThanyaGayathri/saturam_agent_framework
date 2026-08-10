"""
Tenant Capability Policy
========================

A DB-authored agent definition is a document a tenant writes and the platform executes. The
config validator checks that such a document is well-formed; the capability policy checks
whether that tenant is allowed to compose what it names -- which models, which tools, which
MCP servers -- and blocks the privileged toolkits (shell, python, docker, sql, filesystem,
...) unless a tenant has been explicitly granted them.

This example runs the same checks the components API runs, without a server or a database.

Run:
    .venvs/demo/bin/python cookbook/93_components/capability_policy.py
"""

from pathlib import Path

from egpt.integrations.models.openai import OpenAIResponses
from egpt.integrations.tools.duckduckgo import DuckDuckGoTools
from egpt.integrations.tools.sql import SQLTools
from egpt.os.routers.components.validation import validate_component_config
from egpt.registry import Registry
from egpt.registry.policy import (
    CapabilityPolicy,
    PolicyViolationError,
    enforce_policy_or_raise,
)
from egpt.registry.schema import ConfigValidationError

POLICY_FILE = Path(__file__).parent / "capability_policy.json"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# Registering the real SQLTools matters for more than realism. A stored config records only a
# tool's *name*, never its toolkit, and several privileged toolkits expose the same names
# (read_file lives in four of them). With the toolkit registered, the policy can tell exactly
# which one a name belongs to; without it, it falls back to a static name table and refuses
# every candidate, which is the safe answer but a blunter one.
registry = Registry(
    name="Cookbook Registry",
    tools=[DuckDuckGoTools(), SQLTools(db_url="sqlite:///:memory:")],
    models=[OpenAIResponses(id="gpt-5.5")],
)

policy = CapabilityPolicy.from_file(POLICY_FILE)

BASE_CONFIG = {
    "model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"},
    "name": "Reporting Agent",
    "instructions": ["Answer from the data, not from memory."],
}
SQL_CONFIG = {**BASE_CONFIG, "tools": [{"name": "run_sql_query"}]}


def attempt(label, config, tenant_id):
    """Validate a definition the way the components API does, and print what came back."""
    print(f"\n{label}")
    print(f"  tenant: {tenant_id or '<unscoped>'}")
    try:
        validate_component_config(
            config, "agent", registry=registry, policy=policy, tenant_id=tenant_id
        )
        print("  accepted")
    except ConfigValidationError as error:
        for item in error.errors:
            print(f"  rejected [{item.code}] {item.path}")
            print(f"           {item.message}")


# ---------------------------------------------------------------------------
# 1. A model outside the tenant's allow-list
# ---------------------------------------------------------------------------
# The policy permits "openai:*" by default and only "openai:gpt-5.5" for acme, so a model from
# another provider is refused before anything is persisted.
attempt(
    "1. A model the tenant is not allowed",
    {**BASE_CONFIG, "model": {"id": "claude-sonnet-5", "provider": "Anthropic"}},
    tenant_id="acme",
)

# ---------------------------------------------------------------------------
# 2. A privileged tool, with no grant
# ---------------------------------------------------------------------------
# Note the default tenant has tools: ["*"] and is still refused. The wildcard covers ordinary
# tools; reaching a privileged toolkit takes a separate, explicit grant. That separation is
# the point -- it means a broad tools allow-list can never quietly include shell access.
attempt("2. A privileged tool with no grant", SQL_CONFIG, tenant_id=None)

# ---------------------------------------------------------------------------
# 3. The same definition, for a tenant that has been granted it
# ---------------------------------------------------------------------------
# acme carries granted_privileged: ["sql"], so the identical config is accepted.
attempt("3. The same definition, for a granted tenant", SQL_CONFIG, tenant_id="acme")

# ---------------------------------------------------------------------------
# 4. A tenant allowed nothing at all
# ---------------------------------------------------------------------------
attempt("4. A locked-down tenant", BASE_CONFIG, tenant_id="locked-down")

# ---------------------------------------------------------------------------
# 5. The re-check at hydration
# ---------------------------------------------------------------------------
# Checking on write is not enough on its own: a policy can be tightened after a definition was
# published, and a config row can be written by something other than the API. So the policy is
# checked again every time a stored config is turned into a live agent.
#
# Below, SQL_CONFIG is the row acme legitimately stored in step 3. The grant is then revoked,
# and the very same stored bytes stop hydrating.
print("\n5. Hydration re-check after the grant is revoked")

revoked = CapabilityPolicy.from_dict(
    {
        "tenants": {
            "acme": {
                "models": ["openai:gpt-5.5"],
                "tools": ["*"],
                "granted_privileged": [],
            }
        }
    }
)
try:
    enforce_policy_or_raise(
        SQL_CONFIG,
        "agent",
        tenant_id="acme",
        registry=registry,
        policy=revoked,
        component_id="reporting-agent",
    )
    print("  hydrated")
except PolicyViolationError as error:
    print(f"  refused at hydration ({len(error.violations)} violation)")
    print(f"  {error.violations[0].message}")

# Over HTTP this surfaces as a 403 on GET /agents/reporting-agent, and the agent is simply
# omitted from GET /agents -- one stale definition must not empty a whole listing.

# ---------------------------------------------------------------------------
# 6. No policy configured at all
# ---------------------------------------------------------------------------
# The feature is opt-in. With no document configured and none passed in, no capability check
# runs and everything behaves exactly as it did before the policy existed.
print("\n6. With no policy configured")
enforce_policy_or_raise(
    SQL_CONFIG, "agent", tenant_id="acme", registry=registry, policy=None
)
print("  no policy in force, so nothing is checked")
