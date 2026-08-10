"""Cross-tenant isolation probe (a provider-level SECURITY check).

Isolation is a security boundary the store tests (SM-R3 #7, data model #18) assert
as "0 leakage", but they test it at the *store* level. A ``MemoryProvider``
(SM-R11) sits above the store and can leak across tenants via a scope-binding or
cache bug the store tests cannot see — and this harness is the provider-level
instrument. So we probe it directly: write two tenants' distinct secrets into ONE
provider, read each tenant's scope, and assert neither sees the other's memories.

``cross_tenant_leak`` returns the number of foreign memories that leaked (0 = pass).
The gate (harness/gate.py) fails a build on any leak.
"""

from __future__ import annotations

from typing import Dict

from harness.provider import MemoryEvent, MemoryProvider

_TENANT_A = "tenant_a"
_TENANT_B = "tenant_b"


def _events(tenant: str, tag: str) -> list:
    return [
        MemoryEvent(
            event_id=f"{tag}1",
            text=f"{tag}: my private API key is SECRET-{tag}",
            speaker=tag,
            timestamp=1,
            session_id=f"s_{tenant}",
            tenant=tenant,
        )
    ]


def cross_tenant_leak(provider: MemoryProvider) -> Dict[str, int]:
    """Write A + B into one provider, read each scope, count foreign leaks.

    A leak = a memory whose source id belongs to the OTHER tenant appears in a
    tenant's read. Returns {"leaked", "b_visible_to_a", "a_visible_to_b"}.
    """
    provider.write(_events(_TENANT_A, "A"))
    provider.write(_events(_TENANT_B, "B"))

    as_a = provider.read("private API key", k=10, tenant=_TENANT_A)
    as_b = provider.read("private API key", k=10, tenant=_TENANT_B)

    b_visible_to_a = sum(1 for m in as_a for sid in m.source_ids if sid.startswith("B"))
    a_visible_to_b = sum(1 for m in as_b for sid in m.source_ids if sid.startswith("A"))
    return {
        "leaked": b_visible_to_a + a_visible_to_b,
        "b_visible_to_a": b_visible_to_a,
        "a_visible_to_b": a_visible_to_b,
    }
