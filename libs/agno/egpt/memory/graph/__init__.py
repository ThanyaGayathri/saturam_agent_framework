"""The temporal graph's shared shapes and write-side pipelines (SM-G group).

Episode-to-entity mention links are SM-G6 (#104); entity extraction is SM-G2 (#100);
relation/fact edges are SM-G3 (#101), the semantic subgraph SM-G7 (#105), and entity resolution
SM-G8 (#106). The graph *backend* is an integration (``egpt/integrations/memory/graph/``);
nothing here imports one.
"""

from egpt.memory.graph.entities import (
    ENTITY_KEYS,
    EXTRACTOR_ID,
    EntityExtractionPipeline,
    EntityExtractionReport,
    EntityExtractor,
    ExtractedEntities,
    ExtractedEntity,
    LLMEntityExtractor,
    embedding_text,
    entity_details,
)
from egpt.memory.graph.episodic import (
    MENTIONS,
    entity_node_id_for,
    entity_node_id_prefix,
    mention_link_id,
    mention_rows,
    normalized_mentions,
    require_recall_filters,
    require_recall_limit,
    require_time_window,
)

__all__ = [
    "ENTITY_KEYS",
    "EXTRACTOR_ID",
    "EntityExtractionPipeline",
    "EntityExtractionReport",
    "EntityExtractor",
    "ExtractedEntities",
    "ExtractedEntity",
    "LLMEntityExtractor",
    "MENTIONS",
    "embedding_text",
    "entity_details",
    "entity_node_id_for",
    "entity_node_id_prefix",
    "mention_link_id",
    "mention_rows",
    "normalized_mentions",
    "require_recall_filters",
    "require_recall_limit",
    "require_time_window",
]
