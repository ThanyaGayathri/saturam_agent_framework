"""The temporal graph's shared shapes and write-side pipelines (SM-G group).

Episode-to-entity mention links are SM-G6 (#104); entity extraction is SM-G2 (#100); entity
resolution is SM-G8 (#106); relation/fact edges are SM-G3 (#101) and the semantic subgraph SM-G7
(#105). The graph *backend* is an integration (``egpt/integrations/memory/graph/``); nothing here
imports one.
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
from egpt.memory.graph.resolve import (
    DEFAULT_MATCH_LIMIT,
    DEFAULT_MIN_SCORE,
    RESOLUTION_KEYS,
    RESOLVER_ID,
    EntityResolutionPipeline,
    EntityResolutionReport,
    resolution_details,
)

__all__ = [
    "DEFAULT_MATCH_LIMIT",
    "DEFAULT_MIN_SCORE",
    "ENTITY_KEYS",
    "EXTRACTOR_ID",
    "EntityExtractionPipeline",
    "EntityExtractionReport",
    "EntityExtractor",
    "EntityResolutionPipeline",
    "EntityResolutionReport",
    "ExtractedEntities",
    "ExtractedEntity",
    "LLMEntityExtractor",
    "MENTIONS",
    "RESOLUTION_KEYS",
    "RESOLVER_ID",
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
    "resolution_details",
]
