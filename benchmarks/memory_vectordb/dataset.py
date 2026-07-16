"""Synthetic memory corpus + exact-NN ground truth for the SM-R3 benchmark.

A "memory record" here is a short text plus a tenant_id and a couple of metadata
fields, mirroring the MemoryRecord shape on page 5 of the architecture (tenant_id,
type, content, importance). We keep the text synthetic but *distinct* so that each
record maps to a distinct vector via DeterministicEmbedder.

Ground truth: because vectors are deterministic functions of content, the exact
top-k nearest neighbours (cosine) can be computed by brute force. We do it in chunks
to bound memory at 1M+ scale. Recall@k for a store = |store_topk ∩ exact_topk| / k.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from .embedder import DeterministicEmbedder

MEMORY_TYPES = ("episodic", "semantic", "procedural")
_TOPICS = (
    "onboarding",
    "billing",
    "deployment",
    "incident",
    "roadmap",
    "compliance",
    "hiring",
    "support-ticket",
    "architecture",
    "security-review",
)
_ACTIONS = (
    "asked about",
    "resolved",
    "escalated",
    "documented",
    "reviewed",
    "approved",
    "flagged",
    "summarized",
)


@dataclass
class Record:
    id: str
    content: str
    tenant_id: int
    mem_type: str
    importance: float


def generate_records(n: int, num_tenants: int, seed: int = 7) -> List[Record]:
    """Generate n distinct synthetic memory records spread across num_tenants tenants."""
    rng = np.random.default_rng(seed)
    records: List[Record] = []
    for i in range(n):
        tenant = int(i % num_tenants)
        topic = _TOPICS[rng.integers(len(_TOPICS))]
        action = _ACTIONS[rng.integers(len(_ACTIONS))]
        mem_type = MEMORY_TYPES[rng.integers(len(MEMORY_TYPES))]
        # Include i to guarantee uniqueness -> unique vector.
        content = f"[t{tenant}] memory {i}: user {action} {topic} (ctx {rng.integers(1_000_000)})"
        records.append(
            Record(
                id=f"mem-{i}",
                content=content,
                tenant_id=tenant,
                mem_type=mem_type,
                importance=round(float(rng.random()), 4),
            )
        )
    return records


def generate_queries(m: int, num_tenants: int, seed: int = 99) -> List[Dict]:
    """Generate m query strings, each tagged with the tenant it should search within."""
    rng = np.random.default_rng(seed)
    queries: List[Dict] = []
    for j in range(m):
        tenant = int(j % num_tenants)
        topic = _TOPICS[rng.integers(len(_TOPICS))]
        action = _ACTIONS[rng.integers(len(_ACTIONS))]
        text = f"[t{tenant}] recall {action} {topic}? (q{j})"
        queries.append({"text": text, "tenant_id": tenant})
    return queries


def exact_topk(
    records: List[Record],
    queries: List[Dict],
    embedder: DeterministicEmbedder,
    k: int,
    per_tenant: bool = False,
    chunk_size: int = 100_000,
) -> List[List[str]]:
    """Exact cosine top-k record ids per query (brute force, chunked over the corpus).

    If per_tenant is True, each query only competes against records of its own tenant,
    matching the tenant_id-filtered search path.
    """
    q_texts = [q["text"] for q in queries]
    q_mat = embedder.matrix(q_texts)  # (Q, dim), unit vectors

    if per_tenant:
        # Bucket record indices by tenant, then brute-force within each query's tenant.
        by_tenant: Dict[int, List[int]] = {}
        for idx, r in enumerate(records):
            by_tenant.setdefault(r.tenant_id, []).append(idx)
        results: List[List[str]] = []
        # Cache per-tenant matrices lazily.
        tenant_mat: Dict[int, np.ndarray] = {}
        for qi, q in enumerate(queries):
            idxs = by_tenant.get(q["tenant_id"], [])
            if q["tenant_id"] not in tenant_mat:
                tenant_mat[q["tenant_id"]] = embedder.matrix(
                    [records[i].content for i in idxs]
                )
            mat = tenant_mat[q["tenant_id"]]
            sims = mat @ q_mat[qi]
            top = np.argsort(-sims)[:k]
            results.append([records[idxs[t]].id for t in top])
        return results

    # Global search: stream the corpus in chunks, keep a running top-k per query.
    best_ids: List[List[str]] = [[] for _ in queries]
    best_scores = np.full((len(queries), k), -np.inf, dtype=np.float32)
    best_idx = np.full((len(queries), k), -1, dtype=np.int64)

    for start in range(0, len(records), chunk_size):
        chunk = records[start : start + chunk_size]
        mat = embedder.matrix([r.content for r in chunk])  # (C, dim)
        sims = q_mat @ mat.T  # (Q, C)
        # Merge this chunk's local top-k with the running best.
        local_k = min(k, sims.shape[1])
        local_top = np.argpartition(-sims, local_k - 1, axis=1)[:, :local_k]
        for qi in range(len(queries)):
            cand_local = local_top[qi]
            cand_scores = sims[qi, cand_local]
            cand_global = cand_local + start
            merged_scores = np.concatenate([best_scores[qi], cand_scores])
            merged_idx = np.concatenate([best_idx[qi], cand_global])
            order = np.argsort(-merged_scores)[:k]
            best_scores[qi] = merged_scores[order]
            best_idx[qi] = merged_idx[order]

    for qi in range(len(queries)):
        best_ids[qi] = [records[i].id for i in best_idx[qi] if i >= 0]
    return best_ids
