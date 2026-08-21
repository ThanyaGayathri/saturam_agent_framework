# Imported for its registration side effect: in-tree adapters self-register on import, so
# `build_memory_provider` can resolve "co_located" without the caller knowing the module exists.
from egpt.memory.adapters import CoLocatedMemoryProvider, SeedMemoryProvider
from egpt.memory.config import (
    MemoryProviderConfig,
    MemoryProviderConfigError,
    build_memory_provider,
    register_provider,
    registered_providers,
)

# The consolidation scheduler (SM-L1)
from egpt.memory.consolidation import ConsolidationOutcome, ConsolidationWorker
from egpt.memory.episodic import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    DEFAULT_PRUNE_THRESHOLD,
    ImportanceScore,
    ImportanceScorer,
    episode_decay_score,
    half_life_s,
    is_prune_eligible,
    recency_decay,
    reinforce,
    select_for_forgetting,
)

# `forget` (the single-episode archive function) is imported from its submodule directly, not
# re-exported by `egpt.memory.episodic`'s own __init__ -- that package deliberately excludes it to
# avoid binding `egpt.memory.episodic.forget` (the submodule) to the function in its own
# namespace. No such collision exists here: this package has no `egpt.memory.forget` submodule.
from egpt.memory.episodic.forget import forget
from egpt.memory.episodic.relevance import aepisode_relevance, cosine_relevance, episode_relevance
from egpt.memory.episodic.score import (
    DEFAULT_EPISODIC_SCORE_WEIGHTS,
    EpisodicScoreWeights,
    composite_episodic_score,
)
from egpt.memory.episodic.segment import (
    DEFAULT_MAX_TURNS_PER_EPISODE,
    DEFAULT_MIN_TURNS_PER_EPISODE,
    DEFAULT_TIME_GAP_S,
    TIME_GAP_METHOD,
    TURN_CAP_METHOD,
    BoundaryDecision,
    SegmentState,
    segment_events,
    segmenter_version,
    stamp_boundaries,
)
from egpt.memory.learn_stores import RECALL_SLOTS, STORE_TIERS, stores_by_tier, tier_for_store
from egpt.memory.manager import MemoryManager, UserMemory
from egpt.memory.provider import MemoryProvider
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
from egpt.memory.strategies import (
    MemoryOptimizationStrategy,
    MemoryOptimizationStrategyFactory,
    MemoryOptimizationStrategyType,
    SummarizeStrategy,
)
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate, checked_graph, checked_vector
from egpt.memory.tiers import (
    CONSOLIDATION_SOURCE,
    CONSOLIDATION_TARGET,
    DEFAULT_WRITE_TIER,
    PERSISTENT_TIERS,
    TIER_POLICY,
    GraphWriteShape,
    MemoryTier,
    TierPolicy,
    graph_shape_for_tier,
    policy_for_tier,
    routes_to,
    substrates_for_tier,
)
from egpt.memory.types import (
    CONSOLIDATION_KEYS,
    FUSION_KEYS,
    MAX_LIMIT,
    NO_ENTITIES,
    NO_REFLECTION,
    NO_RELATIONS,
    NO_SUPERSEDE,
    SCOPE_SLOTS,
    ConsolidationReport,
    EntityWrite,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemoryScopeRequiredError,
    MemorySlice,
    PartialWriteError,
    ScanCursor,
    Scope,
    Substrate,
    TemporalBounds,
    ValidWindow,
    WriteReceipt,
    consolidation_details,
    entity_extraction_complete,
    entity_extraction_ran,
    escape_key_slot,
    fusion_diagnostics,
    normalize_entity_name,
    require_scope,
)
from egpt.memory.validation import (
    MEMORY_DATA_ACCESS_METHODS,
    assert_scope_isolation,
    reject_duplicate_ids,
    reject_foreign_scopes,
)

# Write-back capture (SM-E2)
from egpt.memory.writeback import (
    ROLE_KEY,
    TOOL_CALL_ERROR_KEY,
    TOOL_CALL_ID_KEY,
    TOOL_NAME_KEY,
    astart_writeback_task,
    episodes_from_events,
    event_from_message,
    event_from_tool_execution,
    events_from_run,
    start_writeback_future,
)

__all__ = [
    "MemoryManager",
    "UserMemory",
    "MemoryOptimizationStrategy",
    "MemoryOptimizationStrategyType",
    "MemoryOptimizationStrategyFactory",
    "SummarizeStrategy",
    # Symbiotic Memory seam envelope (SM-C1)
    "PERSISTENT_TIERS",
    # The CoALA tier model (SM-C2)
    "CONSOLIDATION_SOURCE",
    "CONSOLIDATION_TARGET",
    "DEFAULT_WRITE_TIER",
    "GraphWriteShape",
    "RECALL_SLOTS",
    "STORE_TIERS",
    "TIER_POLICY",
    "TierPolicy",
    "graph_shape_for_tier",
    "policy_for_tier",
    "routes_to",
    "stores_by_tier",
    "tier_for_store",
    "ConsolidationReport",
    "FactWrite",
    "MemoryEvent",
    "MemoryOperationError",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryScopeRequiredError",
    "MemorySlice",
    "MemoryTier",
    "SCOPE_SLOTS",
    "ScanCursor",
    "Scope",
    "Substrate",
    "TemporalBounds",
    "ValidWindow",
    "WriteReceipt",
    # Substrate ports and the helpers a third-party adapter needs (SM-C1, SM-C4)
    "GraphSubstrate",
    "MEMORY_DATA_ACCESS_METHODS",
    "VectorSubstrate",
    "assert_scope_isolation",
    "checked_graph",
    "checked_vector",
    "reject_duplicate_ids",
    "reject_foreign_scopes",
    "require_scope",
    "consolidation_details",
    "entity_extraction_complete",
    "entity_extraction_ran",
    "escape_key_slot",
    "fusion_diagnostics",
    "substrates_for_tier",
    # The seam and its construction path (SM-C1)
    "CONSOLIDATION_KEYS",
    "CoLocatedMemoryProvider",
    "EntityWrite",
    "FUSION_KEYS",
    "MAX_LIMIT",
    "NO_ENTITIES",
    "NO_REFLECTION",
    "NO_RELATIONS",
    "NO_SUPERSEDE",
    "normalize_entity_name",
    "MemoryProvider",
    "PartialWriteError",
    "MemoryProviderConfig",
    "MemoryProviderConfigError",
    "SeedMemoryProvider",
    "build_memory_provider",
    "register_provider",
    "registered_providers",
    # The episode representation (SM-E1)
    "EPISODE_KEYS",
    "PROVENANCE_KEYS",
    "TOPICS_KEY",
    "EpisodicRecord",
    "episode_details",
    "episode_from_event",
    "episode_to_record",
    "graph_node_id_for",
    # Episodic recency-decay scoring (SM-E4)
    "DEFAULT_BASE_HALF_LIFE_S",
    "DEFAULT_IMP_FACTOR",
    "episode_decay_score",
    "half_life_s",
    "recency_decay",
    "reinforce",
    # Episode salience scoring (SM-E5)
    "ImportanceScore",
    "ImportanceScorer",
    # Inline event segmentation -- the SM-R6 v1 prior (SM-E3)
    "DEFAULT_MAX_TURNS_PER_EPISODE",
    "DEFAULT_MIN_TURNS_PER_EPISODE",
    "DEFAULT_TIME_GAP_S",
    "TIME_GAP_METHOD",
    "TURN_CAP_METHOD",
    "BoundaryDecision",
    "SegmentState",
    "segment_events",
    "segmenter_version",
    "stamp_boundaries",
    # Write-back capture (SM-E2)
    "ROLE_KEY",
    "TOOL_CALL_ERROR_KEY",
    "TOOL_CALL_ID_KEY",
    "TOOL_NAME_KEY",
    "event_from_message",
    "event_from_tool_execution",
    "events_from_run",
    "episodes_from_events",
    "start_writeback_future",
    "astart_writeback_task",
    # Episodic relevance scoring (SM-E6)
    "aepisode_relevance",
    "cosine_relevance",
    "episode_relevance",
    # Composite episodic scoring (SM-E7)
    "DEFAULT_EPISODIC_SCORE_WEIGHTS",
    "EpisodicScoreWeights",
    "composite_episodic_score",
    # Episode forgetting / pruning (SM-E9)
    "DEFAULT_PRUNE_THRESHOLD",
    "forget",
    "is_prune_eligible",
    "select_for_forgetting",
    # The consolidation scheduler (SM-L1)
    "ConsolidationOutcome",
    "ConsolidationWorker",
]
