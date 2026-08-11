"""Graphiti adapter -- runs the REAL graphiti-core library over SM-R10's scenario set.

The point of this adapter is to test *Graphiti's own temporal engine*, not a
reimplementation of it. So every load-bearing step routes through library code:

* the edge model is ``graphiti_core.edges.EntityEdge`` (its four timestamps:
  ``valid_at`` / ``invalid_at`` / ``created_at`` / ``expired_at``);
* the contradiction rule is ``graphiti_core.utils.maintenance.edge_operations
  .resolve_edge_contradictions`` -- Graphiti's actual invalidation function;
* persistence is ``EntityEdge.save()`` against a real Neo4j 5 (the same container
  SM-R10 measured on), which issues Graphiti's own
  ``MERGE (s)-[e:RELATES_TO {uuid}]->(t) SET e = $edge_data``.

The LLM is deliberately bypassed: valid-times are supplied directly from the
scenario timeline rather than extracted, so what is measured is the temporal
model in isolation. That is strictly *favourable* to Graphiti -- it removes
extraction error as a confound.

Read path: Graphiti ships no "true at T" predicate (there is no COALESCE anywhere
in the package, and no query filters on ``expired_at IS NULL``). Its
``SearchFilters`` DSL is an OR-of-AND list, which *can* express the open-interval
branch, so ``_where_5a`` below builds the most favourable correct 5a-equivalent
the DSL allows. Any failure is therefore a property of Graphiti's **write** model,
not of a strawman read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode
from graphiti_core.utils.maintenance.edge_operations import resolve_edge_contradictions

GROUP = "smr2"

# Stub embedding: Graphiti persists vector properties on nodes and edges and the
# Neo4j procedure rejects null. Dimension is arbitrary here.
_STUB_VEC = [0.0] * 8


def _dt(epoch_s: int | None) -> datetime | None:
    if epoch_s is None:
        return None
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc)


class GraphitiBiTemporalStore:
    """Drive Graphiti's temporal engine over the SM-R10 op/probe protocol."""

    def __init__(self, driver: Any) -> None:
        self.driver = driver
        self._nodes: dict[str, EntityNode] = {}

    # ------------------------------------------------------------------ nodes
    async def _node(self, name: str) -> EntityNode:
        key = f"{GROUP}:{name}"
        if key not in self._nodes:
            node = EntityNode(name=name, group_id=GROUP, labels=["Entity"])
            # Graphiti's Neo4j save calls db.create.setNodeVectorProperty, which
            # rejects null. Similarity is irrelevant to the temporal probes, so a
            # constant stub keeps the write path exercised without an embedder.
            node.name_embedding = _STUB_VEC
            await node.save(self.driver)
            self._nodes[key] = node
        return self._nodes[key]

    # ------------------------------------------------------------------ write
    async def assert_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        user: str,
        t_valid: int,
        at: int,
        edge_id: str | None = None,
    ) -> str:
        """Assert a fact, resolving contradictions with Graphiti's own rule."""
        src = await self._node(subject)
        tgt = await self._node(obj)

        new_edge = EntityEdge(
            source_node_uuid=src.uuid,
            target_node_uuid=tgt.uuid,
            name=predicate,
            group_id=GROUP,
            fact=f"{subject} {predicate} {obj}",
            created_at=_dt(at),
            valid_at=_dt(t_valid),
        )
        # Graphiti has no tenant concept on the edge; carry user_id as an attribute
        # so scenario 8 (tenant isolation) is expressible at all.
        new_edge.attributes = {"user_id": user, "subject": subject, "object": obj}
        new_edge.fact_embedding = _STUB_VEC
        if edge_id:
            new_edge.uuid = edge_id

        # Candidates = currently-live edges for this (subject, predicate, tenant).
        candidates = await self._live_edges(subject, predicate, user)

        # >>> Graphiti's real contradiction rule <<<
        invalidated = resolve_edge_contradictions(new_edge, candidates)

        # Persist exactly as Graphiti does: mutated candidates are re-saved on
        # their own uuid (SET e = $edge_data -> in-place overwrite).
        for e in invalidated:
            await e.save(self.driver)
        await new_edge.save(self.driver)
        return new_edge.uuid

    async def retract(self, edge_id: str, at: int) -> None:
        """R2-equivalent: expire on the transaction axis."""
        await self.driver.execute_query(
            "MATCH ()-[e:RELATES_TO {uuid:$uuid}]->() SET e.expired_at = $at RETURN e.uuid",
            uuid=edge_id,
            at=_dt(at),
        )

    async def _live_edges(
        self, subject: str, predicate: str, user: str
    ) -> list[EntityEdge]:
        """Edges Graphiti still holds as current belief for this identity."""
        rows, _, _ = await self.driver.execute_query(
            "MATCH (s:Entity)-[e:RELATES_TO]->(o:Entity) "
            "WHERE e.group_id = $g AND e.name = $p AND e.user_id = $u "
            "  AND s.name = $s AND e.expired_at IS NULL "
            "RETURN e, s.uuid AS su, o.uuid AS ou, o.name AS oname",
            g=GROUP,
            p=predicate,
            u=user,
            s=subject,
        )
        out: list[EntityEdge] = []
        for r in rows:
            e = r["e"]
            edge = EntityEdge(
                uuid=e["uuid"],
                source_node_uuid=r["su"],
                target_node_uuid=r["ou"],
                name=e["name"],
                group_id=e["group_id"],
                fact=e["fact"],
                created_at=e["created_at"].to_native() if e.get("created_at") else None,
                valid_at=e["valid_at"].to_native() if e.get("valid_at") else None,
                invalid_at=e["invalid_at"].to_native() if e.get("invalid_at") else None,
                expired_at=e["expired_at"].to_native() if e.get("expired_at") else None,
            )
            edge.attributes = {"object": r["oname"], "user_id": e.get("user_id")}
            edge.fact_embedding = _STUB_VEC
            out.append(edge)
        return out

    # ------------------------------------------------------------------- read
    async def query_valid_at(
        self, subject: str, predicate: str, user: str, t: int
    ) -> list[str]:
        """5a -- 'true at world-time T', current belief.

        Written as the most favourable correct form Graphiti's model allows:
        both bounds, open-interval branch handled, transaction axis filtered.
        Graphiti ships none of this; it is supplied by the caller.
        """
        rows, _, _ = await self.driver.execute_query(
            "MATCH (s:Entity)-[e:RELATES_TO]->(o:Entity) "
            "WHERE e.group_id = $g AND e.name = $p AND e.user_id = $u AND s.name = $s "
            "  AND e.valid_at <= $t "
            "  AND ($t < e.invalid_at OR e.invalid_at IS NULL) "
            "  AND e.expired_at IS NULL "
            "RETURN o.name AS obj",
            g=GROUP,
            p=predicate,
            u=user,
            s=subject,
            t=_dt(t),
        )
        return [r["obj"] for r in rows]

    async def query_bitemporal(
        self, subject: str, predicate: str, user: str, t: int, tt: int
    ) -> list[str]:
        """5b -- 'as believed at transaction-time TT, about world-time T'."""
        rows, _, _ = await self.driver.execute_query(
            "MATCH (s:Entity)-[e:RELATES_TO]->(o:Entity) "
            "WHERE e.group_id = $g AND e.name = $p AND e.user_id = $u AND s.name = $s "
            "  AND e.valid_at <= $t "
            "  AND ($t < e.invalid_at OR e.invalid_at IS NULL) "
            "  AND e.created_at <= $tt "
            "  AND ($tt < e.expired_at OR e.expired_at IS NULL) "
            "RETURN o.name AS obj",
            g=GROUP,
            p=predicate,
            u=user,
            s=subject,
            t=_dt(t),
            tt=_dt(tt),
        )
        return [r["obj"] for r in rows]

    async def reset(self) -> None:
        await self.driver.execute_query(
            "MATCH (n) WHERE n.group_id = $g DETACH DELETE n", g=GROUP
        )
        self._nodes.clear()
