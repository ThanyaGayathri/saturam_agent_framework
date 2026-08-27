"""
Per-Tenant Memory Configuration
===============================

This example shows SM-C5: two tenants, one code path, different memory
backends. A MemoryConfigResolver decides *which* MemoryProviderConfig answers
for an identity; build_memory_provider stays the single construction site, so
nothing downstream learns that per-tenant configuration exists at all.

Key concepts:
- A policy key is (tenant_id, agent_id). None in a slot is a wildcard, so
  ("acme", None) answers for every agent of that tenant.
- Resolution is most-specific-first: exact pair, then tenant-wide, then
  agent-wide, then the identity-less catch-all, then default.
- A tenant-wide policy outranks an agent-wide one. That is the isolation-safe
  precedence: a shared agent must not drag its own backend across tenants.
- Identity strings are stripped on both sides. A policy keyed "acme" and a
  request carrying "acme " are the same tenant, and a blank slot is refused --
  it is not a wildcard, and it cannot identify a tenant either.
- Selection is not isolation. The resolver picks a config; Scope is still what
  partitions data at read and write time.

Resolution is cheap enough to run per request -- four dict lookups, no I/O.
Construction is not: build_memory_provider_for goes all the way down to opening
engines and drivers, so build once per identity and reuse the provider.

This file needs no database, no model and no API key: it registers a tiny
in-process provider so the resolution decisions are the only thing on show.
"""

from typing import Dict

from egpt.memory import (
    MemoryConfigResolver,
    MemoryProvider,
    MemoryProviderConfig,
    MemoryProviderConfigError,
    MemoryTier,
    Substrate,
    build_memory_provider_for,
    register_provider,
)
from egpt.providers.model_provider import ProviderMode

# ---------------------------------------------------------------------------
# A toy provider, so the example is about resolution and nothing else
# ---------------------------------------------------------------------------


@register_provider("cookbook_demo")
class DemoProvider(MemoryProvider):
    """Remembers the config it was built from. A real provider opens backends here."""

    provider_key = "cookbook_demo"

    def __init__(self, config: MemoryProviderConfig) -> None:
        self.config = config

    def read(self, *args, **kwargs):
        return []

    async def aread(self, *args, **kwargs):
        return []

    def write(self, *args, **kwargs):
        return None

    async def awrite(self, *args, **kwargs):
        return None

    def consolidate(self, *args, **kwargs):
        return None

    async def aconsolidate(self, *args, **kwargs):
        return None

    def read_tier(self, *args, **kwargs):
        return []

    async def aread_tier(self, *args, **kwargs):
        return []

    def write_tier(self, *args, **kwargs):
        return None

    async def awrite_tier(self, *args, **kwargs):
        return None

    def tiers(self) -> Dict[MemoryTier, bool]:
        return {tier: False for tier in MemoryTier}

    def substrates(self) -> Dict[Substrate, bool]:
        # A tenant is vector-only when its config names no graph backend.
        return {Substrate.VECTOR: True, Substrate.GRAPH: bool(self.config.graph)}


def config(mode: ProviderMode = ProviderMode.LOCAL, **kwargs) -> MemoryProviderConfig:
    return MemoryProviderConfig(provider="cookbook_demo", mode=mode, **kwargs)


# ---------------------------------------------------------------------------
# One resolver, four tenants with genuinely different deployments
# ---------------------------------------------------------------------------

resolver = MemoryConfigResolver(
    policies={
        # An air-gapped customer: Mode A, and a graph backend alongside vector.
        "airgapped-bank": config(
            ProviderMode.LOCAL,
            vector={"backend": "pgvector", "dsn": "postgresql://localhost/bank"},
            graph={"backend": "neo4j", "uri": "bolt://localhost:7687"},
        ),
        # A hosted customer on Mode B, vector-only: no graph key at all.
        "hosted-startup": config(
            ProviderMode.FRONTIER,
            vector={"backend": "pgvector", "dsn": "postgresql://hosted/startup"},
        ),
        # One agent of one tenant pinned to its own store, more specific than
        # the tenant-wide policy below.
        ("enterprise", "research-agent"): config(
            ProviderMode.LOCAL,
            vector={"backend": "pgvector", "dsn": "postgresql://localhost/research"},
        ),
        ("enterprise", None): config(
            ProviderMode.LOCAL,
            vector={"backend": "pgvector", "dsn": "postgresql://localhost/enterprise"},
        ),
    },
    # Everyone else. Two tenants landing here share one physical store -- Scope,
    # not the resolver, is what keeps their rows apart.
    default=config(
        ProviderMode.FRONTIER,
        vector={"backend": "pgvector", "dsn": "postgresql://shared/pool"},
    ),
)


print("=== Which config answers for whom ===")
for tenant, agent in [
    ("airgapped-bank", None),
    ("hosted-startup", None),
    ("enterprise", "research-agent"),
    ("enterprise", "some-other-agent"),
    ("a-tenant-with-no-policy", None),
]:
    resolved = resolver.lookup(tenant, agent)
    who = tenant if agent is None else f"{tenant}/{agent}"
    print(f"  {who:<38} mode={resolved.mode.value:<8} dsn={resolved.vector['dsn']}")


print()
print("=== Most-specific-first, shown as the probes actually tried ===")
# Two of these keys exist, but only the first one reached wins -- which is the
# whole of "most specific first", and the answer to "why did this tenant get
# that config?".
already_won = False
for key in resolver.candidate_keys("enterprise", "research-agent"):
    if key not in resolver.policies:
        note = ""
    elif already_won:
        note = "   (also defined, but never reached)"
    else:
        note = "   <-- wins"
        already_won = True
    print(f"  {key}{note}")


print()
print("=== The graph toggle is per tenant ===")
for tenant in ["airgapped-bank", "hosted-startup"]:
    provider = build_memory_provider_for(resolver, tenant)
    substrates = provider.substrates()
    shape = "vector + graph" if substrates[Substrate.GRAPH] else "vector-only"
    print(f"  {tenant:<20} {shape}")


print()
print("=== A tenant-wide policy outranks an agent-wide one ===")
# The isolation-relevant precedence: "shared-agent" carries a policy across
# every tenant, but acme's own tenant-wide policy still wins for acme.
precedence = MemoryConfigResolver(
    policies={
        ("acme", None): config(ProviderMode.LOCAL, vector={"dsn": "acme-own-store"}),
        (None, "shared-agent"): config(
            ProviderMode.FRONTIER, vector={"dsn": "shared-agent-store"}
        ),
    }
)
print(
    f"  acme + shared-agent -> {precedence.lookup('acme', 'shared-agent').vector['dsn']}"
)
print(
    f"  beta + shared-agent -> {precedence.lookup('beta', 'shared-agent').vector['dsn']}"
)


print()
print("=== Padding does not create a second tenant ===")
padded = resolver.lookup("airgapped-bank ")
print(
    f"  lookup('airgapped-bank ') -> mode={padded.mode.value} (same policy, not the default)"
)


print()
print("=== Misconfiguration is refused at definition, not at lookup ===")

# Two spellings of one key: whichever iterated last would otherwise win
# silently, leaving the loser inert with nothing to say so.
try:
    MemoryConfigResolver(
        policies={"acme": config(), ("acme", None): config(ProviderMode.FRONTIER)}
    )
except MemoryProviderConfigError as exc:
    print(f"  duplicate key       -> {exc}")

# An identity-less policy beside a default means the default can never win.
try:
    MemoryConfigResolver(
        policies={(None, None): config()}, default=config(ProviderMode.FRONTIER)
    )
except MemoryProviderConfigError as exc:
    print(f"  shadowed default    -> {exc}")

# A blank slot is not a wildcard. None is.
try:
    MemoryConfigResolver(policies={"   ": config()})
except MemoryProviderConfigError as exc:
    print(f"  blank policy key    -> {exc}")

# An int tenant id -- a common shape straight out of a database -- used to
# resolve to the default with no signal at all.
try:
    resolver.lookup(12345)
except MemoryProviderConfigError as exc:
    print(f"  non-string identity -> {exc}")

# An unmatched identity names what was tried, in order.
try:
    build_memory_provider_for(
        MemoryConfigResolver(policies={"acme": config()}), "beta", "agent-9"
    )
except MemoryProviderConfigError as exc:
    print(f"  no policy, no default -> {exc}")


print()
print("=== The build path requires a tenant ===")
# lookup() stays permissive so an operator can ask diagnostic questions about
# wildcard policies. Building a provider for nobody in particular is refused:
# a seam where the unscoped call is the easiest one to write is a seam that
# leaks.
try:
    build_memory_provider_for(resolver)  # type: ignore[call-arg]
except TypeError as exc:
    print(f"  build_memory_provider_for(resolver) -> TypeError: {exc}")
print(
    f"  resolver.lookup() (diagnostic, still allowed) -> mode={resolver.lookup().mode.value}"
)
