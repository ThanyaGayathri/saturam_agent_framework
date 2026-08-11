"""Mode B: the seam over external memory services (SM-C1).

**This adapter is deliberately thin, and that is the design, not an omission.** SPEC section 4 is
explicit: *"Mode A wires co-located substrates (in-VPC pgvector + Neo4j); Mode B wires external
services. Both satisfy the identical ports, so the provider and all consumers are unchanged."* The
composition -- fusion, write fan-out, point-in-time filtering, consolidation -- is transport
agnostic, so duplicating it here to say "but over HTTP" would be copied code that drifts.

What Mode B genuinely changes is which substrate *implementations* are wired, plus one thing worth
recording: a read now crosses the network, and a trace that cannot tell a local index scan from a
remote round trip is hard to reason about. So this subclass exists to (a) give config a distinct
key to select, and (b) stamp the deployment into the fusion diagnostics for SM-X4 (#140).

**Honest limit, carried forward deliberately.** ADR 0003 section 5 asks SM-C1 to *"re-prove
config-only swap against a real external backend"* -- and notes that the SM-R11 proof used a stub,
so it establishes the seam *shape* only: no network, no provider SDK, no real latency. That
re-proof is not possible yet, because no external substrate exists to point at (SM-V1 #95, SM-G1
#99). It is carried to **SM-X2 (#138)**, which owns Mode B, rather than quietly dropped. Nothing
here should be read as evidence that a real external deployment is config-only.
"""

from typing import List, Optional

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, register_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.types import MemoryQuery, MemoryRecord, MemorySlice, Scope

__all__ = ["ExternalMemoryProvider"]


class ExternalMemoryProvider(CoLocatedMemoryProvider):
    """The same composition, over substrates that live outside the VPC.

    Inherits every contract property the co-located provider enforces -- one id across substrates,
    point-in-time on the fused set, identical tier resolution, SCD-2 writes -- because those are
    properties of the seam, not of where the bytes are.

    The mode is **not** policed here. An external service reached inside a VPC is a real
    deployment, and a provider that refused it would be enforcing a naming convention rather than
    an invariant. ``config.mode`` records intent; ``build_memory_provider`` selects the wiring.
    """

    provider_key = "external"

    def _assemble(
        self,
        scope: Scope,
        query: MemoryQuery,
        vector_hits: List[MemoryRecord],
        graph_hits: List[MemoryRecord],
    ) -> MemorySlice:
        """Fuse as Mode A does, then record that the legs came over the network.

        Latency and failure look completely different across a network boundary, and a trace that
        cannot distinguish the two deployments cannot explain a slow read.
        """
        slice_ = super()._assemble(scope, query, vector_hits, graph_hits)
        slice_.fusion["deployment"] = "external"
        return slice_


@register_provider("external")
def _build_external(config: MemoryProviderConfig) -> MemoryProvider:
    """Build the external provider from config.

    Substrates arrive through ``config.options``, the same pre-backend path the co-located factory
    uses and for the same reason: there is no external backend registered to resolve yet. When one
    exists, ``config.vector``/``config.graph`` carry its connection settings and it resolves
    through ``egpt.registry.discovery.construct``.
    """
    vector: Optional[VectorSubstrate] = config.options.get("vector_substrate")
    graph: Optional[GraphSubstrate] = config.options.get("graph_substrate")
    return ExternalMemoryProvider(vector=vector, graph=graph, config=config)
