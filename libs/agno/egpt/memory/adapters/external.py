"""Mode B: external vector + graph services behind the same seam (SM-X2, #138).

SM-R11/SM-C1 built the seam so a backend, or a tenant's deployment mode, swaps by editing
:class:`~egpt.memory.config.MemoryProviderConfig` -- no call site names ``CoLocatedMemoryProvider``
or an SDK. SM-C1 could only prove that shape against in-memory doubles, and its own record (SPEC
section 11.2) is explicit about why: registering a Mode B adapter against substrates that did not
exist yet "would have composed the same two ports over the same in-memory doubles. That proves a
class can be subclassed, not that a network seam holds." SM-V1 (#95, pgvector) and SM-G1 (#99,
Neo4j) have since landed real backends, and SM-X1 (#137) re-proved the config-only swap for Mode A
against them (``tests/integration/memory/test_mode_a.py``). This module is the Mode B half of that
same re-proof (``tests/integration/memory/test_mode_b.py``), plus the adapter it proves.

**There is no second provider shape to write.** ``CoLocatedMemoryProvider`` already composes
whichever :class:`~egpt.memory.substrates.VectorSubstrate`/:class:`~egpt.memory.substrates.GraphSubstrate`
``resolve_substrate`` hands it, in-VPC or across a network alike -- the port is the seam, and a
port does not know or care where the process on the other end of it runs. ``ExternalMemoryProvider``
below is a thin subclass for exactly that reason: composing the identical two ports a second time
under a different name would be the fork ADR 0003's WRAP-over-REPLACE decision (SPEC section 6)
argues against, and would give the two modes a chance to drift that one shared implementation
cannot.

What *is* Mode B's own, and what this subclass adds:

* A distinct ``config.provider`` registry key (``"external"``), so a tenant's Mode A/B switch
  (SM-C5, #93) can select an adapter, not only a ``config.mode`` value that (until now) selected
  nothing (``test_provider_swap.py``'s former ``test_the_mode_is_carried_and_selects_no_adapter``,
  now ``test_the_mode_is_carried_and_the_external_adapter_selects_on_it``).
* Refusing construction under ``mode=LOCAL``. Mode A and Mode B are ``ProviderMode.LOCAL`` and
  ``.FRONTIER`` (SPEC deviation D1); a deployment that names ``provider="external"`` under
  ``mode=LOCAL`` has two answers to "which mode am I in" that disagree, which is exactly the drift
  ``resolve_substrate``'s own ``config.{field}`` + ``mode`` duplicate-mode refusal already rejects
  one level down. Catching it here, at construction, names the actual mismatch instead of
  surfacing as a confusing air-gap refusal from deep inside ``resolve_substrate`` or the embedder
  it resolves (SM-V2 #96's ``_reject_online_for_local``).
* Stamping ``deployment: "external"`` into every ``MemorySlice.fusion`` payload, via
  :meth:`~egpt.memory.adapters.co_located.CoLocatedMemoryProvider._fusion_extra` -- the extension
  point that hook exists for, per ``fusion_diagnostics``'s own "a floor, not a ceiling" docstring.
  A caller that logs or asserts on ``slice_.fusion`` can tell which side of the seam a read
  answered from without branching on ``type(provider)`` or reaching past the seam for
  ``config.mode``.

**Egress / PII (SM-X5, #141) is not re-handled here.** ``config.pii`` already redacts on
write-back and read-inject for every provider, Mode A or B alike (``egpt.memory.security.pii``,
reached from ``MemoryProvider.write``/``assemble_context`` on the base class this subclasses) --
it is deliberately tenant-policy-driven, not mode-driven, since a Mode A tenant with sensitive data
and a Mode B tenant both want the same boundary. Nothing about crossing a real network changes
which fields get scanned or when.

**What real backends actually change is already elsewhere.** ``PgVectorMemorySubstrate`` (SM-V1)
and the graph engine (SM-G1) already accept ``mode`` and act on it -- an embedder or reranker that
reaches a provider's network is refused under ``mode=LOCAL`` and accepted under ``mode=FRONTIER``
(``egpt.memory.embedding``, ADR 0003 A.3c; ``egpt.memory.config._reject_online_reranker_for_local``).
That is the one place Mode A and Mode B were already meant to diverge in behaviour, not in shape --
this adapter does not add a second one.
"""

from typing import Any, Dict, List, Optional, Sequence

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider, build_co_located_kwargs
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, register_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    ConsolidationReport,
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    Scope,
    WriteReceipt,
    require_scope,
)
from egpt.providers.model_provider import ProviderMode

__all__ = ["ExternalMemoryProvider"]


class ExternalMemoryProvider(CoLocatedMemoryProvider):
    """Mode B: the same two substrate ports as :class:`CoLocatedMemoryProvider`, wired to
    services outside the deployment's VPC. See the module docstring for what this subclass adds
    and, more importantly, what it deliberately does not.

    **Every data-access verb is re-declared here, each delegating straight to the base class.**
    Not for behaviour -- there is none to add per verb, only :meth:`_fusion_extra`'s diagnostics
    stamp -- but because ``test_tenant_scoping.py``'s ``test_every_adapter_guards_every_data_access
    _method_first`` parses each registered adapter's *own* source for a leading
    ``require_scope(scope, method=...)`` call, by design (its docstring: "a method with no test is
    exactly the one that loses its guard"), and does not walk the MRO to credit an inherited one.
    ``co_located``/``seed`` earned that check by being genuinely different shapes
    (``test_provider_swap.py``'s own words); an adapter that is a thin subclass earns it by
    re-stating the guard at its own entry points instead, which costs ten short methods and keeps
    the check meaningful for a class it was never written to see through.
    """

    provider_key = "external"

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        require_scope(scope, method="read")
        return super().read(scope, query)

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        require_scope(scope, method="aread")
        return await super().aread(scope, query)

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        require_scope(scope, method="read_tier")
        return super().read_tier(scope, tier, query)

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        require_scope(scope, method="aread_tier")
        return await super().aread_tier(scope, tier, query)

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="write")
        return super().write(scope, events)

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="awrite")
        return await super().awrite(scope, events)

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="write_tier")
        return super().write_tier(scope, tier, events)

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="awrite_tier")
        return await super().awrite_tier(scope, tier, events)

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        require_scope(scope, method="consolidate")
        return super().consolidate(scope, tiers)

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        require_scope(scope, method="aconsolidate")
        return await super().aconsolidate(scope, tiers)

    def _fusion_extra(self) -> Dict[str, Any]:
        return {"deployment": "external"}


@register_provider("external")
def _build_external(config: MemoryProviderConfig) -> MemoryProvider:
    """The only construction call site for Mode B, mirroring ``co_located._build_co_located``.

    Reuses :func:`~egpt.memory.adapters.co_located.build_co_located_kwargs` rather than
    re-resolving ``config.vector``/``config.graph`` a second way -- the same
    ``discovery.construct`` path either mode takes, so a backend cannot diverge between the two
    factories' idea of how to build it.
    """
    if config.mode is not ProviderMode.FRONTIER:
        raise MemoryProviderConfigError(
            f"provider='external' (Mode B) requires mode={ProviderMode.FRONTIER.value!r}; got "
            f"mode={config.mode.value!r}. Mode A and Mode B are the same seam over different "
            "services, distinguished by config.mode (SPEC deviation D1) -- naming provider="
            "'external' under mode=LOCAL gives this config two disagreeing answers to \"which mode "
            "am I in\". Use provider='co_located' for Mode A, or set mode=FRONTIER here for Mode B.",
            field_name="mode",
        )
    return ExternalMemoryProvider(**build_co_located_kwargs(config))
