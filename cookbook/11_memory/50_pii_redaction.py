"""
PII Redaction on Memory Write/Read (SM-X5)
============================================

This example shows SM-X5: PII containment at the two memory boundaries. Memory
is a sensitive data sink -- something written once can be re-injected into a
model's context many times over -- so `egpt.memory.security.pii` redacts PII
**on write-back** (before an event's content reaches storage) and **again on
read/inject** (before an assembled feed-forward context re-enters a run's
input). It reuses the seed's guardrail, `egpt.guardrails.pii.PIIDetectionGuardrail`
(SSN/CC/email/phone, plus any custom patterns), rather than a second regex
implementation.

Key concepts:
- Both boundaries, not one (issue #141, Decision 1): a redact-once policy
  leaks on whichever path it skips. Redacting only on write leaves a record
  written before a tenant's policy turned PII handling on to leak on read;
  redacting only on read leaves raw PII sitting in the store in between.
- Driven by tenant policy via `MemoryProviderConfig.pii` -- the same
  per-tenant config `47_per_tenant_memory_config.py` resolves per identity
  (SM-C5), so one tenant can have PII handling on while another has it off,
  or bring its own `PIIDetectionGuardrail` with different patterns.
- `{}` (the default) is a safe no-op, the same "zero-config is off" contract
  `config.fusion`/`config.rerank` already use -- a deployment that never
  opts a tenant in keeps pre-SM-X5 behavior exactly.
- Write-back redaction lives inside `CoLocatedMemoryProvider.write`/`awrite`
  themselves (`SeedMemoryProvider` too) -- the same per-adapter seam SM-X4's
  `memory_span` tracing already uses, not the agent/team run loop. Every
  caller that reaches storage through `write` gets it: the run loop's
  write-back hooks, `WorkingMemory.offload`, or a direct `provider.write`
  call, as this file makes below.

This file needs no database, no model and no API key: it uses the same
trimmed in-memory vector stand-in `23_consolidation_worker.py`/
`39_working_memory.py`/`48_memory_tracing.py` use.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from egpt.guardrails.pii import PIIDetectionGuardrail
from egpt.memory import (
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    require_scope,
)
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.security.pii import redact_events_for_writeback
from egpt.memory.working import WorkingMemory

SCOPE = Scope(tenant_id="acme", user_id="ada@example.com")

# ---------------------------------------------------------------------------
# A toy vector substrate -- the same trimmed shape 48_memory_tracing.py uses,
# so this file needs no database, no API key, and no network.
# ---------------------------------------------------------------------------


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity."""

    def __init__(self) -> None:
        self.rows: Dict[str, MemoryRecord] = {}

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        for record in records:
            self.rows[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        self.upsert(scope, records)

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        wanted = {tier} if tier else set(query.resolved_tiers())
        terms = set(query.text.lower().split())
        hits = [
            replace(
                record, score=float(len(terms & set(record.content.lower().split())))
            )
            for record in self.rows.values()
            if record.tier in wanted and record.expired_at is None
        ]
        ranked = sorted((h for h in hits if h.score), key=lambda r: -(r.score or 0.0))
        return ranked[: query.limit] if query.limit else []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        return self.search(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        rows = [replace(r) for r in self.rows.values() if r.tier is tier]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        if after is not None:
            rows = [r for r in rows if ((r.created_at or 0), r.id) > after]
        return rows if limit is None else rows[:limit]

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)


PII_MESSAGE = "Reach Ada at ada@example.com or 555-123-4567 about the renewal."


# ---------------------------------------------------------------------------
# Off by default: raw PII lands in storage and comes back out on read
# ---------------------------------------------------------------------------

print("=== A tenant with no PII policy (the zero-config default) ===")
off_vector = InMemoryVector()
off_provider = CoLocatedMemoryProvider(
    vector=off_vector
)  # config.pii defaults to {"enabled": False}

off_provider.write(SCOPE, [MemoryEvent(content=PII_MESSAGE, scope=SCOPE)])
stored = next(iter(off_vector.rows.values()))
print(f"  stored content   -> {stored.content!r}")

wm = WorkingMemory(scope=SCOPE)
context = off_provider.assemble_context(SCOPE, wm, MemoryQuery(text=PII_MESSAGE))
print(f"  injected context -> {context!r}")


# ---------------------------------------------------------------------------
# A tenant that opts in: both boundaries redact
# ---------------------------------------------------------------------------

print()
print("=== A tenant with PII handling on (config.pii={'enabled': True}) ===")
on_vector = InMemoryVector()
on_config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
on_provider = CoLocatedMemoryProvider(vector=on_vector, config=on_config)

on_provider.write(SCOPE, [MemoryEvent(content=PII_MESSAGE, scope=SCOPE)])
stored = next(iter(on_vector.rows.values()))
print(f"  stored content   -> {stored.content!r}")
print(f"  pii_redacted     -> {stored.metadata.get('pii_redacted')}")

wm = WorkingMemory(scope=SCOPE)
context = on_provider.assemble_context(SCOPE, wm, MemoryQuery(text=PII_MESSAGE))
print(f"  injected context -> {context!r}")


# ---------------------------------------------------------------------------
# Both boundaries independently -- a record written before the policy turned
# on is still caught on read (issue #141, Decision 1)
# ---------------------------------------------------------------------------

print()
print(
    "=== Read/inject is a second, independent boundary -- not a backstop for write-back ==="
)
shared_vector = InMemoryVector()
# Written while PII handling was off for this tenant (or by code predating SM-X5 entirely).
CoLocatedMemoryProvider(vector=shared_vector).write(
    SCOPE, [MemoryEvent(content=PII_MESSAGE, scope=SCOPE)]
)

now_on = CoLocatedMemoryProvider(vector=shared_vector, config=on_config)
wm = WorkingMemory(scope=SCOPE)
context = now_on.assemble_context(SCOPE, wm, MemoryQuery(text=PII_MESSAGE))
print(f"  unredacted record, read with the policy now on -> {context!r}")


# ---------------------------------------------------------------------------
# What write()/awrite() actually call, before an event's content lands in a
# vector or graph substrate: redact_events_for_writeback
# ---------------------------------------------------------------------------

print()
print("=== What CoLocatedMemoryProvider.write/awrite call before storage ===")
events = [MemoryEvent(content=PII_MESSAGE, scope=SCOPE)]
redacted = redact_events_for_writeback(events, on_config)
print(f"  original content -> {events[0].content!r}")
print(f"  redacted content  -> {redacted[0].content!r}")
print(f"  original event untouched -> {events[0] is not redacted[0]}")


# ---------------------------------------------------------------------------
# Per-tenant policy: two tenants, two guardrails, one deployment
# ---------------------------------------------------------------------------

print()
print("=== A custom guardrail is a policy choice too, per tenant ===")
custom_guardrail = PIIDetectionGuardrail(
    custom_patterns={"account_number": r"\bACCT-\d{6}\b"}
)
custom_config = MemoryProviderConfig(
    provider="co_located", pii={"enabled": True, "guardrail": custom_guardrail}
)
custom_provider = CoLocatedMemoryProvider(vector=InMemoryVector(), config=custom_config)

account_message = "Your account is ACCT-482913, reach us at support@example.com."
custom_provider.write(SCOPE, [MemoryEvent(content=account_message, scope=SCOPE)])
stored = next(iter(custom_provider.vector.rows.values()))
print(f"  built-in Email check runs -> {'support@example.com' not in stored.content}")
print(f"  custom account pattern runs -> {'ACCT-482913' not in stored.content}")
print(f"  pii_redacted -> {stored.metadata.get('pii_redacted')}")
