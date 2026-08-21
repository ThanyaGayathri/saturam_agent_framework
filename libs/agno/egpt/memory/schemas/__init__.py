"""Per-tier record representation at the memory seam.

The persisted row schemas themselves live in :mod:`egpt.db.schemas.memory` (SM-C3 #91). This
package holds what the seam needs *around* a row -- how one is built from a
:class:`~egpt.memory.types.MemoryEvent` and projected back into a
:class:`~egpt.memory.types.MemoryRecord` -- and defines no schema of its own.
"""

from egpt.memory.schemas.episodic import (
    EPISODE_KEYS,
    PROVENANCE_KEYS,
    TOPICS_KEY,
    EpisodicRecord,
    episode_details,
    episode_from_event,
    episode_to_record,
    graph_node_id_for,
)
from egpt.memory.schemas.semantic import (
    FACT_METADATA_KEYS,
    SPO_PROVENANCE_KEYS,
    fact_record,
)

__all__ = [
    "EPISODE_KEYS",
    "FACT_METADATA_KEYS",
    "PROVENANCE_KEYS",
    "SPO_PROVENANCE_KEYS",
    "TOPICS_KEY",
    "EpisodicRecord",
    "episode_details",
    "episode_from_event",
    "episode_to_record",
    "fact_record",
    "graph_node_id_for",
]
