"""Knowledge graph + Personalized PageRank for the HippoRAG-style retriever.

We build a bipartite-plus graph (the "hippocampal index"):

  - **entity nodes** (normalized OpenIE phrases)
  - **passage nodes** (one per corpus passage)
  - **membership edges** entity--passage, weighted by the entity's IDF so a
    specific phrase binds its passage more strongly than a generic one
  - **relational edges** entity--entity from triples (weight = co-occurrence count)
  - **synonymy edges** entity--entity when embedding cosine >= tau (weight = cosine),
    so surface-form variants of the same entity share PPR mass (HippoRAG's synonymy)

At query time we seed a personalization vector on the entity nodes matched from the
query and run Personalized PageRank over the whole graph; passage nodes are then
ranked directly by their stationary probability. PPR from the query's seed entities
is the "pattern completion" that reaches bridge passages a single dense hop misses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import networkx as nx
import numpy as np

from harness.openie import Triple, normalize_entity

PASSAGE_PREFIX = "PASSAGE::"


@dataclass
class KnowledgeGraph:
    graph: nx.Graph = field(default_factory=nx.Graph)
    entity_display: Dict[str, str] = field(default_factory=dict)  # key -> a surface form
    entity_passages: Dict[str, set] = field(default_factory=dict)  # entity key -> {pid}
    passage_entities: Dict[str, set] = field(default_factory=dict)  # pid -> {entity key}
    n_passages: int = 0

    # ---------------------------------------------------------------- build
    def add_passage(self, pid: str, triples: Sequence[Triple]) -> None:
        self.n_passages += 1
        self.passage_entities.setdefault(pid, set())
        pnode = PASSAGE_PREFIX + pid
        self.graph.add_node(pnode, kind="passage", pid=pid)
        for t in triples:
            s, o = normalize_entity(t.subject), normalize_entity(t.object)
            for ent, disp in ((s, t.subject), (o, t.object)):
                if not ent:
                    continue
                self._touch_entity(ent, disp, pid, pnode)
            if s and o and s != o:
                self._bump_edge(s, o, "relation", 1.0)

    def _touch_entity(self, ent: str, disp: str, pid: str, pnode: str) -> None:
        if ent not in self.entity_display:
            self.entity_display[ent] = disp
            self.graph.add_node(ent, kind="entity")
        self.entity_passages.setdefault(ent, set()).add(pid)
        self.passage_entities[pid].add(ent)
        # membership edge weight is filled with IDF in finalize(); mark it exists.
        self.graph.add_edge(ent, pnode, kind="member", weight=1.0)

    def _bump_edge(self, a: str, b: str, kind: str, w: float) -> None:
        if self.graph.has_edge(a, b):
            self.graph[a][b]["weight"] += w
        else:
            self.graph.add_edge(a, b, kind=kind, weight=w)

    def idf(self, ent: str) -> float:
        df = len(self.entity_passages.get(ent, ()))
        if df == 0:
            return 0.0
        return math.log((self.n_passages + 1) / df)

    def finalize(self, entity_vectors: Dict[str, np.ndarray], synonym_tau: float = 0.85) -> None:
        """Re-weight membership edges by IDF and add synonymy edges above tau."""
        for ent, pids in self.entity_passages.items():
            w = self.idf(ent)
            for pid in pids:
                pnode = PASSAGE_PREFIX + pid
                if self.graph.has_edge(ent, pnode):
                    self.graph[ent][pnode]["weight"] = max(w, 1e-3)
        self._add_synonym_edges(entity_vectors, synonym_tau)

    def _add_synonym_edges(self, entity_vectors: Dict[str, np.ndarray], tau: float) -> None:
        ents = [e for e in self.entity_display if e in entity_vectors]
        if len(ents) < 2:
            return
        mat = np.vstack([entity_vectors[e] for e in ents])  # already normalized
        sims = mat @ mat.T
        for i in range(len(ents)):
            row = sims[i]
            for j in range(i + 1, len(ents)):
                if row[j] >= tau:
                    self._bump_edge(ents[i], ents[j], "synonym", float(row[j]))

    # ----------------------------------------------------------- retrieval
    def personalized_pagerank(self, seed: Dict[str, float], alpha: float = 0.5) -> Dict[str, float]:
        if not seed:
            return {}
        total = sum(seed.values()) or 1.0
        pers = {k: v / total for k, v in seed.items()}
        return nx.pagerank(self.graph, alpha=alpha, personalization=pers, weight="weight")

    def rank_passages(self, ppr: Dict[str, float], k: int) -> List[Tuple[str, float]]:
        scored = [
            (data["pid"], ppr.get(node, 0.0))
            for node, data in self.graph.nodes(data=True)
            if data.get("kind") == "passage"
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def entity_keys(self) -> List[str]:
        return list(self.entity_display.keys())
