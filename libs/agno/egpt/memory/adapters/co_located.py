"""A ``MemoryProvider`` composing both substrate ports (SM-C1).

Straight-line composition: fan a write out to the substrates the tier routes to, merge two read
legs into one slice, distil episodes into facts. It holds no backend code.

Two post-conditions are re-checked here rather than trusted to a backend, because both fail
silently and one puts another tenant's data into a prompt: tenant isolation and bi-temporal
validity.

Out of scope, deliberately: a consolidation scheduler (SM-L1 #122). Rank fusion beyond the default
score-sum is SM-H2's (#132), wired -- see ``_fusion_method``/``_merged`` and
``egpt.memory.retrieval.fuse``; the *default* stays score-sum until SM-H2/H3 measure the 3-leg
pairing against SM-G9's (#107) real graph retriever (ADR 0003 A.6). **Retiring a fact whose source
group changed** is SM-L3's (#124)
``egpt.memory.consolidation.extract.FactExtractionPipeline`` -- opt-in via ``fact_pipeline``, same
shape as ``entity_pipeline``/``relation_pipeline``; ``_distil``'s deterministic dedup below remains
the zero-config default and still cannot retire what it subsumes (its own docstring).
``consolidate`` here is scan -> filter -> distil -> write, and the report names what it does not do.

``consolidate`` carries an in-process resumption cursor (SM-L1 #122) so repeated calls jointly
drain a tier larger than one pass -- see ``__init__``'s ``_scan_cursors`` and ``_advance_cursor``.
The cursor is not part of the public signature and not durable: a fresh instance starts every scope
from the beginning, which is safe because a re-run upserts the same fact ids rather than duplicating
them, just wasted work.

Every tier decision this file makes -- which substrates a write reaches, whether a graph write is
an episode or a fact, which tier consolidation reads and which it writes -- is read from
:data:`egpt.memory.tiers.TIER_POLICY` (SM-C2 #90). None of it is decided here.
"""

import asyncio
import inspect
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Awaitable, Dict, List, Optional, Sequence, Set, Tuple, TypeVar, Union
from uuid import uuid4, uuid5

from egpt.memory.config import (
    MemoryProviderConfig,
    MemoryProviderConfigError,
    register_provider,
    resolve_substrate,
)
from egpt.memory.consolidation import FACT_ID_NAMESPACE, merge_near_duplicate_facts
from egpt.memory.consolidation.extract import STRATEGY_ID as FACT_PIPELINE_STRATEGY_ID
from egpt.memory.consolidation.provenance import provenance_report
from egpt.memory.graph.episodic import entity_node_id_for, normalized_mentions
from egpt.memory.metrics import get_default_aggregator, record_growth
from egpt.memory.provider import MemoryProvider
from egpt.memory.retrieval.fuse import DEFAULT_RRF_K, deterministic_rank_key, rrf_fuse
from egpt.memory.retrieval.orchestrator import RetrievalLegs, aorchestrate, orchestrate
from egpt.memory.retrieval.rerank import (
    DEFAULT_BUDGET_MS as DEFAULT_RERANK_BUDGET_MS,
)
from egpt.memory.retrieval.rerank import (
    DEFAULT_CANDIDATES as DEFAULT_RERANK_CANDIDATES,
)
from egpt.memory.retrieval.rerank import arerank_records, rerank_records
from egpt.memory.schemas.semantic import fact_record
from egpt.memory.security.pii import (
    pii_guardrail_for_config,
    redact_events_for_writeback,
    redact_records_for_writeback,
    redact_text_for_writeback,
)
from egpt.memory.semantic.profile import SemanticProfileStore
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate, checked_graph, checked_vector
from egpt.memory.tiers import (
    CONSOLIDATION_SOURCE,
    CONSOLIDATION_TARGET,
    GraphWriteShape,
    MemoryTier,
    graph_shape_for_tier,
    policy_for_tier,
    routes_to,
    substrates_for_tier,
)
from egpt.memory.types import (
    NO_ENTITIES,
    NO_PROFILE,
    NO_REFLECTION,
    NO_RELATIONS,
    NO_SUMMARY,
    NO_SUPERSEDE,
    PROFILE_NO_USER,
    ConsolidationReport,
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
    ValidWindow,
    WriteReceipt,
    consolidation_details,
    fusion_diagnostics,
    require_scope,
)
from egpt.memory.validation import (
    assert_scope_isolation,
    group_facts_by_scope,
    reject_duplicate_ids,
    reject_foreign_scopes,
)
from egpt.tracing.memory import memory_span
from egpt.utils.dttm import now_epoch_s
from egpt.utils.log import log_warning

if TYPE_CHECKING:
    from egpt.learn.stores.user_profile import UserProfileStore
    from egpt.memory.consolidation.extract import FactExtractionPipeline
    from egpt.memory.consolidation.reflect import ReflectionPipeline
    from egpt.memory.consolidation.summarize import SummaryPipeline
    from egpt.memory.graph.entities import EntityExtractionPipeline
    from egpt.memory.graph.relations import RelationExtractionPipeline
    from egpt.models.message import Message

__all__ = ["CoLocatedMemoryProvider"]

#: Jaccard threshold above which two episodes are treated as the same thing.
#: Deterministic and crude on purpose; real extraction is SM-L3's (#124). What punctuation-only
#: differences slip past this raw tokenizer, ``consolidation.dedup.merge_near_duplicate_facts``
#: (SM-L4 #125) folds after ``_distil`` builds the fact group -- see that module's docstring.
_DUPLICATE_THRESHOLD = 0.8

#: Namespace for deriving a fact id from its source episodes, so a re-run upserts the same row.
#: Defined in ``consolidation.dedup``, not here: that module re-derives a folded fact's id from
#: its combined provenance using this same namespace (review finding 3, PR #116), so importing it
#: rather than keeping a second copy is what keeps the two derivations from drifting apart.
_FACT_NAMESPACE = FACT_ID_NAMESPACE

#: ``derived_by`` for `_distil`'s deterministic dedup -- one constant rather than the bare string
#: literal repeated at every site that names this strategy, so a rename cannot miss one.
_DETERMINISTIC_DEDUP_STRATEGY_ID = "co_located.deterministic_dedup"

#: Cap on simultaneous backend calls in one gathered write. Unbounded fan-out against a real
#: engine is thousands of concurrent round trips, which exhausts a pool rather than going fast.
_MAX_CONCURRENT_WRITES = 16

#: Cap on one consolidation pass. When it is hit the report says ``complete=False``, and the next
#: call to ``consolidate`` on this instance resumes from ``_scan_cursors`` (SM-L1 #122) rather than
#: re-reading the same window.
_MAX_EPISODES_PER_PASS = 5_000

#: What ``ConsolidationReport.details["entities"]`` holds: an ``entity_details`` payload from a
#: wired pipeline, or the ``NO_ENTITIES`` sentinel string from an unwired one. Named rather than
#: left ``Any`` (review finding on PR #89) -- ``Any`` suppresses static analysis on every caller and
#: hides the ``isinstance(..., dict)`` branch a consumer needs before reading a pipeline field.
EntitySummary = Union[Dict[str, Any], str]

#: The ``details["relations"]`` counterpart to :data:`EntitySummary` (SM-G3 #101), same two shapes
#: for the same reason: a ``relation_details`` payload from a wired pipeline, or
#: :data:`~egpt.memory.types.NO_RELATIONS` from an unwired one.
#:
#: ``details["relations"]`` itself is carried as a free extra key on ``consolidation_details``
#: (explicitly documented as allowed), not promoted to the ``types.CONSOLIDATION_KEYS`` floor --
#: that floor is shared by every provider this seam will ever grow, and widening it is a
#: deliberate, separately-reviewable decision this ticket does not make.
RelationSummary = Union[Dict[str, Any], str]

#: The ``details["reflection"]`` counterpart to :data:`EntitySummary` (SM-L6 #127): a
#: ``reflection_details`` payload from a wired pipeline, or
#: :data:`~egpt.memory.types.NO_REFLECTION` from an unwired one. Same free-extra-key treatment as
#: :data:`RelationSummary` -- not a ``types.CONSOLIDATION_KEYS`` floor member.
ReflectionSummary = Union[Dict[str, Any], str]

#: The ``details["profile"]`` counterpart to :data:`EntitySummary` (SM-S3 #120): a small dict from
#: a wired profile store, or :data:`~egpt.memory.types.NO_PROFILE` from an unwired one. Same
#: free-extra-key treatment as :data:`RelationSummary`/:data:`ReflectionSummary`.
ProfileSummary = Union[Dict[str, Any], str]

#: The ``details["summary"]`` counterpart to :data:`EntitySummary` (SM-L8 #129): a
#: ``summary_details`` payload from a wired pipeline, or :data:`~egpt.memory.types.NO_SUMMARY` from
#: an unwired one. Same free-extra-key treatment as :data:`RelationSummary`/:data:`ReflectionSummary`
#: -- not a ``types.CONSOLIDATION_KEYS`` floor member.
SummarySummary = Union[Dict[str, Any], str]


def summary_failed_details(exc: BaseException) -> Dict[str, Any]:
    """The ``details["summary"]`` shape for a pass whose summarization call raised.

    Same minimal-literal treatment :func:`reflection_failed_details` gives its own pipeline, for
    the same reason (``summarize.py`` stays duck-typed here, not imported at runtime).
    """
    return {
        "summarizer": None,
        "complete": False,
        "session": False,
        "day": False,
        "global": False,
        "error": str(exc),
    }


def reflection_failed_details(exc: BaseException) -> Dict[str, Any]:
    """The ``details["reflection"]`` shape for a pass whose reflection call raised.

    Carries the same three keys :func:`~egpt.memory.consolidation.reflect.reflection_details`
    would (``reflect.py`` is not imported here at runtime -- see the module docstring's reasoning
    for keeping ``reflection_pipeline`` duck-typed -- so this is a local, minimal literal rather
    than a shared builder). ``reflector`` is ``None`` rather than a guessed producer id: nothing
    was actually produced. ``error`` names why, for a scheduler or operator reading the report
    without needing to grep logs.

    ``triggered`` reads ``exc.reflection_triggered`` (:meth:`ReflectionPipeline.run`'s docstring)
    rather than being hard-coded -- an earlier revision hard-coded ``False`` here for every caught
    exception, which was correct for a pre-threshold failure (``require_scope``, ``_accumulate``)
    but wrong for the far more common case: a failure from ``synthesize``/``asynthesize`` itself,
    which cannot happen until *after* the trigger has already fired. Reporting ``triggered=False``
    on that path told a downstream scheduler "still building toward threshold" when the threshold
    had in fact already crossed and the pipeline's own trigger was left armed, waiting on a retry.
    ``getattr`` defaults to ``False`` for an exception that never passed through that attribute
    assignment -- true of every pre-threshold failure, and of any exception a non-``reflect.py``
    caller of this function might pass.
    """
    return {
        "reflector": None,
        "complete": False,
        "triggered": bool(getattr(exc, "reflection_triggered", False)),
        "error": str(exc),
    }


def _positive_int(value: Any, option: str) -> int:
    """Validate a bound that reaches a substrate, at construction rather than three frames down.

    Both bounds arrive through ``config.options``, so they are user-supplied and get the same
    boundary treatment as everything else in this package. ``0`` is the case that matters:
    ``max_episodes_per_pass=0`` scans nothing forever while reporting a tier that needs draining,
    so a scheduler retries for eternity and nothing is ever consolidated.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MemoryProviderConfigError(
            f"{option} must be an integer >= 1, got {value!r}. Zero would scan nothing while "
            "reporting the tier as undrained, which a scheduler retries forever.",
            field_name=f"options.{option}",
        )
    return value


#: Carries the candidate's own type through to the return type, so ``self.entity_pipeline``,
#: ``self.relation_pipeline`` and ``self.fact_pipeline`` keep their concrete
#: `Optional["EntityExtractionPipeline"]` / `Optional["RelationExtractionPipeline"]` /
#: `Optional["FactExtractionPipeline"]` annotations at the call sites below instead of all three
#: widening to `Optional[Any]` -- which would silence mypy on a wrong argument type or a missing
#: method on any of them.
_P = TypeVar("_P")


def _checked_pipeline(
    candidate: Optional[_P], *, option: str, verbs: Tuple[str, str] = ("run", "arun")
) -> Optional[_P]:
    """Verify an injected extraction pipeline carries both halves, at construction time.

    Shared by ``entity_pipeline``, ``relation_pipeline`` (SM-G3 #101) and ``fact_pipeline``
    (SM-L3 #124): the same bargain :func:`egpt.memory.substrates.checked_graph` strikes,
    generalised over which option is being checked so the pipelines cannot drift on this rule.
    Names only, but it turns "consolidate mysteriously stopped extracting" or an
    ``AttributeError`` deep inside a pass into a build-time error. ``None`` passes through -- a
    provider without a given extraction pass is the default, not a misconfiguration.

    ``verbs`` defaults to entity/relation's ``run``/``arun``; ``fact_pipeline`` names its own
    (``distil``/``adistil``) because it returns writes rather than performing them (module
    docstring difference: see ``FactExtractionPipeline``).

    Method presence only, like that same bargain -- a candidate with the right names and wrong
    behaviour (a bad model, a broken embedder passed through) still passes here and fails at the
    first ``consolidate``/``aconsolidate`` call, not at construction.
    """
    if candidate is None:
        return None
    missing = [verb for verb in verbs if not callable(getattr(candidate, verb, None))]
    if missing:
        raise MemoryProviderConfigError(
            f"options[{option!r}] is a {type(candidate).__name__}, which is missing "
            f"{missing}. consolidate and aconsolidate each drive one half; a pipeline with only "
            "the sync verb blocks the async lane's event loop.",
            field_name=f"options.{option}",
        )
    return candidate


def _tokens(text: str) -> Set[str]:
    return set(text.lower().split())


def _jaccard(left: Set[str], right: Set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def _valid_at(record: MemoryRecord, at: int) -> bool:
    """``t_valid <= at < t_invalid``, with ``None`` open at either end."""
    return ValidWindow.at(at).overlaps(record.t_valid, record.t_invalid)


def _merge(legs: Sequence[Sequence[MemoryRecord]]) -> List[MemoryRecord]:
    """One ranked list from two legs: dedup by id, sum scores, rank deterministically.

    A score sum, and the provider's default: ADR 0003 A.6 holds the 3-leg (+graph) fusion choice
    open, gated on SM-G9's (#107) real graph retriever and SM-H2/H3's own measurement, so nothing
    here may switch the default without one. RRF is the alternative a caller opts into via
    ``config.fusion`` -- see
    :func:`egpt.memory.retrieval.fuse.rrf_fuse` and :meth:`CoLocatedMemoryProvider._merged`.

    Ties break the same way every fusion strategy in this package does -- see
    :func:`~egpt.memory.retrieval.fuse.deterministic_rank_key` -- never on arrival order, which is
    driven by UUID record ids and made identical corpora rank differently across processes.

    Bounds are **not** merged between the legs here. They used to be, but
    ``_reconcile_bounds`` runs straight after and overwrites every id the graph knows from the
    authority itself -- and any id a graph leg contributed is by definition an id the graph knows.
    Peer-merging was unreachable, and an unreachable invariant is one nothing can test.
    """
    merged: Dict[str, MemoryRecord] = {}
    for records in legs:
        seen: Set[str] = set()
        for record in records:
            if record.id in seen:
                continue  # one leg contributes one score per id
            seen.add(record.id)
            existing = merged.get(record.id)
            if existing is None:
                merged[record.id] = record.detached()
                continue
            existing.score = (existing.score or 0.0) + (record.score or 0.0)
            existing.substrate = None  # no longer from one place
    return sorted(merged.values(), key=deterministic_rank_key)


class CoLocatedMemoryProvider(MemoryProvider):
    """A provider over co-located vector and graph substrates.

    Either substrate may be absent -- a vector-only tenant is supported (SM-C5 #93) and
    ``substrates()`` reports which are wired. Both absent fails at construction.
    """

    provider_key = "co_located"

    def __init__(
        self,
        vector: Optional[VectorSubstrate] = None,
        graph: Optional[GraphSubstrate] = None,
        config: Optional[MemoryProviderConfig] = None,
        max_episodes_per_pass: int = _MAX_EPISODES_PER_PASS,
        max_concurrent_writes: int = _MAX_CONCURRENT_WRITES,
        entity_pipeline: Optional["EntityExtractionPipeline"] = None,
        relation_pipeline: Optional["RelationExtractionPipeline"] = None,
        reflection_pipeline: Optional["ReflectionPipeline"] = None,
        fact_pipeline: Optional["FactExtractionPipeline"] = None,
        profile_store: Optional["UserProfileStore"] = None,
        summary_pipeline: Optional["SummaryPipeline"] = None,
    ) -> None:
        if vector is None and graph is None:
            raise MemoryProviderConfigError(
                "CoLocatedMemoryProvider needs at least one substrate; both are unset.",
                field_name="provider",
            )
        self.vector = vector
        self.graph = graph
        #: SM-G2's (#100) entity extraction, run by ``consolidate`` and by nothing on the read path.
        #: ``None`` by default: extraction is an LLM pass per episode, so a provider must be asked
        #: for it rather than start paying for it on an upgrade.
        #:
        #: Duck-typed for the same reason ``close`` is -- the type lives in ``memory.graph``, which
        #: pulls in the model and learn stacks, and every ``import egpt.memory`` would pay for it.
        self.entity_pipeline = _checked_pipeline(entity_pipeline, option="entity_pipeline")
        #: SM-G3's (#101) relation extraction, run by ``consolidate`` right after entity
        #: extraction. Same duck-typed, opt-in shape as ``entity_pipeline`` and the same reason:
        #: an LLM pass per episode that a provider must be asked for, not upgraded into.
        self.relation_pipeline = _checked_pipeline(relation_pipeline, option="relation_pipeline")
        #: SM-L6's (#127) importance-gated reflection, run by ``consolidate`` right after entity
        #: and relation extraction. Same duck-typed, opt-in shape and the same reason: an LLM pass
        #: a provider must be asked for, not upgraded into -- and unlike those two, it produces
        #: ``FactWrite`` candidates the caller writes through the same SEMANTIC path as a
        #: distilled fact, rather than writing to the graph itself (see ``reflect.py``'s docstring).
        self.reflection_pipeline = _checked_pipeline(reflection_pipeline, option="reflection_pipeline")
        #: SM-L3's (#124) mem0-style extract-and-merge, replacing ``_distil``'s deterministic
        #: dedup for the pass it runs on. ``None`` by default for the same reason as the two
        #: pipelines above: extraction and the merge decision are each an LLM pass, so a provider
        #: must be asked for them rather than start paying for it on an upgrade. Unlike those two,
        #: this pipeline's writes flow through the same fact-write path ``_distil`` feeds --
        #: see ``consolidate``.
        self.fact_pipeline = _checked_pipeline(fact_pipeline, option="fact_pipeline", verbs=("distil", "adistil"))
        #: SM-S3's (#120) structured user-profile extraction, run by ``consolidate`` right after
        #: entity and relation extraction. Same duck-typed, opt-in shape as the pipelines above,
        #: for the same reason: an LLM pass a provider must be asked for, not upgraded into.
        #: Unlike them, this reuses ``egpt.learn.stores.user_profile.UserProfileStore`` directly
        #: rather than a bespoke pipeline type -- the ticket's own scope is to reuse that store's
        #: schema and extraction, not fork a fourth extractor shape.
        self.profile_store = _checked_pipeline(
            profile_store, option="profile_store", verbs=("extract_profile", "aextract_profile")
        )
        #: Where a wired profile update actually lands: the vector substrate, tenant/user-scoped
        #: through the same ``Scope`` every other semantic-tier write already gets (SM-C4 #92).
        #: Built here rather than injected: it is a thin wrapper over ``self.vector``, which the
        #: provider already owns, so a second injection point for the identical substrate would
        #: only invite the two to drift.
        #:
        #: ``schema=`` is read off ``self.profile_store`` (``getattr``, not an attribute access
        #: that would break a duck-typed double lacking one), not defaulted to the base
        #: ``UserProfile`` -- a wired store carrying an extended schema (e.g. a subclass adding
        #: `company`) previously had its extra fields silently dropped on every read through this
        #: provider (`get_profile`, and `_update_profile`'s own existing-profile read, which feeds
        #: the LLM's "current values" context on every consolidation pass) because this store was
        #: built with the default schema regardless of what was actually wired (QC round 4, PR
        #: #135). ``None`` (no store wired) falls through to ``SemanticProfileStore``'s own default.
        profile_schema = getattr(self.profile_store, "schema", None)
        self._semantic_profile = (
            SemanticProfileStore(vector=self.vector, schema=profile_schema)
            if profile_schema is not None
            else SemanticProfileStore(vector=self.vector)
        )
        #: SM-L8's (#129) hierarchical (session/day/global) summarization, run by ``consolidate``
        #: right after reflection. Same duck-typed, opt-in shape as the pipelines above, and the
        #: same reason: an LLM pass a provider must be asked for, not upgraded into -- and like
        #: reflection, it produces ``FactWrite`` candidates the caller writes through the same
        #: SEMANTIC path as a distilled fact (see ``summarize.py``'s docstring).
        self.summary_pipeline = _checked_pipeline(summary_pipeline, option="summary_pipeline")
        self.config = config or MemoryProviderConfig(provider=self.provider_key)
        #: Ceiling on one consolidation pass. Adjustable because the default is a safety bound, not
        #: a policy: a tier larger than this has its tail consolidated over multiple passes, tracked
        #: by ``_scan_cursors`` below.
        self.max_episodes_per_pass = _positive_int(max_episodes_per_pass, "max_episodes_per_pass")
        #: Cap on simultaneous per-record backend calls in one async write.
        self.max_concurrent_writes = _positive_int(max_concurrent_writes, "max_concurrent_writes")
        #: The resumption cursor SM-L1 (#122) brings, keyed by ``(scope.key(), tier)``. Lives on the
        #: instance, not the substrate: it is how repeated ``consolidate`` calls jointly drain a
        #: tier larger than one pass without adding a parameter to the public method (the interface
        #: docstring is explicit that a caller-facing cursor was tried and failed three times).
        #: Not durable across a process restart -- a fresh instance rescans from the beginning,
        #: which costs redundant work but not correctness, because a fact id is derived from its
        #: source episodes.
        #: TODO(SM-L1x, aicoe-tracker #189): grows with the active scope set and is never
        #: evicted -- one entry per (scope, tier) pair ever consolidated on this instance, for
        #: the instance's lifetime. Bounded per-deployment today by how many distinct scopes one
        #: process consolidates (today, ``ConsolidationWorker``'s own configured scope list); a
        #: high-cardinality scope set (many users x many tiers, over a long-lived process) would
        #: want a bounded LRU or eviction on idle rather than this plain dict. Should land before
        #: SM-L9 wires this worker into a multi-tenant cron scheduler.
        self._scan_cursors: Dict[Tuple[str, MemoryTier], ScanCursor] = {}

    def substrates(self) -> Dict[Substrate, bool]:
        return {Substrate.VECTOR: self.vector is not None, Substrate.GRAPH: self.graph is not None}

    def tiers(self) -> Dict[MemoryTier, bool]:
        """A tier is served when a wired substrate stores it, so a vector-only provider serves
        three of the four and a graph-only one serves two."""
        return {tier: bool(substrates_for_tier(tier) & self._wired()) for tier in MemoryTier}

    def get_profile(self, scope: Scope) -> Optional[Any]:
        """The user's structured profile (SM-S3 #120), or ``None`` when unwired, never
        consolidated, or unparseable.

        Not part of ``MemoryProvider``'s abstract interface -- ``read``/``read_tier`` are
        embedding-relevance search over a tier's ``content``, not a lookup by kind, and a
        profile's fixed placeholder content (``f"user profile for {scope.user_id}"``) is not
        guaranteed to surface for an arbitrary query even if it did route there. Adding an
        abstract verb to ``MemoryProvider`` would force every other implementor (including one
        with no notion of a profile) to answer it; a concrete method on this adapter, over the
        ``_semantic_profile`` it already owns, is the narrower change (QC round 3, PR #135 --
        before this, the only way to reach a stored profile was the private attribute directly).
        """
        return self._semantic_profile.get(scope)

    async def aget_profile(self, scope: Scope) -> Optional[Any]:
        """Async twin of :meth:`get_profile`, making the same decisions."""
        return await self._semantic_profile.aget(scope)

    def close(self) -> None:
        """Close whichever substrates hold something. Duck-typed, because the ports do not name it.

        Adding ``close`` to ``VectorSubstrate`` / ``GraphSubstrate`` would make every in-memory
        double implement an empty method to satisfy a Protocol, so a backend that has nothing to
        release simply does not have the attribute.
        """
        failures: List[BaseException] = []
        for substrate in (self.vector, self.graph):
            closer = getattr(substrate, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as exc:  # noqa: PERF203 - every substrate gets closed regardless
                    failures.append(exc)
        self._raise_close_failure(failures)

    @staticmethod
    def _raise_close_failure(failures: Sequence[BaseException]) -> None:
        """Close everything, then report. Shared so neither half can stop at the first failure.

        Latent while the vector substrate holds nothing, and exactly the kind of latent that stops
        being latent: SM-V1 (#95) brings a second connection pool, and a raising vector closer would
        otherwise leave the graph pool open with nothing to reopen it.
        """
        if failures:
            raise failures[0]

    async def aclose(self) -> None:
        failures: List[BaseException] = []
        for substrate in (self.vector, self.graph):
            closer = getattr(substrate, "aclose", None) or getattr(substrate, "close", None)
            if not callable(closer):
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: PERF203 - as above
                failures.append(exc)
        self._raise_close_failure(failures)

    # -- guards ---------------------------------------------------------------

    def _wired(self) -> Set[str]:
        return {name for name, on in (("vector", self.vector), ("graph", self.graph)) if on is not None}

    def _require_writable(self, tier: MemoryTier) -> None:
        """Refuse a write no *wired* substrate would store, rather than returning a success receipt."""
        policy = policy_for_tier(tier)
        routed = policy.substrates
        if not routed:
            raise MemoryOperationError(
                f"Tier {tier.value!r} has no persistent substrate; building it out is {policy.owner}.",
                field_name="tier",
            )
        if not (routed & self._wired()):
            raise MemoryOperationError(
                f"No wired substrate serves the {tier.value!r} tier: it routes to {sorted(routed)} "
                f"and this provider has {sorted(self._wired()) or 'none'}.",
                field_name="tier",
            )

    @staticmethod
    def _assert_requested_tiers(query: MemoryQuery, records: Sequence[MemoryRecord]) -> None:
        """A substrate may not return a tier the query did not ask for.

        The provider re-checks scope and validity after the legs return and used to trust tier
        entirely, so a backend ignoring ``resolved_tiers()`` had ``read_tier(SEMANTIC)`` returning
        episodic and procedural rows, faithfully grouped under those tiers in ``by_tier``. The
        existing test asserts the two *legs agree with each other*, which cannot see a failure both
        legs share.
        """
        wanted = set(query.resolved_tiers())
        unwanted = sorted({r.tier.value for r in records if r.tier not in wanted})
        if unwanted:
            raise MemoryOperationError(
                f"A substrate returned tiers {unwanted} that the query did not ask for "
                f"({sorted(t.value for t in wanted)}); a backend ignored the tier filter.",
                field_name="tiers",
            )

    def _require_answerable(self, query: MemoryQuery) -> None:
        """Refuse a read this provider cannot answer correctly.

        Three cases, all refusals rather than empty results: naming a substrate that is not wired,
        excluding the graph when one *is* wired, and asking for a point in time with no graph at
        all. The last two are one rule -- the graph is the authority for validity, so an answer
        assembled without it contains facts the graph has recorded as false.

        The ``as_of`` check is outside the ``substrates`` branch on purpose: a vector-only provider
        names no substrates and still cannot answer a point-in-time read.
        """
        wired = self.substrates()
        if query.as_of is not None and not wired[Substrate.GRAPH] and routes_to("graph", query.resolved_tiers()):
            raise MemoryOperationError(
                "A point-in-time read needs the graph substrate, which is the authority for "
                "bi-temporal validity; none is wired, and the vector projection carries no bounds "
                "so it would return invalidated facts as true.",
                field_name="as_of",
            )
        if query.substrates is None:
            return
        if not query.substrates:
            # Unlike `tiers=[]`, which is a coherent "search no tiers", an empty substrate set is a
            # request nothing could ever answer. Left to fall through it produced three behaviours
            # for one input -- refused with a graph wired, silently empty on a vector-only provider,
            # and refused on the seed -- so the same query meant different things per deployment.
            raise MemoryOperationError(
                "MemoryQuery.substrates is empty, so no substrate would be consulted and the read "
                "could only ever return nothing. Pass None for every wired substrate, or name the "
                "ones you want.",
                field_name="substrates",
            )
        missing = sorted(s.value for s in query.substrates if not wired[s])
        if missing:
            raise MemoryOperationError(
                f"This provider does not have the {missing} substrate(s) the query names.",
                field_name="substrates",
            )
        # Tier-aware: excluding the graph is only wrong for a tier the graph actually stores. A
        # procedural read is vector-only, so the graph has no validity opinion to withhold and
        # refusing it blocks the one read shape where skipping the leg is legitimate.
        graph_backed_tiers = routes_to("graph", query.resolved_tiers())
        if wired[Substrate.GRAPH] and graph_backed_tiers and Substrate.GRAPH not in query.substrates:
            reason = "point-in-time " if query.as_of is not None else ""
            raise MemoryOperationError(
                f"The query excludes the graph from a {reason}read, and the graph is the authority "
                "for bi-temporal validity, so the answer would include facts it has recorded as "
                "false.",
                field_name="substrates",
            )

    @staticmethod
    def _graph_backed(records: Sequence[MemoryRecord]) -> List[str]:
        """Ids whose tier routes to the graph -- the only ones it can have an opinion about.

        A procedural read is vector-only per ``TIER_POLICY``, so asking the graph for its bounds is
        a guaranteed-empty round trip, and a network hop in Mode B.
        """
        return [r.id for r in records if routes_to("graph", [r.tier])]

    def _reconcile_bounds(self, scope: Scope, records: Sequence[MemoryRecord]) -> bool:
        """Overwrite each record's bi-temporal bounds with the graph's, in place.

        Without this the read path serves facts the graph has recorded as **false**. The vector
        projection carries no bounds; under SCD-2 the graph's bound-bearing successor row holds the
        *same content* as the live row, so it scores identically and drops off the graph leg's
        top-``limit`` page as soon as the corpus is larger than the page. Nothing then reaches
        ``_merge`` to tighten against, and the stale row passes ``_valid_at`` labelled current
        belief. Measured: at ``limit=3`` over a six-row corpus, every returned row was invalidated.

        One batched call per read, not per record -- see ``GraphSubstrate.bounds_for_ids``.

        Returns whether every record that *could* carry a graph bound now does. Vacuously ``True``
        when none of them routes to the graph -- a procedural slice is fully authoritative without
        a graph call, and reporting it unreconciled would flag a correct answer as unreliable.
        ``False`` means the opposite: rows the graph might have bounded went unchecked.
        """
        askable = self._graph_backed(records)
        if not askable:
            return True  # vacuous: no returned record routes to the graph, so nothing is stale
        if self.graph is None:
            return False
        bounds = self.graph.bounds_for_ids(scope, askable)
        for record in records:
            found = bounds.get(record.id)
            if found is not None:
                record.apply_bounds(found)
        return True

    async def _areconcile_bounds(self, scope: Scope, records: Sequence[MemoryRecord]) -> bool:
        """Async variant of :meth:`_reconcile_bounds`, returning the same flag."""
        askable = self._graph_backed(records)
        if not askable:
            return True
        if self.graph is None:
            return False
        bounds = await self.graph.abounds_for_ids(scope, askable)
        for record in records:
            found = bounds.get(record.id)
            if found is not None:
                record.apply_bounds(found)
        return True

    # -- write ----------------------------------------------------------------

    def _prepare(self, scope: Scope, events: Sequence[MemoryEvent]) -> Tuple[List[MemoryEvent], List[MemoryRecord]]:
        """Stamp one id per event and project each into the record the vector leg stores.

        The id is stamped once and reused across substrates: it is the cross-substrate dedup key,
        so a second id turns one episode into two results.
        """
        reject_foreign_scopes(scope, events)
        reject_duplicate_ids(events)

        now = now_epoch_s()
        stamped: List[MemoryEvent] = []
        records: List[MemoryRecord] = []
        for event in events:
            target = event.tier
            self._require_writable(target)
            occurred_at = event.occurred_at if event.occurred_at is not None else now
            event_id = event.id or str(uuid4())
            # Validated here rather than only in the graph leg (SM-G6 #104): a vector-only tenant
            # (SM-C5 #93) would otherwise accept a malformed `entities` in silence, and the same
            # batch would be refused or not depending on which substrates happen to be wired.
            entities = normalized_mentions(event.entities, episode_id=event_id)
            prepared = replace(
                event,
                id=event_id,
                tier=target,
                occurred_at=occurred_at,
                # `MemoryEvent.__post_init__` (aicoe-tracker#181) now copies `topics` again once
                # `replace()` reconstructs the event below -- one new redundant copy on this path
                # (this call was already followed by `MemoryRecord.__post_init__`'s own copy
                # pre-#181; `__post_init__`'s is the only one that's new). Left as-is rather than
                # trimmed: if `__post_init__`'s copy is ever made conditional, this explicit one
                # is the only thing standing between `records` (below) and aliasing the caller's
                # original list.
                topics=list(event.topics) if event.topics is not None else None,
                entities=entities if event.entities is not None else None,
                metadata=dict(event.metadata),
            )
            stamped.append(prepared)
            records.append(
                MemoryRecord(
                    id=event_id,
                    tier=target,
                    content=prepared.content,
                    scope=scope,
                    topics=list(prepared.topics) if prepared.topics is not None else None,
                    # The same derivation the graph's `MENTIONS` edges use, so the vector row does
                    # not drop the caller's entities on a deployment with no graph leg. Checking the
                    # two stay in step as the graph changes is `episodic.link.resolve_episode_link`
                    # (SM-E10 #117); this write path stays the single source both sides fan out from.
                    provenance={"entity_ids": [entity_node_id_for(scope, name) for name in entities]},
                    t_valid=occurred_at,
                    created_at=now,
                    metadata=dict(prepared.metadata),
                )
            )
        return stamped, records

    def _store_label(self) -> str:
        """``egpt.memory.store`` value shared by all six ``memory_span`` call sites below."""
        return ",".join(sorted(k.value for k, v in self.substrates().items() if v))

    @staticmethod
    def _write_tier_label(events: Sequence[MemoryEvent]) -> str:
        """``egpt.memory.tier`` value for ``write``/``awrite``."""
        return ",".join(sorted({e.tier.value for e in events}))

    @staticmethod
    def _read_tier_label(query: MemoryQuery) -> str:
        """``egpt.memory.tier`` value for ``read``/``aread``."""
        if query.tiers is None:
            return "all"
        return ",".join(sorted(t.value for t in query.tiers))

    @staticmethod
    def _consolidate_tier_label(tiers: Optional[Sequence[MemoryTier]]) -> str:
        """``egpt.memory.tier`` value for ``consolidate``/``aconsolidate``."""
        if tiers is None:
            return f"{CONSOLIDATION_SOURCE.value}->{CONSOLIDATION_TARGET.value}"
        return ",".join(sorted(t.value for t in tiers))

    @staticmethod
    def _receipt(stamped: Sequence[MemoryEvent]) -> WriteReceipt:
        tiers: List[MemoryTier] = []
        for event in stamped:
            if event.tier not in tiers:
                tiers.append(event.tier)
        return WriteReceipt(
            accepted=len(stamped),
            event_ids=[e.id for e in stamped if e.id is not None],
            tiers_written=tiers,
        )

    @staticmethod
    def _record_write_growth(scope: Scope, stamped: Sequence[MemoryEvent]) -> None:
        record_growth(
            get_default_aggregator(),
            scope,
            "write",
            tier=",".join(sorted({e.tier.value for e in stamped})),
            accepted=len(stamped),
            event_ids=tuple(e.id for e in stamped if e.id is not None),
        )

    @staticmethod
    def _committed_events(
        stamped: Sequence[MemoryEvent],
        vector_records: Sequence[MemoryRecord],
        vector_committed: bool,
        episodes: Sequence[MemoryEvent],
        episodes_committed: bool,
        facts: Sequence[FactWrite],
        facts_committed: bool,
    ) -> List[MemoryEvent]:
        """Which of `stamped` actually landed, given which verb(s) committed.

        A graph-only provider (`vector=None`) can commit episodes while facts fail in the same
        batch, or vice versa -- the two graph verbs are independent, so their successes cannot be
        collapsed into one shared "graph_committed" flag without overcounting growth for whichever
        verb failed. Union by id (not concatenation) because an `EPISODIC` event routes to both the
        vector leg and the episode verb -- counting it twice would overcount growth in the other
        direction.
        """
        committed_ids: Set[str] = set()
        if vector_committed:
            committed_ids.update(r.id for r in vector_records)
        if episodes_committed:
            committed_ids.update(e.id for e in episodes if e.id is not None)
        if facts_committed:
            committed_ids.update(f.record.id for f in facts if f.record.id is not None)
        return [e for e in stamped if e.id in committed_ids]

    @staticmethod
    def _record_fact_growth(scope: Scope, facts: Sequence[FactWrite]) -> None:
        record_growth(
            get_default_aggregator(),
            scope,
            "consolidate",
            tier="episodic->semantic",
            accepted=len(facts),
            event_ids=tuple(f.record.id for f in facts if f.record.id is not None),
        )

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="write")
        # PII containment, write-back boundary (SM-X5 #141): a no-op unless self.config.pii
        # opts this tenant in. Every write-back caller reaches storage through this method (the
        # run loop's write-back hooks, WorkingMemory.offload, a direct provider.write) -- placed
        # here, not one layer up, for the same reason `memory_span` tracing (SM-X4) is per-adapter
        # rather than on the abstract base: this is the actual seam.
        events = redact_events_for_writeback(events, self.config)
        with memory_span(
            "write",
            tenant_id=scope.tenant_id,
            tier=self._write_tier_label(events),
            store=self._store_label(),
        ):
            if not events:
                # Guards a degenerate `MemoryGrowthEntry(tier="", accepted=0)` -- an empty batch
                # has nothing to grow the store by, so it shouldn't record that it did. The span
                # above still opens: an empty write is a legal call this seam must still trace.
                return self._receipt([])
            stamped, records = self._prepare(scope, events)
            vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
            receipt = self._receipt(stamped)
            vector_committed = False
            episodes: List[MemoryEvent] = []
            facts: List[FactWrite] = []
            episodes_committed = False
            facts_committed = False
            try:
                if self.vector is not None and vector_records:
                    self.vector.upsert(scope, vector_records)
                    vector_committed = True
                if self.graph is not None:
                    # Both verbs are attempted, then failures are raised together -- the sync half of
                    # the settle-then-propagate rule `_gather_writes` applies. A refusal is collected
                    # like any other failure rather than short-circuiting: an `except
                    # MemoryOperationError: raise` arm here left the two halves three graph rows apart
                    # for one identical failure, which is the divergence this loop exists to prevent.
                    episodes, facts = self._graph_batches(stamped, records)
                    failures: List[BaseException] = []

                    def attempt(write: Any, batch: Sequence[Any]) -> bool:
                        """One try/except, shared by both verbs. Written twice, they drift."""
                        if not batch:
                            return False
                        try:
                            write(scope, batch)
                            return True
                        except Exception as exc:
                            failures.append(exc)
                            return False

                    episodes_committed = attempt(self.graph.upsert_episodes, episodes)
                    facts_committed = attempt(self.graph.upsert_facts, facts)
                    self._raise_write_failure(failures, receipt)
            except PartialWriteError:
                # `_raise_write_failure`'s own docstring assumes the vector leg has already
                # committed the full batch by the time this raises -- true whenever a provider
                # has a vector substrate, since every graph-routed tier also routes to vector
                # (`TIER_POLICY`). It is *not* true for a graph-only provider (`vector=None` is a
                # valid config, `__init__` only requires one substrate) -- there, this branch can
                # be reached with everything `False` (every attempted graph verb failed), and
                # growth must not be recorded for that. `_committed_events` also protects a
                # mixed-tier batch where one graph verb committed and the other didn't: only the
                # events that verb actually wrote are counted, not the whole batch.
                committed = self._committed_events(
                    stamped, vector_records, vector_committed, episodes, episodes_committed, facts, facts_committed
                )
                if committed:
                    self._record_write_growth(scope, committed)
                raise
            except MemoryOperationError:
                raise  # the vector leg itself refused before anything committed -- nothing grew
            except Exception as exc:
                # Everything else lands here through one of two doors. `self.vector.upsert`
                # raising takes this door too (its own docstring makes it all-or-nothing in one
                # transaction, so any exception there means nothing committed --
                # `vector_committed` stays False). The other door is a bug in
                # `_graph_batches`/`_raise_write_failure`'s plumbing itself (not a substrate
                # failure -- those are caught by `attempt` and turned into `PartialWriteError`
                # above) raised *after* one or both legs already committed, which
                # `_committed_events` reflects.
                committed = self._committed_events(
                    stamped, vector_records, vector_committed, episodes, episodes_committed, facts, facts_committed
                )
                if committed:
                    self._record_write_growth(scope, committed)
                raise PartialWriteError(receipt) from exc
            self._record_write_growth(scope, stamped)
            return receipt

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="awrite")
        events = redact_events_for_writeback(events, self.config)  # SM-X5 #141; see write()'s comment
        with memory_span(
            "write",
            tenant_id=scope.tenant_id,
            tier=self._write_tier_label(events),
            store=self._store_label(),
        ):
            if not events:
                # See the sync half's comment: the span still opens for an empty write.
                return self._receipt([])
            stamped, records = self._prepare(scope, events)
            vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
            receipt = self._receipt(stamped)
            vector_committed = False
            episodes: List[MemoryEvent] = []
            facts: List[FactWrite] = []
            episodes_committed = False
            facts_committed = False
            try:
                if self.vector is not None and vector_records:
                    await self.vector.aupsert(scope, vector_records)
                    vector_committed = True
                if self.graph is not None:
                    # Parity with the sync half comes from *both* attempting every verb, not from
                    # `gather` itself: bare `gather` propagates the first exception without cancelling
                    # its siblings, so they commit regardless. What `return_exceptions=True` adds is
                    # deterministic selection -- a `MemoryOperationError` among the failures must win
                    # over a transport error, or the same batch reports "retry me" on one run and
                    # "this will never work" on the next -- and it stops sibling exceptions becoming
                    # never-retrieved warnings.
                    episodes, facts = self._graph_batches(stamped, records)
                    writes: List[Awaitable[Any]] = []
                    if episodes:
                        writes.append(self.graph.aupsert_episodes(scope, episodes))
                    if facts:
                        writes.append(self.graph.aupsert_facts(scope, facts))
                    if writes:
                        committed_flags, failures = await self._gather_writes(writes)
                        # `committed_flags` lines up with `writes`, built above in episodes-then-facts
                        # order -- unpack positionally rather than zipping against a parallel label
                        # list, since exactly which slots exist depends on which batches were non-empty.
                        i = 0
                        if episodes:
                            episodes_committed = committed_flags[i]
                            i += 1
                        if facts:
                            facts_committed = committed_flags[i]
                        self._raise_write_failure(failures, receipt)
            except PartialWriteError:
                # See the sync half's comment: `_raise_write_failure`'s "vector already
                # committed" assumption does not hold for a graph-only provider, so this must
                # reflect what actually committed, not assume -- and `_committed_events` also
                # protects a mixed-tier batch where one graph verb committed and the other didn't.
                committed = self._committed_events(
                    stamped, vector_records, vector_committed, episodes, episodes_committed, facts, facts_committed
                )
                if committed:
                    self._record_write_growth(scope, committed)
                raise
            except MemoryOperationError:
                raise
            except Exception as exc:
                # See the sync half's comment: `_committed_events` distinguishes "nothing
                # committed" from "one or both legs committed and something else broke after."
                committed = self._committed_events(
                    stamped, vector_records, vector_committed, episodes, episodes_committed, facts, facts_committed
                )
                if committed:
                    self._record_write_growth(scope, committed)
                raise PartialWriteError(receipt) from exc
            self._record_write_growth(scope, stamped)
            return receipt

    @staticmethod
    def _raise_write_failure(failures: Sequence[BaseException], receipt: WriteReceipt) -> None:
        """The one place either half decides what a fan-out failure becomes.

        Shared rather than written twice, because the two halves drifting is precisely the defect
        this keeps producing. Two rules:

        **A refusal wins over a transport error** as the cause, so the same batch does not report
        "retry me" on one run and "this will never work" on the next.

        **It is a ``PartialWriteError`` either way.** By the time a substrate refuses, the vector
        leg has already committed the whole batch -- so "a refusal is not a partial write" was
        false, and raising it bare denied the caller the receipt, whose ids are the only thing
        making the documented retry idempotent. Refusals raised *before* the fan-out, from
        ``_prepare``, still propagate bare: nothing was written, so there is nothing to reconcile.
        The refusal is preserved as ``__cause__``.
        """
        if not failures:
            return
        refusal = next((f for f in failures if isinstance(f, MemoryOperationError)), None)
        raise PartialWriteError(receipt) from (refusal or failures[0])

    async def _gather_writes(self, coroutines: Sequence[Awaitable[Any]]) -> Tuple[List[bool], List[BaseException]]:
        """Run write coroutines concurrently, **settle them all**, and report what happened.

        Bounded by a semaphore as well: `batch` is capped only by `max_episodes_per_pass` and
        `events` is caller-supplied, so an unbounded fan-out is thousands of simultaneous round
        trips against a real engine -- which exhausts a connection pool rather than going fast.

        Returns ``(committed, failures)`` rather than raising itself: a caller needs to know
        *which* coroutine(s) committed (not just whether anything did) so growth reflects exactly
        what landed when a caller fans out more than one independent verb -- e.g. `awrite`'s
        episodes/facts pair, where one can succeed while the other fails in the same call.
        `committed[i]` corresponds to `coroutines[i]`. That's also why this doesn't raise itself:
        the branch that goes on to raise via `_raise_write_failure` still needs `committed` first.
        """
        semaphore = asyncio.Semaphore(self.max_concurrent_writes)

        async def bounded(coroutine: Awaitable[Any]) -> Any:
            async with semaphore:
                return await coroutine

        # Settled, so which failure surfaces is a decision rather than a race.
        results = await asyncio.gather(*(bounded(c) for c in coroutines), return_exceptions=True)
        committed = [not isinstance(r, BaseException) for r in results]
        failures = [r for r in results if isinstance(r, BaseException)]
        return committed, failures

    @staticmethod
    def _graph_batches(
        stamped: Sequence[MemoryEvent], records: Sequence[MemoryRecord]
    ) -> Tuple[List[MemoryEvent], List[FactWrite]]:
        """Split one write into the graph's two verbs. Routing lives here, once, for both halves.

        A semantic memory is a fact, not an episode: routing it through the episode verb produces a
        flat pseudo-episode rather than the entity/relation shape SM-R7 intends (finding F9).
        """
        episodes: List[MemoryEvent] = []
        facts: List[FactWrite] = []
        for event, record in zip(stamped, records):
            # Shaped by `TIER_POLICY`, not by an `is SEMANTIC` test. The table guarantees a
            # graph shape exactly when the tier routes to the graph, so this one lookup also
            # decides whether the event reaches the graph at all.
            shape = graph_shape_for_tier(event.tier)
            if shape is GraphWriteShape.FACT:
                facts.append(FactWrite(record=record, derived_by="co_located.direct_write"))
            elif shape is GraphWriteShape.EPISODE:
                episodes.append(event)
        return episodes, facts

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        # Hoisted, because `_prepare`'s guard is inside the per-event loop: an empty batch never
        # reached it, so `write_tier(WORKING, [])` returned a success receipt for a tier that
        # `write_tier(WORKING, [event])` refuses. `seed.write_tier` already guards up front.
        require_scope(scope, method="write_tier")
        self._require_writable(tier)
        return self.write(scope, [replace(event, tier=tier) for event in events])

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="awrite_tier")
        self._require_writable(tier)
        return await self.awrite(scope, [replace(event, tier=tier) for event in events])

    # -- read -----------------------------------------------------------------

    def _slice(
        self,
        scope: Scope,
        query: MemoryQuery,
        merged: List[MemoryRecord],
        vector_hits: List[MemoryRecord],
        graph_hits: List[MemoryRecord],
        instant: int,
        bounds_reconciled: bool,
        *,
        lexical_hits: List[MemoryRecord],
        rerank_diagnostics: Dict[str, Any],
        fused_count: int,
    ) -> MemorySlice:
        """Filter the reconciled set and package it. Bounds must already be authoritative.

        ``fused_count`` is ``len(merged)`` captured by the caller *before* an optional rerank
        stage (SM-H3 #133) can narrow ``merged`` -- via a reranker's own ``top_n`` -- so that the
        ``fused`` diagnostic below keeps its pre-existing meaning, "how many records the fusion
        stage produced," rather than silently becoming "how many survived reranking" whenever
        rerank is enabled (round-4 review finding). ``dropped_as_superseded`` below is
        deliberately **not** rebased the same way: it answers "of what this filtering pass is
        about to examine, how many got knocked out," which is ``merged`` (the post-rerank pool
        actually being filtered) by definition -- the docstring on that key already documents that
        it and ``fused`` are not expected to reconcile via a sum, for an unrelated reason (the
        `query.limit` truncation), so a rerank-narrowed pool is one more instance of an already-
        acknowledged gap, not a new one.
        """
        # Both predicates, always. `is_current_belief` is transaction time only, and under SCD-2
        # the successor row carries a past `t_invalid` with `expired_at = None` -- so checking only
        # current belief let `read()` and `read(as_of=now)` disagree at the same instant.
        current = [r for r in merged if r.is_current_belief()]
        # A window **replaces** the point predicate rather than stacking with it: they are two
        # spellings of one axis, and intersecting them empties every read about the past, since
        # `instant` is the present whenever `as_of` is unset. `MemoryQuery` refuses both at once,
        # so this is a choice between two given filters and never a merge of them (SM-V4 #98).
        #
        # The rule runs again here even though the substrate already narrowed, because the
        # substrate filtered its own **stale** projection: `_reconcile_bounds` has just overwritten
        # these bounds from the graph, which is the authority, and a window decided before that was
        # decided on the wrong ones. The substrate's pass is what makes this a full page rather
        # than the remains of one; this pass is what makes it correct.
        requested_window = query.window()
        window = requested_window or ValidWindow.at(instant)
        visible = [r for r in current if window.overlaps(r.t_valid, r.t_invalid)]
        records = visible[: query.limit] if query.limit else []

        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in records:
            by_tier.setdefault(record.tier, []).append(record)

        return MemorySlice(
            scope=scope,
            query=query,
            records=records,
            by_tier=by_tier,
            fusion=fusion_diagnostics(
                method=self._fusion_method(),
                vector_hits=len(vector_hits),
                lexical_hits=len(lexical_hits),
                graph_hits=len(graph_hits),
                fused=fused_count,
                returned=len(records),
                # A short result with non-zero hits is ambiguous otherwise: an empty corpus and a
                # page the validity filter emptied look identical. Precision is handled by
                # reconciliation, so what this counts is the recall shortfall named in
                # `VectorSubstrate.search`.
                # Pre-limit, and **valid time only**. Counting the transaction-time drop here too
                # conflated "the world moved on" with "a newer revision exists", which are
                # different diagnoses -- and `bounds_for_ids` is explicitly allowed to report
                # expired rows, so a conforming backend makes the difference observable.
                dropped_by_validity=0 if requested_window else len(current) - len(visible),
                dropped_as_superseded=len(merged) - len(current),
                # The same drop, attributed to the question that was actually asked. One filter
                # runs, so one of these two is always zero -- reporting the count under both would
                # double it, and reporting it only under `dropped_by_validity` would say "the world
                # moved on" about rows the caller deliberately excluded. Non-zero here after the
                # substrate already narrowed means reconciliation tightened a bound.
                #
                # Pre-limit, like `dropped_by_validity` above -- so neither key accounts for rows
                # `visible[:query.limit]` truncated. `returned + dropped_as_superseded +
                # dropped_outside_window` need not sum to `fused`; the remainder passed every
                # filter and lost only to the page size. A `dropped_by_limit` key would close that,
                # and does not exist because nothing here needed it yet.
                dropped_outside_window=len(current) - len(visible) if requested_window else 0,
                # Reported by the reconciliation itself, not inferred from the wiring. Inferring
                # it claimed `True` for a procedural slice, where `_graph_backed` correctly makes
                # no call at all -- the flag would have said a graph vouched for bounds no graph
                # was asked about. `False` means rows the graph might have bounded went unchecked.
                bounds_reconciled=bounds_reconciled,
                instant=instant,
                **rerank_diagnostics,
            ),
        )

    def _fusion_method(self) -> str:
        """The provider's fusion strategy, read from ``config.fusion`` (SM-H2 #132).

        ``score_sum`` is the default and stays the default: ADR 0003 A.6 holds the 3-leg
        weighted-vs-RRF choice open, gated on SM-G9's (#107) real graph retriever and SM-H2/H3's
        own measurement, so nothing here may switch it without one. ``"rrf"`` is the opt-in
        alternative -- validated at ``MemoryProviderConfig`` construction (``_validate_fusion``
        always returns a dict, never ``None``), so a value reaching this method is already known
        good.
        """
        return self.config.fusion.get("method", "score_sum")

    def _merged(
        self,
        scope: Scope,
        query: MemoryQuery,
        legs: RetrievalLegs,
        *,
        method: str,
    ) -> List[MemoryRecord]:
        """Merge the legs and run every post-condition that does not need a backend call.

        ``method`` is required rather than defaulted: both halves call this, and a default would
        make the async one report the sync verb -- which is the same "names the method it delegated
        to" defect the entry guards exist to avoid, surfacing from the post-condition instead. (Not
        to be confused with the *fusion* method -- :meth:`_fusion_method` -- which this reads to
        decide how to merge.)

        RRF fuses all three legs, including lexical: it is rank-based, so it never needs the legs'
        scores to share a scale, which is exactly the precondition ``_merge``'s score-sum cannot
        meet for lexical (see ``_merge``'s own docstring). ``score_sum`` keeps the lexical leg out
        of ranking -- ADR 0003 A.6's constraint -- and only RRF, the measured strategy this ticket
        adds, may fold it in.
        """
        if self._fusion_method() == "rrf":
            k = self.config.fusion.get("k", DEFAULT_RRF_K)
            merged = rrf_fuse(legs.legs(), k=k)
        else:
            merged = _merge([legs.vector, legs.graph])
        assert_scope_isolation(scope, merged, method=method)
        self._assert_requested_tiers(query, merged)
        return merged

    def _rerank(self, query: MemoryQuery, merged: List[MemoryRecord]) -> Tuple[List[MemoryRecord], Dict[str, Any]]:
        """Optional cross-encoder rerank over the fused list (SM-H3 #133), off unless configured.

        Runs between fusion and bounds reconciliation: the fused order is what "top ~50 fused
        candidates" (the issue's own words) refers to, and reranking before reconciliation means a
        reordering never needs to know which rows a later validity check will still drop.
        ``config.rerank`` decides everything else -- see its own docstring and
        ``egpt.memory.retrieval.rerank``.
        """
        if not self.config.rerank.get("enabled", False):
            return merged, {}
        outcome = rerank_records(
            query.text,
            merged,
            reranker=self.config.rerank["reranker"],
            candidates=self.config.rerank.get("candidates", DEFAULT_RERANK_CANDIDATES),
            budget_ms=self.config.rerank.get("budget_ms", DEFAULT_RERANK_BUDGET_MS),
        )
        return outcome.records, outcome.diagnostics()

    async def _arerank(
        self, query: MemoryQuery, merged: List[MemoryRecord]
    ) -> Tuple[List[MemoryRecord], Dict[str, Any]]:
        """Async variant of :meth:`_rerank`, making the same decisions over ``arerank_records``."""
        if not self.config.rerank.get("enabled", False):
            return merged, {}
        outcome = await arerank_records(
            query.text,
            merged,
            reranker=self.config.rerank["reranker"],
            candidates=self.config.rerank.get("candidates", DEFAULT_RERANK_CANDIDATES),
            budget_ms=self.config.rerank.get("budget_ms", DEFAULT_RERANK_BUDGET_MS),
        )
        return outcome.records, outcome.diagnostics()

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        require_scope(scope, method="read")
        with memory_span(
            "read",
            tenant_id=scope.tenant_id,
            tier=self._read_tier_label(query),
            store=self._store_label(),
        ):
            return self._read(scope, query, method="read")

    def _read(self, scope: Scope, query: MemoryQuery, *, method: str) -> MemorySlice:
        """The read path both entry points share, carrying the verb that entered it.

        ``read_tier`` delegates here rather than to :meth:`read` so a post-condition failure names
        the verb the caller used. Threading it is what keeps the entry guard and the post-condition
        from disagreeing about which method is running.
        """
        # `GraphSubstrate.recall_episodes` (SM-G6 #104) and `.recall_facts` (SM-G7 #105) are
        # deliberately absent from this fan-out. Both answer a different question -- rows by
        # valid-time window and by entity/relation link, unranked -- than `traverse`'s
        # relevance-ranked search, and merging an unranked leg into `_merged`'s ranked-fusion
        # machinery would need a decision about how the two combine that nobody has made. No ticket
        # currently owns wiring either into the fused read path; a caller that wants one reaches
        # `provider.graph.recall_episodes` / `.recall_facts` directly, as
        # `cookbook/18_episodic_subgraph.py` / `cookbook/25_semantic_subgraph.py` do. Stated here
        # rather than left to look like an oversight, per round-3 review, PR #90.
        self._require_answerable(query)
        instant = query.as_of if query.as_of is not None else now_epoch_s()
        legs = orchestrate(scope, query, vector=self.vector, graph=self.graph)
        # Whether the lexical leg enters ranking is `_merged`'s call, via `_fusion_method` (SM-H2
        # #132): the default `score_sum` keeps it diagnostics-only (ADR 0003 A.6), `rrf` folds it
        # in. See `_merged`'s docstring.
        merged = self._merged(scope, query, legs, method=method)
        # Captured before `_rerank` can narrow `merged` (a reranker's own `top_n`, SM-H3 #133,
        # round-4 review) -- `_slice`'s `fused` diagnostic means "true fusion output count" and
        # always has (pre-dating this PR); passing it through explicitly is what keeps that
        # meaning from silently becoming "post-rerank count" once rerank narrows the pool.
        fused_count = len(merged)
        merged, rerank_diagnostics = self._rerank(query, merged)
        reconciled = self._reconcile_bounds(scope, merged)
        return self._slice(
            scope,
            query,
            merged,
            legs.vector,
            legs.graph,
            instant,
            reconciled,
            lexical_hits=legs.lexical,
            rerank_diagnostics=rerank_diagnostics,
            fused_count=fused_count,
        )

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        require_scope(scope, method="aread")
        with memory_span(
            "read",
            tenant_id=scope.tenant_id,
            tier=self._read_tier_label(query),
            store=self._store_label(),
        ):
            return await self._aread(scope, query, method="aread")

    async def _aread(self, scope: Scope, query: MemoryQuery, *, method: str) -> MemorySlice:
        """Async variant of :meth:`_read`, carrying the verb the same way."""
        self._require_answerable(query)
        instant = query.as_of if query.as_of is not None else now_epoch_s()
        legs = await aorchestrate(scope, query, vector=self.vector, graph=self.graph)
        # See `_read`'s comment: `_merged`/`_fusion_method` decide whether lexical enters ranking.
        merged = self._merged(scope, query, legs, method=method)
        # See `_read`'s comment: captured before `_arerank` can narrow `merged`.
        fused_count = len(merged)
        merged, rerank_diagnostics = await self._arerank(query, merged)
        reconciled = await self._areconcile_bounds(scope, merged)
        return self._slice(
            scope,
            query,
            merged,
            legs.vector,
            legs.graph,
            instant,
            reconciled,
            lexical_hits=legs.lexical,
            rerank_diagnostics=rerank_diagnostics,
            fused_count=fused_count,
        )

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Read one tier. A tier this provider does not serve reads **empty**, it is not refused.

        One rule across both adapters and both entry points. Previously the same logical request
        got three answers -- `co_located.read_tier(WORKING)` refused, `seed.read_tier(WORKING)`
        returned `[]`, and `co_located.read(tiers=[WORKING])` returned an empty slice -- so no
        caller could write one handler. Reads report absence; **writes** refuse it, because writing
        somewhere nothing will store it loses data while reading it simply finds nothing.

        The guard is repeated here rather than left to the ``read`` it delegates to, so the
        refusal names the verb the caller used. Every entry point carries its own for that reason;
        the second call is a type check on an object already in hand.

        Not wrapped in ``memory_span`` (SM-X4 #140): it delegates to the private ``_read``, not the
        traced ``read``, so a direct ``read_tier`` call produces no ``memory.read`` span. A v1
        scope decision -- SM-X4's acceptance criteria named only read/write/consolidate -- that was
        disclosed only in PR #158's description until now. ``write_tier``/``awrite_tier`` above
        have no such gap: they delegate into the traced ``write``/``awrite``, so a direct call to
        either still gets a span.
        """
        require_scope(scope, method="read_tier")
        return self._read(scope, replace(query, tiers=[tier]), method="read_tier").records

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Async variant of :meth:`read_tier`, with the same read-empty rule and the same
        undelegated-from-``memory_span`` gap documented there."""
        require_scope(scope, method="aread_tier")
        return (await self._aread(scope, replace(query, tiers=[tier]), method="aread_tier")).records

    # -- consolidate ----------------------------------------------------------

    def _check_consolidatable(self, tiers: Optional[Sequence[MemoryTier]]) -> VectorSubstrate:
        """Refuse an unrunnable pass, and hand back the substrate that will run it.

        Returning it rather than re-reading ``self.vector`` is what lets the callers narrow the
        Optional without an ``assert`` -- which ``-O`` strips, and which the seed adapter's own
        comment rejects for exactly that reason.
        """
        if self.vector is None:
            raise MemoryOperationError(
                "consolidate needs the vector substrate to enumerate episodes; none is wired.",
                field_name="provider",
            )
        if tiers is not None and set(tiers) != {CONSOLIDATION_SOURCE}:
            raise MemoryOperationError(
                f"consolidate reads the {CONSOLIDATION_SOURCE.value} tier and writes "
                f"{CONSOLIDATION_TARGET.value}, but was asked for {sorted(t.value for t in tiers)}. "
                "Which tier a pass may name is TIER_POLICY's consolidates_from.",
                field_name="tiers",
            )
        return self.vector

    def _cursor_key(self, scope: Scope, tier: MemoryTier) -> Tuple[str, MemoryTier]:
        """The ``_scan_cursors`` key -- ``scope.key()``, not the ``Scope`` object itself.

        A pass scoped to a tenant and a pass scoped to one user within it are different walks with
        different cursors, so the *call* scope is the key, not any scope a scanned row carries.
        """
        return scope.key(), tier

    def _advance_cursor(self, key: Tuple[str, MemoryTier], scanned: Sequence[MemoryRecord]) -> None:
        """Move the cursor to the last row this pass read, if it read any.

        ``scanned`` arrives in ``scan``'s own ascending ``(created_at, id)`` order, so its last
        element is the composite maximum -- no re-sort needed. Advancing even on a *complete* pass
        (rather than resetting) is what makes the next call cheap: it scans only what was written
        since, instead of re-reading the whole tier to confirm nothing new arrived.

        **Assumes episodes arrive with non-decreasing ``created_at``.** A write landing with
        ``(created_at, id) <= this cursor`` -- most plausibly clock skew between writers stamping
        ``now_epoch_s()`` a moment apart -- is excluded by ``scan``'s ``after`` bound on every
        future pass on this instance: not merely deferred, silently unreachable, the same failure
        mode ``tail_pending`` exists to name for the cap case. There is no detection or repair
        here; a deployment where writer clocks can disagree meaningfully needs one, and it is not
        built by this ticket. Pinned by
        ``test_a_backdated_write_below_the_cursor_is_never_consolidated``.
        """
        if not scanned:
            return
        last = scanned[-1]
        self._scan_cursors[key] = (last.created_at or 0, last.id)

    def _distil(self, episodes: Sequence[MemoryRecord]) -> Tuple[List[FactWrite], int]:
        """Group near-duplicate episodes and emit one fact per group.

        Deterministic token overlap, so the pass is testable end to end. This is the *shape*, not
        the strategy -- SM-R7 ratified mem0-style extract-and-merge, which is SM-L3's (#124).

        Grouping walks content order, not scan order: scan orders on ``(created_at, id)`` with UUID
        ids, so scan order made which episode heads a group vary per process.

        **Two episodes never group across scopes, even inside one tenant.** A pass scoped to a
        tenant (``Scope(tenant_id="acme")``, no ``user_id``) legitimately scans every user's
        episodes, and merging on content alone put one user's episode into a fact attributed to
        another -- who then read it back as their own. ``_assert_tenant_isolation`` cannot see this:
        both rows are the same tenant. Which user the fact landed on was decided by the content
        tiebreak, so the attribution was arbitrary as well as wrong.
        """
        now = now_epoch_s()
        # (scope, head tokens, members). The head's token set is cached rather than recomputed on
        # every comparison -- re-tokenising inside the inner loop dominated the quadratic term and
        # cost 7.5s of pure CPU at the 5,000 cap. Candidates are bucketed by scope first, so a
        # multi-user pass compares only within a user.
        buckets: Dict[Scope, List[Tuple[Set[str], List[MemoryRecord]]]] = {}
        for episode in sorted(episodes, key=lambda r: (r.scope.key(), r.content, r.created_at or 0, r.id)):
            tokens = _tokens(episode.content)
            for head_tokens, members in buckets.setdefault(episode.scope, []):
                if _jaccard(head_tokens, tokens) >= _DUPLICATE_THRESHOLD:
                    members.append(episode)
                    break
            else:
                buckets[episode.scope].append((tokens, [episode]))
        groups: List[List[MemoryRecord]] = [
            members for bucket_scope in sorted(buckets, key=lambda sc: sc.key()) for _, members in buckets[bucket_scope]
        ]

        facts: List[FactWrite] = []
        merged = 0
        for group in groups:
            merged += len(group) - 1
            head = group[0]
            source_ids = sorted(member.id for member in group)
            facts.append(
                FactWrite(
                    # Derived from the sources, so a re-run upserts the same row rather than
                    # accumulating a copy per pass.
                    record=MemoryRecord(
                        id=str(uuid5(_FACT_NAMESPACE, "|".join(source_ids))),
                        tier=CONSOLIDATION_TARGET,
                        content=head.content,
                        scope=head.scope,
                        topics=list(head.topics) if head.topics is not None else None,
                        provenance={"source_episode_ids": source_ids},
                        # The earliest evidence, not the head's. A fact claiming to become true
                        # later than the oldest episode it was distilled from answers a
                        # point-in-time read wrongly for the interval in between.
                        t_valid=min((m.t_valid for m in group if m.t_valid is not None), default=None),
                        # ...and the latest, symmetrically, or the fact **outlives every episode it
                        # came from**. `_visible_episodes` keeps an episode valid *at the pass
                        # instant*, so one carrying a future-dated `t_invalid` passes the filter and
                        # used to be minted with no upper bound at all -- unsuppressable afterwards
                        # by any read, at any limit, with any `as_of`. That is the failure
                        # `_visible_episodes` exists to prevent, one step later in the same method.
                        #
                        # Open-ended if *any* member is: a group with one unbounded episode is
                        # still true for as long as that episode is. And since
                        # `min(t_valid) <= m.t_valid < m.t_invalid <= max(t_invalid)` for every
                        # member, this can never build the empty interval the envelope refuses.
                        t_invalid=(
                            None if any(m.t_invalid is None for m in group) else max(m.t_invalid for m in group)  # type: ignore[type-var]
                        ),
                        created_at=max((m.created_at for m in group if m.created_at is not None), default=now),
                    ),
                    source_episode_ids=source_ids,
                    derived_by=_DETERMINISTIC_DEDUP_STRATEGY_ID,
                )
            )
        return facts, merged

    def _report(
        self,
        scope: Scope,
        scanned: Sequence[MemoryRecord],
        episodes: Sequence[MemoryRecord],
        facts: Sequence[FactWrite],
        merged: int,
        folded: int,
        entities: EntitySummary,
        relations: RelationSummary,
        reflection: ReflectionSummary,
        summary: SummarySummary,
        derived_by: str = _DETERMINISTIC_DEDUP_STRATEGY_ID,
        supersede: str = NO_SUPERSEDE,
        profile: ProfileSummary = NO_PROFILE,
    ) -> ConsolidationReport:
        hit_cap = len(scanned) >= self.max_episodes_per_pass
        # AC#3's anti-lossiness detection (SM-L7 #128), wired here rather than left as an unwired
        # library call (round-3 review) -- this is the one place `consolidate`/`aconsolidate`
        # already has both `facts` and the episode ids this pass read, with no extra I/O.
        #
        # **Scoped to `scanned`, not the scope's full episodic history -- read this caveat before
        # trusting a value below 1.0 as corruption.** A fact's `source_episode_ids` can legitimately
        # carry citations unioned forward from an *earlier* `consolidate` call's episode batch
        # (RESEARCH.md's own union-forward rule), which this pass never read and so cannot verify --
        # a scope-wide audit against every retained episode, not only this pass's scan window, needs
        # a full-tier read this method does not perform on every call (cost concern raised in
        # aicoe-tracker#194, which owns that audit). A value here that is `< 1.0` is therefore a
        # signal to investigate, not by itself proof of a dangling citation; a citation to an episode
        # this pass genuinely never read reads identically to one pointing at nothing. Detection
        # only -- nothing here raises or blocks the write on it, matching the round-2/3 review's own
        # "policy on violation stays in #194" boundary.
        provenance = provenance_report(facts, resolved_episode_ids={r.id for r in scanned})
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=len(scanned),
            semantic_written=len(facts),
            # `merged` counts *episodes* `_distil` absorbed; `folded` counts *facts*
            # `merge_near_duplicate_facts` (SM-L4 #125) folded afterwards -- different units, kept
            # in separate fields rather than summed into one that neither could be recovered from.
            duplicates_merged=merged,
            near_duplicate_facts_folded=folded,
            # `relations["edges_invalidated"]` (SM-L5 #126), not hard-coded 0: `_relations`/
            # `_arelations` hand back only `RelationExtractionReport.details`, and before SM-L5
            # added `edges_invalidated` to that dict (`relations.py`'s own `_report`), the count
            # SM-G5's pipeline was already producing had nowhere to reach this field from -- it
            # stayed 0 even on a pass that genuinely invalidated edges. `relations` is a bare
            # string (`NO_RELATIONS`) when no pipeline is wired, hence the `isinstance` guard
            # rather than a plain `.get`.
            #
            # `fact_pipeline`'s own UPDATE/DELETE retirements are deliberately not folded in here:
            # per ADR 0003 SS B.6, "the graph remains sole authority for semantic contradiction/
            # invalidation" -- a vector-side transaction-time supersede is a *different* mechanism
            # (module docstring, `consolidation/extract.py`), not a second contradiction this field
            # should also count.
            contradictions_resolved=relations.get("edges_invalidated", 0) if isinstance(relations, dict) else 0,
            details=consolidation_details(
                derived_by=derived_by,
                # `>=`, deliberately conservative: `scan` returns at most `limit`, so a full page
                # and a truncated one are indistinguishable from here. A tier holding exactly the
                # cap is reported as truncated when it was drained -- the false positive costs one
                # unnecessary cap increase, the false negative costs silent loss of the tail.
                complete=not hit_cap,
                supersede=supersede,
                entities=entities,
                # `relations`, `reflection`, `profile` and `summary` are free extra keys, not
                # `CONSOLIDATION_KEYS` floor members (see `RelationSummary`'s docstring): promoting
                # any of them is a separate, deliberate decision this ticket does not make.
                relations=relations,
                reflection=reflection,
                profile=profile,
                summary=summary,
                episodes_skipped_as_invalid=len(scanned) - len(episodes),
                # Renamed from `tail_unreachable` (SM-C1): that name stopped being true once
                # `_advance_cursor` shipped -- the tail is reachable, by calling `consolidate` again
                # on this instance. `tail_pending` says what is actually still known: this pass
                # stopped at the cap and left rows unread, without claiming they are unreachable.
                tail_pending=hit_cap,
                # Free extra key, same treatment as `relations`/`reflection` above -- not promoted
                # to `CONSOLIDATION_KEYS` (see this method's own comment on `provenance` above for
                # what a value below 1.0 does and does not mean).
                provenance_validity=provenance.validity,
                provenance_dangling=list(provenance.dangling),
            ),
        )

    def _entities(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> EntitySummary:
        """Run SM-G2's (#100) entity extraction over the episodes this pass kept.

        Over ``episodes`` rather than ``scanned``: an episode the graph has recorded as false is one
        ``_visible_episodes`` already dropped from distillation, and extracting entities from it
        would put nodes in the graph sourced from a row no read returns.

        **After** the fact write, deliberately. Extraction is an LLM pass and the flakiest step in
        the lane; running it first would let a provider timeout cost the deterministic dedup that
        had nothing to do with it. A failure here still propagates -- it is not swallowed into a
        report that claims a clean run.

        **Two independent caps, not one.** ``self.max_episodes_per_pass`` bounds what ``scan``
        reads into this pass; the pipeline's own ``max_episodes_per_pass`` (default 200, well below
        this class's default 5000) can cut further inside ``EntityExtractionPipeline._selected``.
        A scheduler reading only the top-level ``details["complete"]`` would miss a truncated
        extraction -- ``details["entities"]["complete"]`` is the one that says so.

        ``NO_ENTITIES`` only when no pipeline is wired -- **not** also when ``episodes`` happens to
        be empty (review finding on PR #89, round 3). A wired pipeline handed an empty list still
        ran: ``EntityExtractionPipeline.run([])`` makes no model call and returns a proper
        ``entity_details(complete=True, ...)`` dict, which is what a caller needs to tell "nothing
        to extract this pass" apart from "extraction was never configured" -- the two read
        identically as ``NO_ENTITIES`` otherwise, and a scheduler retrying the second case would
        wait forever for a pipeline that was never going to run.

        PII containment (SM-X5 #141 round-1 review, finding #2): ``episodes`` here is already
        redacted by ``consolidate``/``aconsolidate`` before this method runs, so
        ``EntityExtractionPipeline.extract`` never reads raw episode content -- this closes the
        verbatim-copy leak. It does not redact the pipeline's own generated
        ``EntityWrite.description``/``.aliases``/``.properties``: ``EntityExtractionPipeline`` is
        a standalone, config-agnostic class with no tenant-policy awareness of its own, and giving
        it one is a larger change than this ticket's scope (parallel to the entities/relations
        write-accounting gap already tracked as aicoe-tracker #186). Redacting clean episodes
        substantially reduces this risk (the extractor has no raw PII to restate) but does not
        eliminate it the way the fact/profile fixes above do for their own paths.
        """
        if self.entity_pipeline is None:
            return NO_ENTITIES
        return self.entity_pipeline.run(scope, episodes).details

    async def _aentities(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> EntitySummary:
        """Async variant of :meth:`_entities`, making the same decisions."""
        if self.entity_pipeline is None:
            return NO_ENTITIES
        return (await self.entity_pipeline.arun(scope, episodes)).details

    def _relations(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> RelationSummary:
        """Run SM-G3's (#101) relation extraction over the episodes this pass kept.

        Same ``episodes``, same ordering after the fact write, and the same
        wired-vs-empty-vs-never-wired distinction as :meth:`_entities` -- see that method's
        docstring for why each of those matters, including the same PII-containment scope
        boundary (SM-X5 #141 round-1 review, finding #2): ``episodes`` is redacted before this
        runs, but ``RelationExtractionPipeline``'s own generated fact fields are not.
        """
        if self.relation_pipeline is None:
            return NO_RELATIONS
        # TODO(aicoe-tracker #195): this discards the typed `RelationExtractionReport` entirely,
        # keeping only `.details` -- a `Dict[str, Any]`. `edges_invalidated` survives that only
        # because `relations.py`'s own `_report` deliberately duplicates it into `.details` as a
        # workaround (see that call site's comment); any *other* field added to the typed report
        # in the future will silently fail to reach `ConsolidationReport` the same way
        # `edges_invalidated` did before SM-L5, unless it gets the same manual duplication.
        # Returning the typed report itself (or a richer summary than `RelationSummary`'s
        # `Union[Dict[str, Any], str]`) would close this for good, but changes this method's
        # public return shape and every `NO_RELATIONS`-pattern-matching call site -- wider than
        # one ticket, tracked separately at aicoe-tracker #195.
        return self.relation_pipeline.run(scope, episodes).details

    async def _arelations(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> RelationSummary:
        """Async variant of :meth:`_relations`, making the same decisions -- including the same
        `.details`-only TODO gap; see that method's comment."""
        if self.relation_pipeline is None:
            return NO_RELATIONS
        return (await self.relation_pipeline.arun(scope, episodes)).details

    def _profile_messages(self, episodes: Sequence[MemoryRecord]) -> List["Message"]:
        """Episodes, as the ``Message`` list ``UserProfileStore.extract_profile`` expects.

        Lazily imported: ``egpt.models.message`` is the same heavy-stack dependency the module
        docstring keeps ``entity_pipeline``/``relation_pipeline`` duck-typed to avoid paying for
        on every ``import egpt.memory`` -- only reached when a profile store is actually wired.
        """
        from egpt.models.message import Message

        return [Message(role="user", content=r.content) for r in episodes if r.content]

    def _redact_profile(self, profile: Any) -> Any:
        """Mask PII out of a :class:`~egpt.learn.schemas.UserProfile`'s string fields before it
        reaches :attr:`_semantic_profile`'s write (SM-X5 #141 round-1 review, finding #2).

        ``_profile_messages(episodes)`` above already carries redacted episode content into the
        extraction call (``episodes`` is redacted by the caller, ``consolidate``/
        ``aconsolidate``), but the *output* ``UserProfile`` -- ``name``/``preferred_name``, plus
        whatever custom fields a tenant's schema subclass adds -- is the LLM's own structured
        summary, not a copy of episode text, and ``SemanticProfileStore._record`` serialises the
        whole object into ``metadata["profile"]``, a field :func:`redact_records_for_writeback`
        deliberately never touches (metadata is caller-structured data everywhere else in this
        package). No existing helper reaches inside an arbitrary dataclass's fields, so this
        walks ``dataclasses.fields(profile)`` directly rather than adding a `UserProfile`-specific
        dependency to `memory/security/pii.py`.

        Only ``str`` fields are touched, via the same guardrail every other write-back path uses
        -- this catches a pattern match (an SSN typed into a free-text custom field, say) embedded
        anywhere in a profile field, but it is not a substitute for "``name`` is identity data by
        definition, regardless of pattern": a guardrail that does not pattern-match ordinary names
        will not mask one. That is a capability limit of the guardrail this PR reuses, not
        something this function papers over.
        """
        if pii_guardrail_for_config(self.config) is None:
            return profile
        changes: Dict[str, Any] = {}
        for f in dataclass_fields(profile):
            value = getattr(profile, f.name)
            if isinstance(value, str):
                redacted = redact_text_for_writeback(value, self.config)
                if redacted != value:
                    changes[f.name] = redacted
        if not changes:
            return profile
        return replace(profile, **changes)

    def _update_profile(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> ProfileSummary:
        """Run SM-S3's (#120) profile extraction over the episodes this pass kept, and persist
        the result through :attr:`_semantic_profile` -- tenant/user-scoped, unlike
        ``self.profile_store``'s own ``self.db``, which this method never touches.

        Same wired-vs-never-wired distinction as :meth:`_entities`, plus one more: a tenant-wide
        pass (``scope.user_id is None``) has no single user to key a profile by, so it reports
        :data:`PROFILE_NO_USER` rather than silently skipping -- the same reasoning
        :data:`~egpt.memory.types.NO_PROFILE` gets for "never wired".

        **Read-modify-write, no lock or CAS.** ``self._semantic_profile.get`` is read, an LLM
        call happens, then ``.write`` lands the result -- two overlapping passes for the *same*
        scope can each read the same stale profile and the later write silently clobbers the
        earlier one. This is the same class of exposure ``ConsolidationWorker.run_once`` already
        documents for its cursor (safe against concurrent *distinct* scopes; not safe against two
        callers driving the *same* scope at once) and that entity/relation merge carries too --
        not a gap this ticket introduces, and not one it closes. ``ConsolidationWorker`` itself
        never triggers this: it gathers one ``_drain_scope`` per *distinct* configured scope, so
        the same scope is never consolidated twice in one tick. It is reachable only when a
        caller drives ``consolidate``/``aconsolidate`` directly for one scope from more than one
        place at once -- which should be serialized until a stronger guarantee exists here.
        """
        if self.profile_store is None:
            return NO_PROFILE
        if scope.user_id is None:
            return PROFILE_NO_USER
        messages = self._profile_messages(episodes)
        if not messages:
            return {"complete": True, "updated": False}
        existing = self._semantic_profile.get(scope)
        updated = self.profile_store.extract_profile(messages, user_id=scope.user_id, existing_profile=existing)
        if updated is None:
            return {"complete": True, "updated": False}
        updated = self._redact_profile(updated)
        receipt = self._semantic_profile.write(scope, updated)
        return {"complete": True, "updated": receipt is not None}

    async def _aupdate_profile(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> ProfileSummary:
        """Async variant of :meth:`_update_profile`, making the same decisions."""
        if self.profile_store is None:
            return NO_PROFILE
        if scope.user_id is None:
            return PROFILE_NO_USER
        messages = self._profile_messages(episodes)
        if not messages:
            return {"complete": True, "updated": False}
        existing = await self._semantic_profile.aget(scope)
        updated = await self.profile_store.aextract_profile(messages, user_id=scope.user_id, existing_profile=existing)
        if updated is None:
            return {"complete": True, "updated": False}
        updated = self._redact_profile(updated)
        receipt = await self._semantic_profile.awrite(scope, updated)
        return {"complete": True, "updated": receipt is not None}

    def _reflect(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> Tuple[List[FactWrite], ReflectionSummary]:
        """Run SM-L6's (#127) reflection over the episodes this pass kept.

        Returns the candidate insight ``FactWrite``s alongside the report details, rather than
        writing them here -- unlike :meth:`_entities`/:meth:`_relations`, which own a dedicated
        graph port and write inside themselves, an insight is a SEMANTIC fact that must go through
        the exact same vector/graph write loop a distilled fact does (``reflect.py``'s "one write
        model"), so the caller folds it into ``facts`` before that loop runs.

        Same wired-vs-never-wired distinction as :meth:`_entities`: ``NO_REFLECTION`` only when no
        pipeline is configured, never merely because this pass's accumulated importance had not
        yet crossed the threshold -- that reads as ``details["triggered"] is False`` on a real
        report, which a scheduler needs to tell apart from "reflection was never configured".

        **Folding an insight into ``facts`` changes what an entity/relation failure looks like on
        an otherwise-duplicate-free pass.** Before reflection existed, a pass with no duplicate
        episodes took the ``else:`` branch below -- no receipt, so an entity/relation failure
        propagated bare. A triggered insight makes ``facts`` non-empty even when ``_distil`` found
        nothing to dedupe, routing that same pass through the ``if facts:`` branch instead, where
        an entity/relation failure is wrapped into ``PartialWriteError(receipt)`` -- ``receipt``
        naming only the insight fact's id, since that is the only thing this pass actually wrote.
        Deliberate, not incidental: an insight *is* a committed write by the time entities/
        relations run, exactly the condition that branch exists to detect, and a caller catching
        bare exceptions from ``consolidate`` on a reflection-configured provider must be ready for
        this.

        **A synthesis failure here must not fail this whole pass.** Unlike entities/relations
        (whose failure after a fact write is wrapped into ``PartialWriteError`` and propagated),
        this call runs *before* ``if facts:`` -- nothing has committed yet, so an uncaught
        exception would previously propagate bare out of ``consolidate`` and block dedup facts,
        entity extraction, and relation extraction for the *entire* scope on every future pass,
        for as long as the same episodes keep failing to synthesize (reproduced: a synthesizer
        that always raises wrote zero semantic rows across three consecutive passes, including
        the ordinary dedup fact for an unrelated episode). Reflection is an optional, additive
        stage; the mandatory dedup/entity/relation work must not go down with it -- the same
        each-child-independent guarantee ``AnyTrigger`` (SM-L2 #123, ``triggers.py``) already
        makes for its own children. Caught and logged here, not swallowed silently: the pipeline's
        own trigger is left armed (``_consume`` was never reached inside ``run``), so the next
        pass retries synthesis on the same accumulation -- the same "safe to retry" contract
        every other pass in this module already carries -- without blocking anything else in the
        meantime.

        **``MemoryScopeRequiredError`` is not caught here -- it propagates.** Every other
        exception this call can raise is a runtime failure worth retrying (a bad model response,
        a network error, injected content the synthesizer refuses); a scope failing its own
        validity check is a caller bug at the site that built it, not something a later pass can
        fix by trying again. Catching it the same way as the rest would turn a call-site defect
        into an indefinitely-repeating, silently-logged no-op instead of surfacing it to whoever
        can actually fix it -- and in practice it is unreachable through ``consolidate``, which
        already validates ``scope`` before calling this, so the guard is for a caller-supplied
        ``reflection_pipeline`` reached some other way.
        """
        if self.reflection_pipeline is None:
            return [], NO_REFLECTION
        try:
            report = self.reflection_pipeline.run(scope, episodes)
        except MemoryScopeRequiredError:
            raise
        except Exception as exc:
            log_warning(f"Reflection failed for scope {scope.key()!r}, will retry next pass: {exc}")
            return [], reflection_failed_details(exc)
        return report.insights, report.details

    async def _areflect(
        self, scope: Scope, episodes: Sequence[MemoryRecord]
    ) -> Tuple[List[FactWrite], ReflectionSummary]:
        """Async variant of :meth:`_reflect`, making the same decisions."""
        if self.reflection_pipeline is None:
            return [], NO_REFLECTION
        try:
            report = await self.reflection_pipeline.arun(scope, episodes)
        except MemoryScopeRequiredError:
            raise
        except Exception as exc:
            log_warning(f"Reflection failed for scope {scope.key()!r}, will retry next pass: {exc}")
            return [], reflection_failed_details(exc)
        return report.insights, report.details

    def _summarize(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> Tuple[List[FactWrite], SummarySummary]:
        """Run SM-L8's (#129) hierarchical summarization over the episodes this pass kept.

        Same shape as :meth:`_reflect`: candidate ``FactWrite``s are returned, not written --
        ``summarize.py``'s own "one write model" docstring -- and a failure here is caught and
        logged rather than propagated, for the identical reason ``_reflect``'s docstring gives at
        length: summarization is optional and additive, an LLM call is the flakiest step in this
        lane, and the mandatory dedup/entity/relation work bolted onto the same pass must not go
        down because a summarizer had a bad day.

        **Retry safety is not uniform across the two things this pass can fail at.** A day/global
        *fold* failure genuinely retries: ``SummaryPipeline``'s own pending accumulation and trigger
        are left armed (its ``_roll_up_day``/``_roll_up_global`` docstrings), untouched by anything
        that happens up here, so the next call to this same pipeline instance folds the identical
        pending set again. A *session-narration* failure does not have that guarantee: this
        episodic batch's episodes are still consumed by ``consolidate``'s own scan cursor
        regardless of whether summarization succeeded (that cursor is shared with dedup/entity/
        relation extraction, not owned by this pipeline), so the next ``consolidate`` call scans a
        new batch and these specific episodes are never re-presented to the session summarizer.
        Unlike ``ReflectionPipeline`` -- whose whole flow *is* the accumulate-then-conditionally-fire
        pattern, so its "will retry next pass" claim covers everything it does -- this pipeline has
        a step (session narration) that sits outside that pattern, which is why the same claim does
        not cover it here. Giving this pipeline its own resumption cursor, independent of
        ``consolidate``'s, would close the gap; this ticket does not make that change.

        **``MemoryScopeRequiredError`` is not caught here -- it propagates**, the same carve-out
        ``_reflect`` makes: a scope failing its own validity check is a caller bug, not a runtime
        failure worth retrying silently forever.
        """
        if self.summary_pipeline is None:
            return [], NO_SUMMARY
        try:
            report = self.summary_pipeline.run(scope, episodes)
        except MemoryScopeRequiredError:
            raise
        except Exception as exc:
            log_warning(f"Summarization failed for scope {scope.key()!r}, will retry next pass: {exc}")
            return [], summary_failed_details(exc)
        return report.summaries, report.details

    async def _asummarize(
        self, scope: Scope, episodes: Sequence[MemoryRecord]
    ) -> Tuple[List[FactWrite], SummarySummary]:
        """Async variant of :meth:`_summarize`, making the same decisions."""
        if self.summary_pipeline is None:
            return [], NO_SUMMARY
        try:
            report = await self.summary_pipeline.arun(scope, episodes)
        except MemoryScopeRequiredError:
            raise
        except Exception as exc:
            log_warning(f"Summarization failed for scope {scope.key()!r}, will retry next pass: {exc}")
            return [], summary_failed_details(exc)
        return report.summaries, report.details

    @staticmethod
    def _by_scope(facts: Sequence[FactWrite]) -> List[Tuple[Scope, List[FactWrite]]]:
        """Batch facts by their own scope -- ``egpt.memory.validation.group_facts_by_scope``.

        Kept as a thin alias rather than inlined at each call site: both call sites below read as
        ``self._by_scope(facts)`` in a diff against the pre-extraction version, so the behavior
        change this ticket makes is visible as none. See the shared function's docstring for why
        this must key on each fact's own scope rather than the call scope.
        """
        return group_facts_by_scope(facts)

    def _fact_targets(self, facts: Sequence[FactWrite]) -> Tuple[bool, bool]:
        """Which substrates the consolidation target tier routes to, per ``TIER_POLICY``.

        ``write`` filters by routing and ``_graph_batches`` drops a tier that does not reach the
        graph; consolidate did neither, so it wrote wherever a substrate happened to be wired.

        **Growth-tracking caveat, latent while SEMANTIC routes to both vector and graph.**
        consolidate()/aconsolidate()'s per-group growth tracking (``group_committed``, set only
        after a write call returns without raising) counts on the atomic vector leg landing before
        a non-atomic ``upsert_facts``/``aupsert_facts`` call (see ``_graph_fact_batch``'s own
        docstring: that call "is explicitly not required to be atomic") can partially commit and
        then raise. A graph-only target tier (``to_vector`` False here, a real and tested
        configuration -- see ``test_consolidation_honours_tier_routing_like_the_write_path_does``)
        already exercises the happy path fine; what stays uncovered is a group whose graph leg
        *partially* commits before raising with no vector leg to have already banked that growth --
        that group under-counts (records zero, not the row(s) that actually landed) rather than
        over-counting, which is the safe direction but still a real gap. Fixing it for real needs
        the graph substrate to report how much of a failed batch committed, which no
        ``GraphSubstrate`` implementation in this tree does today -- tracked as SM-X8 follow-up
        rather than guarded here, since refusing this configuration outright would break the
        graph-only tier routing above, which is intentional and already relied upon.
        """
        routed = substrates_for_tier(CONSOLIDATION_TARGET) if facts else frozenset()
        return "vector" in routed, "graph" in routed

    def _visible_episodes(self, scope: Scope, episodes: List[MemoryRecord], instant: int) -> List[MemoryRecord]:
        """Drop episodes the graph has recorded as false before anything is distilled.

        ``scan`` reads the vector substrate, whose projection is stale with respect to validity, so
        an invalidated episode arrived here looking live and was minted into a **brand-new
        semantic fact with no bounds at all** -- unsuppressable afterwards by any read, at any
        limit, with any ``as_of``. Strictly worse than the read-path version of this bug, which at
        least stops when the corpus shrinks.
        """
        self._reconcile_bounds(scope, episodes)
        return [e for e in episodes if e.is_current_belief() and _valid_at(e, instant)]

    async def _avisible_episodes(self, scope: Scope, episodes: List[MemoryRecord], instant: int) -> List[MemoryRecord]:
        """Async variant of :meth:`_visible_episodes`."""
        await self._areconcile_bounds(scope, episodes)
        return [e for e in episodes if e.is_current_belief() and _valid_at(e, instant)]

    def _redact_facts(self, facts: Sequence[FactWrite]) -> List[FactWrite]:
        """Mask PII out of every fact's ``record.content`` and SPO fields before a write (SM-X5
        #141 round-1 review, finding #2).

        ``record.content`` is redacted through :func:`redact_records_for_writeback`, the same
        function ``episodes`` above was redacted with -- `FactWrite` composes a `MemoryRecord`, so
        the two share the exact same leak shape. ``subject``/``predicate``/``object`` are separate
        free-text fields the relation-extraction path populates outside ``record.content``
        (`FactWrite`'s own docstring: "SPO is optional structure over the free-text statement in
        ``record.content``") -- :func:`redact_records_for_writeback` never touches them, since it
        only knows about the composed ``MemoryRecord``, so they need :func:`redact_text_for_writeback`
        applied individually.
        """
        records = redact_records_for_writeback([f.record for f in facts], self.config)
        redacted: List[FactWrite] = []
        for fact, record in zip(facts, records):
            subject = redact_text_for_writeback(fact.subject, self.config)
            predicate = redact_text_for_writeback(fact.predicate, self.config)
            obj = redact_text_for_writeback(fact.object, self.config)
            if record is fact.record and subject == fact.subject and predicate == fact.predicate and obj == fact.object:
                redacted.append(fact)
                continue
            redacted.append(replace(fact, record=record, subject=subject, predicate=predicate, object=obj))
        return redacted

    @staticmethod
    def _check_distilled(facts: Sequence[FactWrite]) -> None:
        """The guard ``_prepare`` applies to a write batch, applied to a consolidated one.

        Called **before** either half writes anything, for two reasons. One function both halves
        call cannot drift -- added to the sync path alone, it was missing from the async one a
        review later. And a refusal before the fan-out must propagate bare: inside the write's
        ``try`` it was reshaped into a ``PartialWriteError`` announcing zero committed ids, which
        tells a caller to retry something that never started.

        ``_distil``'s grouping partitions its input, so no two groups can derive one fact id today
        -- but that is a property of the current strategy, which SM-L3 (#124) replaces, and an
        un-enforced invariant one refactor from reopening a duplicate-row hole is worth one call.

        Called on ``_distil``'s output *after* ``consolidation.dedup.merge_near_duplicate_facts``
        (SM-L4 #125) has already folded it, not on ``_distil``'s raw groups -- the fold re-derives
        a folded fact's id from its combined provenance, so this still checks the ids that are
        actually about to be written, the same guarantee it always gave (review finding 9,
        PR #116).
        """
        reject_duplicate_ids([fact.record for fact in facts])

    def _graph_fact_batch(self, fact_scope: Scope, batch: Sequence[FactWrite], receipt: WriteReceipt) -> None:
        """The sync counterpart of ``_gather_writes`` for one batch of facts.

        **A batched write reports one failure, not the best of several.** The per-fact loop this
        replaced could collect a transport error *and* a refusal from one batch and let
        ``_raise_write_failure`` pick the refusal as the cause; a single call raises once, so that
        selection has nothing to choose between here. The trade is deliberate -- one round trip per
        scope group was the point -- and the selection still matters on the `write` path, where the
        two verbs fan out and can fail differently
        (`test_a_refusal_among_concurrent_failures_wins_over_a_transport_error`). The consolidate
        test's own "mixed" case was dropped for exactly this reason, which its docstring records.

        Both halves now make **one** call, so they cannot diverge on how many facts they attempted.
        What still has to be shared is how a failure is *shaped*: ``upsert_facts`` is explicitly not
        required to be atomic, so a batch that raises may have committed part of itself. That is a
        partial write whatever else has happened, which is why it is shaped here rather than left to
        ``consolidate``'s ``committed_any`` arm -- that arm would report a bare refusal, and a bare
        refusal denies the caller the ids its retry needs.
        """
        failures: List[BaseException] = []
        try:
            self.graph.upsert_facts(fact_scope, batch)  # type: ignore[union-attr]  # caller checked
        except Exception as exc:
            failures.append(exc)
        self._raise_write_failure(failures, receipt)

    def _distil_facts(
        self, scope: Scope, episodes: Sequence[MemoryRecord]
    ) -> Tuple[List[FactWrite], int, int, str, str]:
        """Which strategy runs -- ``fact_pipeline`` when wired, ``_distil``'s dedup default
        otherwise -- and the ``(derived_by, supersede)`` pair the report names it with.

        ``supersede`` is a **capability** flag, not an occurrence one: :data:`FACT_PIPELINE_STRATEGY_ID`
        means this pass *could* retire a fact, not that it *did*. A pass whose every candidate was
        ADD or NOOP reports the same value as one that retired ten -- distinguishing them needs
        ``report.deleted`` plus the retirement half of ``report.updated`` from
        :class:`~egpt.memory.consolidation.extract.FactExtractionReport` directly, which this
        method's own ``merged`` return does not carry either. :data:`NO_SUPERSEDE`, by contrast, is
        unambiguous: no pipeline wired, no retirement is possible this pass or any other.

        ``merged`` is redefined for the pipeline path: ``_distil`` counts extra *episodes* folded
        into a group; the pipeline counts UPDATE and NOOP decisions, which is the closest existing
        analogue (episodes that did not become a brand-new fact) since it has no notion of a
        "group" to subtract one from.

        ``merge_near_duplicate_facts`` (SM-L4 #125) runs only on the ``_distil`` default path,
        never over a wired ``fact_pipeline``'s output -- and always ``folded=0`` there, not "not
        run this pass". Two reasons: ``dedup.py``'s own module docstring already scopes the
        embedding/LLM-driven case to this ticket ("SM-L3 owns embedding-similarity dedup"), so a
        wired pipeline already decided ADD/UPDATE/DELETE/NOOP against existing facts itself and
        running a second, cruder content-equality fold over its output is redundant at best. Worse
        than redundant: ``merge_near_duplicate_facts`` unconditionally resets
        ``expired_at``/``superseded_by`` to ``None`` on every fold, documented as safe only because
        ``_distil``'s output never sets them -- a pipeline's UPDATE/DELETE retirement copies do,
        and folding one into an unrelated same-content fact would silently un-retire it.
        """
        if self.fact_pipeline is not None:
            facts, report = self.fact_pipeline.distil(scope, episodes)
            return facts, report.updated + report.noop, 0, FACT_PIPELINE_STRATEGY_ID, FACT_PIPELINE_STRATEGY_ID
        facts, merged = self._distil(episodes)
        facts, folded = merge_near_duplicate_facts(facts)
        return facts, merged, folded, _DETERMINISTIC_DEDUP_STRATEGY_ID, NO_SUPERSEDE

    async def _adistil_facts(
        self, scope: Scope, episodes: Sequence[MemoryRecord]
    ) -> Tuple[List[FactWrite], int, int, str, str]:
        """Async variant of :meth:`_distil_facts`, making the same decisions."""
        if self.fact_pipeline is not None:
            facts, report = await self.fact_pipeline.adistil(scope, episodes)
            return facts, report.updated + report.noop, 0, FACT_PIPELINE_STRATEGY_ID, FACT_PIPELINE_STRATEGY_ID
        # Off the loop, matching `aconsolidate`'s existing `_distil` call: quadratic and measured
        # seconds of pure CPU at the cap, during which no other coroutine in the process runs.
        facts, merged = await asyncio.to_thread(self._distil, episodes)
        # Not offloaded: a pure in-memory fold over `_distil`'s already-small output, no `await`
        # anywhere in it (matching `merge_near_duplicate_facts`'s own docstring and the sync call
        # site's identical reasoning).
        facts, folded = merge_near_duplicate_facts(facts)
        return facts, merged, folded, _DETERMINISTIC_DEDUP_STRATEGY_ID, NO_SUPERSEDE

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        require_scope(scope, method="consolidate")
        with memory_span(
            "consolidate",
            tenant_id=scope.tenant_id,
            tier=self._consolidate_tier_label(tiers),
            store=self._store_label(),
        ):
            vector = self._check_consolidatable(tiers)
            instant = now_epoch_s()
            cursor_key = self._cursor_key(scope, CONSOLIDATION_SOURCE)
            # Detached before anything touches them: `scan` may hand back the rows the substrate is
            # holding (`search` returns copies, `scan` need not), and `_reconcile_bounds` writes bounds
            # onto what it is given.
            scanned = [
                r.detached()
                for r in vector.scan(
                    scope,
                    CONSOLIDATION_SOURCE,
                    limit=self.max_episodes_per_pass,
                    after=self._scan_cursors.get(cursor_key),
                )
            ]
            assert_scope_isolation(scope, scanned, method="consolidate")
            episodes = self._visible_episodes(scope, scanned, instant)
            # PII containment (SM-X5 #141 round-1 review, finding #2): `consolidate` reads
            # storage directly via `vector.scan` above, bypassing `write`/`awrite`'s redaction
            # entirely -- these episodes may already carry raw PII, either from a bypassed write
            # path or because they were written before this tenant's policy turned PII handling
            # on (the read boundary's own justification, see `security/pii.py`'s module
            # docstring). Redacting here keeps every LLM pass below from reading raw content, and
            # `_distil`'s dedup default copies episode content verbatim into `FactWrite.record`
            # (see `redact_records_for_writeback`'s docstring for why this alone is not
            # sufficient for the LLM-generated paths).
            episodes = redact_records_for_writeback(episodes, self.config)
            facts, merged, folded, derived_by, supersede = self._distil_facts(scope, episodes)
            # Folded in before the `if facts:` branch below, deliberately: an insight is written
            # through the exact same vector/graph loop, receipt, and `PartialWriteError` accounting a
            # distilled fact is, so a pass with no duplicate facts but a triggered reflection still
            # takes the `if facts:` path rather than needing a second, parallel write branch.
            insight_facts, reflection = self._reflect(scope, episodes)
            facts += insight_facts
            # Same fold-before-branch reasoning as reflection, one stage later: a summary node is
            # written through the exact same vector/graph loop, receipt, and `PartialWriteError`
            # accounting a distilled fact or an insight is (`summarize.py`'s "one write model").
            summary_facts, summary = self._summarize(scope, episodes)
            facts += summary_facts
            # A second pass, not a duplicate of the one above: episodes were clean going into
            # distillation/reflection/summarization, but each of those is (in the fact_pipeline
            # path) an LLM restating or paraphrasing episode content -- it can still reintroduce
            # PII in its own words even from clean input. This is the choke point every fact this
            # method itself writes to vector/graph funnels through.
            facts = self._redact_facts(facts)

            if facts:
                self._check_distilled(facts)
                to_vector, to_graph = self._fact_targets(facts)
                # Stamped up front, exactly as `write` does. Advancing counters inside the loop left
                # `accepted=0, event_ids=[]` on every single-scope failure -- the ordinary case -- while
                # rows were already committed, and the error text then reads "retry with these ids: []".
                # Counting only committed rows is the other tempting fix and is ruled out by
                # `PartialWriteError`'s own contract: `event_ids` are the ids a retry must reuse.
                receipt = WriteReceipt(
                    accepted=len(facts),
                    event_ids=[f.record.id for f in facts],
                    tiers_written=[CONSOLIDATION_TARGET],
                )
                committed_any = False
                try:
                    for fact_scope, batch in self._by_scope(facts):
                        group_committed = False
                        try:
                            if to_vector:
                                # `fact_record`, not `f.record`: the vector row must carry the same
                                # subject/predicate/object/confidence/derived_by a graph `recall_facts`
                                # read already surfaces, or the same fact reads as two different shapes
                                # depending which leg answered (SM-S1 #118).
                                vector.upsert(fact_scope, [fact_record(f) for f in batch])
                                committed_any = True
                                group_committed = True
                            if to_graph and self.graph is not None:
                                self._graph_fact_batch(fact_scope, batch, receipt)
                                committed_any = True
                                group_committed = True
                        finally:
                            # `finally`, not "after the `if to_graph` block": a group's own graph leg
                            # can raise right after its vector leg landed, and that group's own partial
                            # commit is real growth exactly as much as a fully-landed group's is --
                            # the single-group case below relies on this (its vector leg committing,
                            # then its only group's graph leg raising, must still be counted, matching
                            # the round-1 fix). Recorded per group, under that group's own `fact_scope`,
                            # not once at the end under the pass-wide `scope` with the full `facts`
                            # count -- a group after this one that the raise below stops the loop from
                            # ever reaching gets no entry, since its `try` never ran.
                            if group_committed:
                                self._record_fact_growth(fact_scope, batch)
                except PartialWriteError:
                    # Nothing left to record here: every group that committed anything already
                    # recorded its own growth above, inside the loop, before the raise.
                    raise
                except MemoryOperationError:
                    # A refusal on the *first* write, before anything landed, is just a refusal --
                    # matching `write`, where a vector refusal precedes any commit and propagates bare.
                    # Once something is committed it is a partial write like any other -- but, as
                    # above, already-committed groups already recorded their own growth.
                    if not committed_any:
                        raise
                    raise PartialWriteError(receipt) from None
                except Exception as exc:
                    raise PartialWriteError(receipt) from exc
                # Extraction runs after the fact writes, not inside their `try`: it is not a fact write
                # and must not be counted as one in `committed_any`. But once facts landed, a failure
                # here is exactly as much a partial write as a failure in the loop above -- the caller
                # still needs `receipt` to retry the ids that committed. `receipt` exists only inside
                # this `if facts:` branch, which is why the guard is here rather than wrapping the
                # `return` unconditionally.
                try:
                    entities = self._entities(scope, episodes)
                    # After entities, not before: relation edges land on the same entity nodes by
                    # name (module docstring), so running this second lets a fresh node exist before
                    # any edge that names it -- though `upsert_facts` creates a bare endpoint either
                    # way, so this is ordering for readability, not a correctness dependency.
                    relations = self._relations(scope, episodes)
                    # After relations, same non-dependency: a profile update reads/writes its own
                    # singleton row and touches neither entities nor relations.
                    profile = self._update_profile(scope, episodes)
                except Exception as exc:
                    raise PartialWriteError(receipt) from exc
            else:
                # No `receipt` exists here to attach -- `facts` is empty, so no *semantic fact* was
                # committed above. `_entities`/`_relations` below can still write graph nodes/edges of
                # their own, uncounted by this branch's `PartialWriteError` handling -- a pre-existing
                # gap, pre-existing for `_entities` alone and now also covering `_relations`'s
                # write below -- this ticket does not widen the fix for either; tracked as
                # aicoe-tracker #186. A failure here is a plain refusal, matching
                # `write`'s rule: propagate bare, do not wrap. Deliberately parallel to the `if facts:`
                # branch above, not an oversight against it -- see the review-round-3 note in
                # `_entities`'s own docstring.
                #
                # `_update_profile`'s write below carries the identical gap (QC round 3, PR #135):
                # its own `SemanticProfileStore.write` failure here is a plain refusal too, uncounted
                # by any receipt -- the same #186 scope-out, not a separate oversight for the profile
                # leg specifically.
                #
                # Recording a zero-accepted entry here (rather than skipping it) is what makes a scope
                # that never consolidates anything distinguishable from one that was never asked.
                self._record_fact_growth(scope, facts)
                entities = self._entities(scope, episodes)
                relations = self._relations(scope, episodes)
                profile = self._update_profile(scope, episodes)
            # After every write in this pass has landed, never before: advancing on a pass that then
            # raises would have the next call skip the very episodes that failed to consolidate, which
            # is the caller-hands-the-termination-condition failure mode the interface docstring warns
            # about, reincarnated one layer down.
            self._advance_cursor(cursor_key, scanned)
            return self._report(
                scope,
                scanned,
                episodes,
                facts,
                merged,
                folded,
                entities,
                relations,
                reflection,
                summary,
                derived_by,
                supersede,
                profile,
            )

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        require_scope(scope, method="aconsolidate")
        with memory_span(
            "consolidate",
            tenant_id=scope.tenant_id,
            tier=self._consolidate_tier_label(tiers),
            store=self._store_label(),
        ):
            vector = self._check_consolidatable(tiers)
            instant = now_epoch_s()
            cursor_key = self._cursor_key(scope, CONSOLIDATION_SOURCE)
            scanned = [
                r.detached()
                for r in await vector.ascan(
                    scope,
                    CONSOLIDATION_SOURCE,
                    limit=self.max_episodes_per_pass,
                    after=self._scan_cursors.get(cursor_key),
                )
            ]
            assert_scope_isolation(scope, scanned, method="aconsolidate")
            episodes = await self._avisible_episodes(scope, scanned, instant)
            # PII containment (SM-X5 #141 round-1 review, finding #2): see the sync half's
            # comment for why this runs here. `_redact_facts`/`redact_records_for_writeback` are
            # pure functions with no I/O, so the sync helpers are reused as-is on this lane too.
            episodes = redact_records_for_writeback(episodes, self.config)
            facts, merged, folded, derived_by, supersede = await self._adistil_facts(scope, episodes)
            # Same fold-before-branch as the sync half, for the same reason -- see its comment.
            insight_facts, reflection = await self._areflect(scope, episodes)
            facts += insight_facts
            # See the sync half's comment: same fold-before-branch reasoning, one stage later.
            summary_facts, summary = await self._asummarize(scope, episodes)
            facts += summary_facts
            facts = self._redact_facts(facts)

            if facts:
                self._check_distilled(facts)
                to_vector, to_graph = self._fact_targets(facts)
                receipt = WriteReceipt(
                    accepted=len(facts),
                    event_ids=[f.record.id for f in facts],
                    tiers_written=[CONSOLIDATION_TARGET],
                )
                committed_any = False
                try:
                    for fact_scope, batch in self._by_scope(facts):
                        group_committed = False
                        try:
                            # See the sync half's comment: `fact_record` keeps the vector leg's shape
                            # in step with what `recall_facts` already returns from the graph leg.
                            if to_vector:
                                await vector.aupsert(fact_scope, [fact_record(f) for f in batch])
                                committed_any = True
                                group_committed = True
                            if to_graph and self.graph is not None:
                                _, failures = await self._gather_writes([self.graph.aupsert_facts(fact_scope, batch)])
                                self._raise_write_failure(failures, receipt)
                                committed_any = True
                                group_committed = True
                        finally:
                            # See the sync half's comment: `finally` so a group's own partial commit
                            # (vector landed, then its graph leg raised) is still counted, recorded
                            # under its own `fact_scope` right when it lands.
                            if group_committed:
                                self._record_fact_growth(fact_scope, batch)
                except PartialWriteError:
                    # Nothing left to record here: every group that committed already recorded its own
                    # growth above, inside the loop, before the raise.
                    raise
                except MemoryOperationError:
                    if not committed_any:
                        raise
                    raise PartialWriteError(receipt) from None
                except Exception as exc:
                    raise PartialWriteError(receipt) from exc
                try:
                    entities = await self._aentities(scope, episodes)
                    relations = await self._arelations(scope, episodes)
                    profile = await self._aupdate_profile(scope, episodes)
                except Exception as exc:
                    raise PartialWriteError(receipt) from exc
            else:
                # No `receipt` exists here to attach -- `facts` is empty, so no *semantic fact* was
                # committed above. `_entities`/`_relations` below can still write graph nodes/edges of
                # their own, uncounted by this branch's `PartialWriteError` handling -- a pre-existing
                # gap, pre-existing for `_entities` alone and now also covering `_relations`'s
                # write below -- this ticket does not widen the fix for either; tracked as
                # aicoe-tracker #186. A failure here is a plain refusal, matching
                # `write`'s rule: propagate bare, do not wrap. Deliberately parallel to the `if facts:`
                # branch above, not an oversight against it -- see the review-round-3 note in
                # `_entities`'s own docstring.
                #
                # `_aupdate_profile`'s write below carries the identical gap (QC round 3, PR #135):
                # its own `SemanticProfileStore.awrite` failure here is a plain refusal too, uncounted
                # by any receipt -- the same #186 scope-out, not a separate oversight for the profile
                # leg specifically.
                #
                # Recording a zero-accepted entry here (rather than skipping it) is what makes a scope
                # that never consolidates anything distinguishable from one that was never asked.
                self._record_fact_growth(scope, facts)
                entities = await self._aentities(scope, episodes)
                relations = await self._arelations(scope, episodes)
                profile = await self._aupdate_profile(scope, episodes)
            self._advance_cursor(cursor_key, scanned)
            return self._report(
                scope,
                scanned,
                episodes,
                facts,
                merged,
                folded,
                entities,
                relations,
                reflection,
                summary,
                derived_by,
                supersede,
                profile,
            )


@register_provider("co_located")
def _build_co_located(config: MemoryProviderConfig) -> MemoryProvider:
    """The only construction call site, for a real backend and an injected double alike.

    Both ``config.vector`` and ``config.graph`` resolve through ``egpt.registry.discovery.construct``
    -- ``vectorstore`` for one (SM-V1 #95) and ``graphdb`` for the other (SM-G1 #99) -- along the
    identical path, so the two legs cannot diverge. ``options`` keeps carrying an already-built
    substrate, which is how a test injects a double.
    """
    return CoLocatedMemoryProvider(
        # A `vectorstore` rather than a `vectordb`: the latter's backends answer `VectorDb`, whose
        # async methods carry the `async_` prefix rather than this seam's `a`, and which knows
        # nothing of a scope or a tier.
        vector=resolve_substrate(
            "vector",
            "vectorstore",
            config.vector,
            config.options.get("vector_substrate"),
            "vector_substrate",
            checked_vector,
            config.mode,
        ),
        graph=resolve_substrate(
            "graph",
            "graphdb",
            config.graph,
            config.options.get("graph_substrate"),
            "graph_substrate",
            checked_graph,
            config.mode,
        ),
        config=config,
        max_episodes_per_pass=config.options.get("max_episodes_per_pass", _MAX_EPISODES_PER_PASS),
        max_concurrent_writes=config.options.get("max_concurrent_writes", _MAX_CONCURRENT_WRITES),
        # Injected, not resolved by registry key: the pipeline needs a model and an embedder, and
        # naming those in provider config would put SM-V2's (#96) embedder selection here.
        entity_pipeline=config.options.get("entity_pipeline"),
        # Same reasoning as `entity_pipeline`, one line up: a model is all `RelationExtractionPipeline`
        # needs, but naming one here would put model selection in provider config.
        relation_pipeline=config.options.get("relation_pipeline"),
        # Same reasoning again: a model (wrapped in an `InsightSynthesizer`) is all
        # `ReflectionPipeline` needs, but naming one here would put model selection in provider
        # config.
        reflection_pipeline=config.options.get("reflection_pipeline"),
        # Same reasoning again: `FactExtractionPipeline` (SM-L3 #124) needs a model and the
        # already-resolved vector substrate, neither of which belongs in provider config.
        fact_pipeline=config.options.get("fact_pipeline"),
        # Same reasoning again: a `UserProfileStore` (SM-S3 #120) needs a model, and reusing the
        # seed's own store rather than naming a bespoke pipeline type keeps its schema/extraction
        # as the one place profile fields are defined.
        profile_store=config.options.get("profile_store"),
        # Same reasoning again: `SummaryPipeline` (SM-L8 #129) needs a model (wrapped in a
        # `SessionSummarizer`) and a `SummarizeStrategy`, neither of which belongs in provider
        # config.
        summary_pipeline=config.options.get("summary_pipeline"),
    )
