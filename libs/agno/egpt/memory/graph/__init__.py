"""The temporal graph's write-side pipelines (SM-G group).

Entity extraction is SM-G2 (#100); relation/fact edges are SM-G3 (#101), the semantic subgraph
SM-G7 (#105), and entity resolution SM-G8 (#106). The graph *backend* is an integration
(``egpt/integrations/memory/graph/``); nothing here imports one.
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

__all__ = [
    "ENTITY_KEYS",
    "EXTRACTOR_ID",
    "EntityExtractionPipeline",
    "EntityExtractionReport",
    "EntityExtractor",
    "ExtractedEntities",
    "ExtractedEntity",
    "LLMEntityExtractor",
    "embedding_text",
    "entity_details",
]
