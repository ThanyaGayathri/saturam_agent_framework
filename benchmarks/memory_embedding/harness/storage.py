"""Storage / index cost model for the memory vector tier (SM-R4 <-> SM-R3).

The chosen embedding dimension directly drives the pgvector storage and index
footprint, which is why SM-R4 is coupled to the SM-R3 vector-store decision.
This module turns a dimension into concrete bytes at the provisional 10M-memory
scale so DECISION.md can quantify the trade-off.

pgvector constraints (from SM-R3 seed context):
  - `vector`  column: float32, max 2000 dimensions
  - `halfvec`  column: float16, max 4000 dimensions
An HNSW index roughly duplicates the raw vector bytes and adds graph link
overhead; we use a widely-cited ~1.5x-2x multiplier as an order-of-magnitude
estimate (index is measured for real in SM-R3, not here).
"""

from __future__ import annotations

from dataclasses import dataclass

PGVECTOR_MAX_DIM = 2000
HALFVEC_MAX_DIM = 4000
HNSW_INDEX_MULTIPLIER = 1.8  # rough: graph links + neighbour lists over raw bytes


@dataclass
class StorageEstimate:
    dim: int
    n_vectors: int
    storage_type: str  # "vector" (float32) or "halfvec" (float16)
    bytes_per_component: int
    raw_gb: float
    index_gb: float
    total_gb: float
    fits_vector: bool
    fits_halfvec: bool


def estimate(
    dim: int, n_vectors: int = 10_000_000, prefer_halfvec: bool = False
) -> StorageEstimate:
    fits_vector = dim <= PGVECTOR_MAX_DIM
    fits_halfvec = dim <= HALFVEC_MAX_DIM

    use_halfvec = prefer_halfvec or not fits_vector
    bytes_per = 2 if use_halfvec else 4
    storage_type = "halfvec" if use_halfvec else "vector"

    raw_bytes = dim * bytes_per * n_vectors
    raw_gb = raw_bytes / (1024**3)
    index_gb = raw_gb * HNSW_INDEX_MULTIPLIER
    total_gb = raw_gb + index_gb

    return StorageEstimate(
        dim=dim,
        n_vectors=n_vectors,
        storage_type=storage_type,
        bytes_per_component=bytes_per,
        raw_gb=round(raw_gb, 2),
        index_gb=round(index_gb, 2),
        total_gb=round(total_gb, 2),
        fits_vector=fits_vector,
        fits_halfvec=fits_halfvec,
    )
