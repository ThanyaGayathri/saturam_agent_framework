"""Azure AI Search :class:`~egpt.memory.substrates.VectorSubstrate` (AZ-B, migration plan gap #3).

Registered as the ``"azure_ai_search"`` row in
``egpt.registry._declarations.vectorstores``, resolved by
``egpt.registry.discovery.construct("vectorstore", "azure_ai_search", **kwargs)`` exactly as
``"pgvector"`` is -- the whole point of the seam being ``egpt.memory.substrates.VectorSubstrate`` /
``HybridSearchSubstrate`` Protocols rather than a base class is that this module can satisfy them
structurally with no shared ancestor to :class:`~egpt.integrations.memory.vector.pgvector.pgvector.PgVectorMemorySubstrate`.

**The two decisions this backend embodies are made once, in
** ``cookbook/95_azure_migration/AZ_B_dimension_and_scoring_decisions.md`` **, not re-derived here:**

1. The embedding dimension is whatever :mod:`egpt.memory.embedding` resolves -- 768 for Mode A, the
   existing 1024 truncation for Mode B -- unchanged from the pgvector backend, even though the
   pgvector-specific 2000-dim HNSW ceiling that forced that truncation does not apply to Azure AI
   Search's own (much higher) vector-field limit. See that document's "Decision 1" for why the
   truncation stays despite the forcing reason going away.
2. :meth:`search_candidates` issues two independent single-leg queries -- vector-only and
   text-only -- and returns them unfused, exactly as :class:`~egpt.memory.substrates.HybridSearchSubstrate`
   requires. It does **not** use Azure AI Search's native hybrid/RRF query shape, because RRF
   returns one already-fused ranked list and this port's contract is two separate ones -- see that
   document's "Decision 2" for why the two are structurally incompatible, not merely a style choice.

**One shared index, not one index per tenant.** Unlike the pgvector backend -- which SM-R3
benchmarked and ratified a per-tenant physical partition for, because a shared-table ``tenant_id``
filter over one HNSW index measured recall collapsing from 0.80 to 0.22 as tenants went from 1 to
50 -- this backend uses one Azure AI Search index with every :class:`~egpt.memory.types.Scope` slot
as an OData ``$filter`` predicate. This is the pgvector backend's *rejected* shared-table shape,
carried over deliberately rather than by oversight: Azure AI Search's filtered vector search is a
different engine with different filter-pushdown behaviour than pgvector's post-HNSW filter, so it
is not assumed to degrade the same way -- but it is also not *measured*, because that measurement
needs a real Azure AI Search resource at a representative tenant count, which is not available in
the environment this module was built in. Recorded as an open question in the decisions doc and in
``cookbook/95_azure_migration/TEST_LOG.md`` rather than silently shipped as if it were known-good.
Per-tenant physical isolation (one index per tenant) is a real option Azure AI Search supports and
is available as a future change to :meth:`init_schema` if a benchmark later shows it is needed --
nothing here forecloses it.

**The Azure document key is derived, not the record's own ``id``.** ``VectorSubstrate.upsert``'s
dedup key is ``id``, unique **per tenant** -- matching
``PgVectorMemorySubstrate``'s own primary key, ``(tenant_id, id)`` (its ``upsert_statement``
docstring is explicit that this, not the full five-slot scope, is the primary key). Azure AI
Search's key field is global to the index and restricted to a narrow character set (letters,
digits, ``_``, ``-``, ``=``), so a record's caller-supplied ``id`` cannot be used as the Azure key
directly: two different tenants are free to mint the same ``id``, which a single shared index would
then collide on, and an arbitrary ``id`` need not even be in Azure's allowed key charset.
:func:`_document_key` resolves both problems by encoding ``(tenant_id, id)`` into a charset-safe,
deterministic key -- an *injective* encoding, so no two distinct pairs can ever land on the same
document (see that function's docstring for the length prefix that makes it injective, and
``test_document_key_is_injective_over_adversarial_separator_inputs`` for the proof), and
deterministic, so a re-upsert with the same ``(tenant_id, id)`` replaces the same document rather
than creating a duplicate, matching pgvector's ``ON CONFLICT`` semantics. The record's real ``id`` is also stored as an ordinary filterable field, and every read
(:meth:`get_record` included) matches on that field through the same scope-filter path every other
read uses -- never on the derived key alone -- because a call :class:`~egpt.memory.types.Scope`
may be **narrower** than the scope a record was written under (an unset slot means "any"), and the
derived key only encodes ``tenant_id``, not the other four slots a narrower-than-write-time filter
must still be able to check.
"""

import base64
import json
import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from egpt.memory.embedding import resolve_memory_embedder
from egpt.memory.errors import MemoryProviderConfigError
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    RetrievalCandidates,
    ScanCursor,
    Scope,
    Substrate,
    require_scope,
)
from egpt.metrics import ProviderMode
from egpt.utils.dttm import now_epoch_s
from egpt.utils.log import log_debug

from egpt.integrations.memory.vector.azure_ai_search import odata

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

try:
    from azure.core.credentials import AzureKeyCredential
    from azure.core.exceptions import HttpResponseError
    from azure.search.documents import SearchClient
    from azure.search.documents.aio import SearchClient as AsyncSearchClient
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.aio import SearchIndexClient as AsyncSearchIndexClient
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        HnswParameters,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SearchableField,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )
    from azure.search.documents.models import VectorizedQuery
except ImportError:
    raise ImportError(
        "`azure-search-documents` not installed. Please install using `pip install azure-search-documents`"
    )

__all__ = ["AzureAISearchMemorySubstrate"]

#: Azure AI Search's own service defaults for HNSW, per Microsoft Learn's vector index
#: documentation -- used as this backend's defaults rather than pgvector's (``m=16``,
#: ``ef_construction=200``), because pgvector's `HnswParameters` constraints
#: (``m`` in 4..10, ``ef_construction``/``ef_search`` in 100..1000) do not admit pgvector's own
#: numbers at all; picking Azure's documented defaults is the closer inheritance.
_DEFAULT_M = 4
_DEFAULT_EF_CONSTRUCTION = 400
_DEFAULT_EF_SEARCH = 500

#: Saturation constant turning Azure AI Search's unbounded BM25 ``@search.score`` into the
#: ``[0.0, 1.0]`` similarity the port requires, as ``r / (r + k)`` -- reused verbatim from
#: ``pgvector.pgvector._RANK_SATURATION`` rather than re-derived. See
#: ``AZ_B_dimension_and_scoring_decisions.md``'s "Decision 2" for why the same constant crosses
#: backends: it is a monotonic squashing choice, not a calibration against either backend's real
#: BM25/``ts_rank_cd`` score distribution, and SM-H2 (#132) -- the fusion stage -- is where that
#: calibration belongs for either.
_RANK_SATURATION = 0.1

#: Azure AI Search's own ceiling on a vector field's dimensionality with HNSW (Microsoft Learn,
#: "Vector Index Limits" -- fetched and confirmed in this session; there is no narrower number in
#: the SDK's own type definitions). Checked here for the same reason
#: ``pgvector.sql.MAX_HNSW_DIM`` is: refusing a too-wide embedder at construction, naming the field,
#: beats discovering it as an opaque service error from the first ``create_or_update_index`` call.
#: Not the number this backend actually writes at (see :data:`_DEFAULT_M` and the module docstring
#: -- Mode A/B stay at 768/1024), only the ceiling a misconfigured embedder could exceed.
MAX_VECTOR_DIM = 3072

#: The stored (non-vector) fields, in a fixed order both the write path and the read path use --
#: matching ``pgvector.sql.COLUMNS``'s role.
_TEXT_FIELD = "content"
_VECTOR_FIELD = "embedding"

#: HTTP statuses a retry *can* fix, so they must not become ``MemoryOperationError``. The split
#: pgvector expresses by SQLAlchemy exception type has no exception-type equivalent here:
#: ``HttpResponseError`` is azure-core's base class for every HTTP status alike, a malformed filter
#: (400) and a throttled request (429) included, so classifying on the class would call a rate
#: limit permanent. The status code is the only place the distinction lives, hence a code set
#: rather than an exception tuple. 408/429 are Azure AI Search's own documented timeout/throttle
#: responses; 500/502/503/504 are the transient server family. Everything else -- including a
#: status this module does not recognise, and including ``HttpResponseError`` with no status at all
#: -- is left to propagate unchanged rather than be asserted permanent, because the memory provider
#: reads ``MemoryOperationError`` as "this will never work"
#: (``co_located._raise_write_failure``) and would tell a caller to give up on a network blip.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

#: Statuses ``create_or_update_index`` uses to refuse an index definition it cannot reconcile with
#: an existing index (a changed vector dimension, a changed algorithm) -- the only statuses
#: :meth:`AzureAISearchMemorySubstrate._reject_schema_drift` will read as schema drift. An auth
#: failure (401/403), a throttle (429) or an outage (503) on the same call is *not* drift and must
#: not send an operator off to plan a data migration.
_SCHEMA_CONFLICT_STATUS = frozenset({400, 409})


def _is_retryable(exc: "HttpResponseError") -> bool:
    """Whether this HTTP failure is one a retry could fix -- see :data:`_RETRYABLE_STATUS`.

    An unknown or absent status counts as retryable: the cost of retrying something permanent is a
    wasted round trip, the cost of the reverse is a caller abandoning a write over a rate limit.
    """
    status = getattr(exc, "status_code", None)
    return status is None or status in _RETRYABLE_STATUS


def _document_key(tenant_id: str, id: str) -> str:
    """The Azure AI Search document key for ``(tenant_id, id)`` -- see the module docstring.

    URL-safe base64 of the UTF-8 encoding of ``f"{len(tenant_id)}:{tenant_id}|{id}"``. The base64
    alphabet (``[A-Za-z0-9_-]`` plus ``=`` padding) is a subset of Azure AI Search's allowed key
    charset, so no further escaping of the *output* is needed -- but the base64 alphabet says
    nothing about the *inputs*, which ``Scope.__post_init__`` only checks for non-blankness and so
    may contain any character including the ``|`` separator. The length prefix is what makes the
    join injective rather than merely charset-safe: the leading run of digits ends at the first
    non-digit (the ``:``), giving ``len(tenant_id)`` unambiguously; the next ``len(tenant_id)``
    characters are ``tenant_id``; the character after those is the separator; the remainder is
    ``id``. Every pre-image is therefore recoverable, so two distinct ``(tenant_id, id)`` pairs
    cannot encode to the same key -- without the prefix they could (``("acme", "x|e1")`` and
    ``("acme|x", "e1")`` both joined to ``acme|x|e1``), and because ``upsert`` writes through
    ``merge_or_upload_documents`` that collision would have merged one tenant's fields into
    another tenant's document in place.

    Deterministic, so a re-upsert of the same pair encodes to the same key and replaces the same
    document, matching pgvector's ``ON CONFLICT (tenant_id, id)``.
    """
    return base64.urlsafe_b64encode(f"{len(tenant_id)}:{tenant_id}|{id}".encode("utf-8")).decode("ascii")


def _as_json(value: Any, field: str) -> str:
    """Serialize a nested value, in this module's error envelope rather than the stdlib's."""
    try:
        return json.dumps(value)
    except TypeError as exc:
        raise MemoryOperationError(f"{field} is not JSON-serializable ({exc}).", field_name=field) from exc


def _from_json(value: Any, field: str, record_id: Any) -> Dict[str, Any]:
    """Decode a stored JSON-text field into the mapping ``MemoryRecord`` expects."""
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(json.loads(value))
    except (TypeError, ValueError) as exc:
        raise MemoryOperationError(
            f"{field} on record {record_id!r} did not decode to a JSON object ({exc}).",
            field_name=field,
        ) from exc


def _topics_from_json(value: Any, record_id: Any) -> Optional[List[str]]:
    if not value:
        return None
    if isinstance(value, list):
        return [str(topic) for topic in value]
    try:
        return [str(topic) for topic in json.loads(value)]
    except (TypeError, ValueError) as exc:
        raise MemoryOperationError(
            f"topics on record {record_id!r} is not a readable JSON array ({exc}).", field_name="topics"
        ) from exc


def _lexical_similarity(rank: float) -> float:
    """Azure's unbounded BM25 ``@search.score`` as the ``[0.0, 1.0]`` similarity the port requires.

    ``r / (r + k)`` is strictly increasing on ``r >= 0``, so it cannot reorder the list it is
    applied to -- see :data:`_RANK_SATURATION`. Azure's BM25 score is documented as non-negative,
    so unlike ``pgvector.pgvector._lexical_similarity`` this does not *clamp* a negative rank; it
    refuses one. The refusal is explicit rather than left to the division, because the division is
    not the safety net it looks like: for ``-k < r < 0`` the expression returns a negative float
    silently -- violating the ``[0.0, 1.0]`` contract :class:`~egpt.memory.types.MemoryRecord`'s
    ``score`` carries -- and only ``r == -k`` exactly raises, a ``ZeroDivisionError`` naming nothing
    a caller can act on.
    """
    if rank < 0:
        raise MemoryOperationError(
            f"Azure AI Search returned a negative BM25 @search.score ({rank}); the lexical leg's "
            "similarity contract is [0.0, 1.0] and there is no defensible mapping from a negative "
            "rank onto it. Azure documents this score as non-negative, so this is a service or API "
            "version contract change, not a tunable.",
            field_name="score",
        )
    return rank / (rank + _RANK_SATURATION)


def _tiebreak(record: MemoryRecord) -> Tuple[int, str, str]:
    """How either leg orders records its primary measure scores equally -- matches
    ``pgvector.pgvector._tiebreak`` exactly, including never breaking a tie on a uuid."""
    return (record.created_at or 0, record.content, record.id)


class AzureAISearchMemorySubstrate:
    """Memory vectors in one Azure AI Search index, scoped by an OData ``$filter`` per call.

    Sync and async are both native: the async half drives ``azure.search.documents.aio``'s own
    client rather than wrapping the sync client in a thread pool, matching this repo's standing
    rule that an ``async def`` wearing a thread hop is not a real async variant.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        credential: Optional[Union["AzureKeyCredential", "TokenCredential"]] = None,
        api_key: Optional[str] = None,
        index_name: str = "egpt-memory",
        embedder: Optional[Any] = None,
        embedder_options: Optional[Dict[str, Any]] = None,
        dim: Optional[int] = None,
        embedding_version: Optional[str] = None,
        mode: ProviderMode = ProviderMode.LOCAL,
        m: int = _DEFAULT_M,
        ef_construction: int = _DEFAULT_EF_CONSTRUCTION,
        ef_search: int = _DEFAULT_EF_SEARCH,
        auto_init_schema: bool = True,
    ) -> None:
        resolved_endpoint = endpoint or os.getenv("EGPT_MEMORY_AZURE_SEARCH_ENDPOINT")
        if not resolved_endpoint:
            raise MemoryProviderConfigError(
                "AzureAISearchMemorySubstrate needs a service endpoint, from endpoint or "
                "EGPT_MEMORY_AZURE_SEARCH_ENDPOINT.",
                field_name="endpoint",
            )
        self.endpoint = resolved_endpoint
        # `Any`, not the sync/async credential union: the SDK's sync `SearchClient` types its
        # `credential` keyword as `AzureKeyCredential | TokenCredential` and the async client as
        # `AzureKeyCredential | AsyncTokenCredential` -- two different unions for what is, for the
        # `AzureKeyCredential` path this module actually tests, the identical object passed to
        # both. Typing this attribute narrowly would force choosing one client's union over the
        # other's for no behavioural reason.
        self.credential: Any = self._resolved_credential(credential, api_key)
        self.index_name = self._checked_index_name(index_name)

        # The pipeline decides the model, its dimension and the version tag; this store only holds
        # the field width they imply -- same division of responsibility as
        # `PgVectorMemorySubstrate.__init__`, and the same reason: a re-embedding migration is
        # SM-V2's (#96), not a per-write coercion here.
        self.mode = mode
        self.pipeline = resolve_memory_embedder(mode, embedder, embedder_options, dim, dimensions_field="dim")
        self.embedder = self.pipeline.embedder
        self.dim = self.pipeline.dimensions
        if not 0 < self.dim <= MAX_VECTOR_DIM:
            raise MemoryProviderConfigError(
                f"The resolved embedder reports {self.dim} dimensions; Azure AI Search's vector "
                f"fields support at most {MAX_VECTOR_DIM}. A deployment above that has no field "
                "width this backend can declare -- fix the embedder or its `dimensions` override.",
                field_name="dim",
            )
        self.embedding_version = embedding_version if embedding_version is not None else self.pipeline.embedding_version

        self.m, self.ef_construction, self.ef_search = self._checked_hnsw_params(m, ef_construction, ef_search)

        self._client: Optional[SearchClient] = None
        self._aclient: Optional[AsyncSearchClient] = None
        self._index_client: Optional[SearchIndexClient] = None
        self._aindex_client: Optional[AsyncSearchIndexClient] = None
        if auto_init_schema:
            self.init_schema()

    # -- construction-time validation -----------------------------------------

    @staticmethod
    def _resolved_credential(
        credential: Optional[Union["AzureKeyCredential", "TokenCredential"]], api_key: Optional[str]
    ) -> Union["AzureKeyCredential", "TokenCredential"]:
        """A caller-supplied credential wins; else an API key from the argument or the environment.

        Deliberately narrower than pgvector's URL-only construction: this module depends only on
        ``azure-search-documents``, not ``azure-identity``, so it cannot construct a
        ``DefaultAzureCredential`` itself for a managed-identity deployment. A caller wanting that
        passes an already-constructed ``TokenCredential`` through ``credential=`` -- the type this
        method accepts either way -- rather than this backend growing an optional dependency on
        ``azure-identity`` to build one internally.
        """
        if credential is not None:
            return credential
        resolved_key = api_key or os.getenv("EGPT_MEMORY_AZURE_SEARCH_API_KEY")
        if not resolved_key:
            raise MemoryProviderConfigError(
                "AzureAISearchMemorySubstrate needs a credential: pass credential= (an "
                "AzureKeyCredential or TokenCredential), or api_key=/EGPT_MEMORY_AZURE_SEARCH_API_KEY "
                "for key-based auth.",
                field_name="credential",
            )
        return AzureKeyCredential(resolved_key)

    @staticmethod
    def _checked_index_name(index_name: str) -> str:
        """Azure AI Search index names: lowercase letters, digits, dashes; 2-128 characters; must
        start and end with a letter or digit. Checked here so a bad name fails at construction with
        a field name, not three layers into a `create_or_update_index` call.
        """
        # At least 2 characters: a bare `[a-z0-9](...)?` with the optional group empty would admit
        # a single character, one below Azure's own minimum.
        if not isinstance(index_name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,126}[a-z0-9]", index_name):
            raise MemoryProviderConfigError(
                f"index_name={index_name!r} is not a legal Azure AI Search index name: lowercase "
                "letters, digits and dashes, 2-128 characters, starting and ending with a letter "
                "or digit.",
                field_name="index_name",
            )
        return index_name

    @staticmethod
    def _checked_hnsw_params(m: int, ef_construction: int, ef_search: int) -> Tuple[int, int, int]:
        """Azure AI Search's own `HnswParameters` bounds, checked before they reach the SDK's own
        (differently-shaped) validation error."""
        checks = (("m", m, 4, 10), ("ef_construction", ef_construction, 100, 1000), ("ef_search", ef_search, 100, 1000))
        for field_name, value, low, high in checks:
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise MemoryProviderConfigError(
                    f"{field_name} must be an integer in {low}..{high}, got {value!r}.", field_name=field_name
                )
        return m, ef_construction, ef_search

    # -- plumbing -------------------------------------------------------------

    def _search_client(self) -> SearchClient:
        if self._client is None:
            self._client = SearchClient(endpoint=self.endpoint, index_name=self.index_name, credential=self.credential)
        return self._client

    def _async_search_client(self) -> AsyncSearchClient:
        """Built on first use, not in ``__init__`` -- matches
        ``PgVectorMemorySubstrate._async_engine``'s own reasoning: an async client is safest built
        against the loop that will actually use it, not the (possibly absent) loop present at
        construction time.
        """
        if self._aclient is None:
            self._aclient = AsyncSearchClient(
                endpoint=self.endpoint, index_name=self.index_name, credential=self.credential
            )
        return self._aclient

    def _index_search_client(self) -> SearchIndexClient:
        if self._index_client is None:
            self._index_client = SearchIndexClient(endpoint=self.endpoint, credential=self.credential)
        return self._index_client

    def _async_index_search_client(self) -> AsyncSearchIndexClient:
        if self._aindex_client is None:
            self._aindex_client = AsyncSearchIndexClient(endpoint=self.endpoint, credential=self.credential)
        return self._aindex_client

    @staticmethod
    @contextmanager
    def _translated(operation: str) -> Iterator[None]:
        """The one place a service failure becomes this module's error, or deliberately does not.

        Matches ``PgVectorMemorySubstrate._translated``'s retryability *split* -- deterministic
        failures become ``MemoryOperationError``, retryable ones propagate -- but not its
        mechanism: pgvector can name the deterministic SQLAlchemy exception types, whereas
        azure-core raises one class for every status, so the decision is made on the status code
        (:func:`_is_retryable`). A non-HTTP failure (``ServiceRequestError``, a timeout, an
        unreachable endpoint) is not caught here at all and propagates as itself.
        """
        try:
            yield
        except HttpResponseError as exc:
            if _is_retryable(exc):
                raise
            raise MemoryOperationError(f"Azure AI Search refused {operation}: {exc}", field_name=operation) from exc

    def close(self) -> None:
        """Close the sync clients. Mirrors ``PgVectorMemorySubstrate.close``'s all-or-nothing
        refusal when the async half is open, for the identical reason: an async client's transport
        belongs to the loop that opened it, so a sync call cannot release it."""
        if self._aclient is not None or self._aindex_client is not None:
            raise MemoryOperationError(
                "This substrate has an open async client, and close() cannot release it: its "
                "connections belong to the loop that opened them. Call aclose() from the same "
                "async context; it closes both.",
                field_name="close",
            )
        if self._client is not None:
            self._client.close()
        if self._index_client is not None:
            self._index_client.close()

    async def aclose(self) -> None:
        """Closes both halves -- see :meth:`close`."""
        if self._aclient is not None:
            await self._aclient.close()
            self._aclient = None
        if self._aindex_client is not None:
            await self._aindex_client.close()
            self._aindex_client = None
        if self._client is not None:
            self._client.close()
        if self._index_client is not None:
            self._index_client.close()

    # -- schema -----------------------------------------------------------------

    def _index_definition(self) -> "SearchIndex":
        """The index this backend needs -- built fresh each time, cheap and always current with
        ``self.dim``/``self.m``/etc., matching ``PgVectorMemorySubstrate._schema_statements`` being
        computed once from the same inputs.
        """
        profile_name = "egpt-memory-vector-profile"
        algorithm_name = "egpt-memory-hnsw"
        fields: List[SearchField] = [
            SimpleField(name="doc_key", type=SearchFieldDataType.String, key=True),
            SimpleField(name="id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="tenant_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="agent_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="user_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="team_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="session_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="tier", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name=_TEXT_FIELD, type=SearchFieldDataType.String, filterable=False),
            SimpleField(name="topics_json", type=SearchFieldDataType.String, filterable=False),
            SimpleField(name="provenance_json", type=SearchFieldDataType.String, filterable=False),
            SimpleField(name="metadata_json", type=SearchFieldDataType.String, filterable=False),
            SimpleField(name="embedding_version", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="t_valid", type=SearchFieldDataType.Int64, filterable=True, sortable=True),
            SimpleField(name="t_invalid", type=SearchFieldDataType.Int64, filterable=True, sortable=True),
            SimpleField(name="created_at", type=SearchFieldDataType.Int64, filterable=True, sortable=True),
            SimpleField(name="expired_at", type=SearchFieldDataType.Int64, filterable=True, sortable=True),
            SimpleField(name="superseded_by", type=SearchFieldDataType.String, filterable=False),
            SearchField(
                name=_VECTOR_FIELD,
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self.dim,
                vector_search_profile_name=profile_name,
            ),
        ]
        vector_search = VectorSearch(
            profiles=[VectorSearchProfile(name=profile_name, algorithm_configuration_name=algorithm_name)],
            algorithms=[
                HnswAlgorithmConfiguration(
                    name=algorithm_name,
                    parameters=HnswParameters(
                        m=self.m, ef_construction=self.ef_construction, ef_search=self.ef_search, metric="cosine"
                    ),
                )
            ],
        )
        return SearchIndex(name=self.index_name, fields=fields, vector_search=vector_search)

    def _reject_schema_drift(self, exc: HttpResponseError) -> None:
        """Translate a *rejected index definition* into a configuration error naming the field,
        instead of letting Azure's own message -- which names neither this backend's argument nor
        this module's error envelope -- surface unshaped.

        Azure AI Search refuses to change a vector field's dimensions (and several other
        properties) on an existing index via ``create_or_update_index``; the honest read of that
        refusal is close to ``PgVectorMemorySubstrate._reject_schema_drift``'s: the index exists
        under a different configuration than this instance is asking for, and reconciling that is a
        migration, not a restart.

        **Where this is weaker than pgvector's, stated rather than papered over.** pgvector reads
        the live schema back and diffs it, so it knows drift happened; this backend has only the
        service's rejection of the DDL call to go on, and cannot tell "the existing index disagrees
        about ``dim``" from "the definition this module built is invalid" -- both are a 400. So the
        message names both possibilities. What it no longer does is claim drift for *any* HTTP
        failure: only :data:`_SCHEMA_CONFLICT_STATUS` reaches here (see :meth:`init_schema`), so an
        auth failure, a throttle or an outage during schema init propagates to :meth:`_translated`
        and is classified by retryability like any other call, rather than misdirecting an operator
        toward a data migration. Distinguishing the two 400 cases needs the service's own error
        code, which cannot be pinned to a verified string in an environment with no live Azure AI
        Search resource -- recorded as an open question in ``cookbook/95_azure_migration/TEST_LOG.md``
        rather than guessed at here.
        """
        raise MemoryProviderConfigError(
            f"Azure AI Search rejected the index definition for {self.index_name!r} at "
            f"dim={self.dim}, m={self.m}, ef_construction={self.ef_construction}: {exc}. Either "
            "the index already exists under a configuration create_or_update_index cannot "
            "reconcile to these values -- Azure AI Search refuses in-place changes to a vector "
            "field's dimensions and several other index-level properties, so moving to a new "
            "configuration is a migration (new index, re-embed, cut over), not a restart -- or the "
            "definition itself was refused. The service message above distinguishes them.",
            field_name="dim",
        ) from exc

    def init_schema(self) -> None:
        """Create or reconcile the index, idempotently -- the async-unaware entry point
        ``__init__`` calls when ``auto_init_schema`` is set."""
        with self._translated("schema init"):
            try:
                self._index_search_client().create_or_update_index(self._index_definition())
            except HttpResponseError as exc:
                if getattr(exc, "status_code", None) not in _SCHEMA_CONFLICT_STATUS:
                    raise
                self._reject_schema_drift(exc)
        log_debug(f"AzureAISearchMemorySubstrate schema ready on index {self.index_name!r}")

    async def ainit_schema(self) -> None:
        """Async twin of :meth:`init_schema`."""
        with self._translated("schema init"):
            try:
                await self._async_index_search_client().create_or_update_index(self._index_definition())
            except HttpResponseError as exc:
                if getattr(exc, "status_code", None) not in _SCHEMA_CONFLICT_STATUS:
                    raise
                self._reject_schema_drift(exc)
        log_debug(f"AzureAISearchMemorySubstrate schema ready on index {self.index_name!r}")

    # -- row mapping ------------------------------------------------------------

    def _reject_foreign_scope(self, scope: Scope, record: MemoryRecord) -> None:
        """A record is written under the scope it carries; a mismatch is refused, never
        reinterpreted -- matches ``PgVectorMemorySubstrate._reject_foreign_scope`` exactly,
        including logging only slot names and never values.
        """
        if record.scope != scope:
            differing = [
                slot
                for slot in ("tenant_id", "agent_id", "user_id", "team_id", "session_id")
                if getattr(record.scope, slot, None) != getattr(scope, slot, None)
            ]
            raise MemoryOperationError(
                f"Record {record.id!r} carries a scope that does not match the write scope; "
                f"{len(differing)} slot(s) differ: {', '.join(differing)}. Batch per scope rather "
                "than leaving it to the backend.",
                field_name="scope",
            )

    def _document(self, scope: Scope, record: MemoryRecord, embedding: Sequence[float]) -> Dict[str, Any]:
        if len(embedding) != self.dim:
            raise MemoryOperationError(
                f"Embedding for record {record.id!r} has {len(embedding)} dimensions; this index's "
                f"vector field is {self.dim}-wide. Re-embedding at a new dimension is a versioned "
                "migration (SM-V2 #96), not a per-write coercion.",
                field_name="embedding",
            )
        return {
            "doc_key": _document_key(scope.tenant_id, record.id),
            "id": record.id,
            "tenant_id": scope.tenant_id,
            "agent_id": scope.agent_id,
            "user_id": scope.user_id,
            "team_id": scope.team_id,
            "session_id": scope.session_id,
            "tier": record.tier.value,
            _TEXT_FIELD: record.content,
            "topics_json": None if record.topics is None else _as_json(list(record.topics), "topics"),
            "provenance_json": _as_json(record.provenance, "provenance"),
            "metadata_json": _as_json(record.metadata, "metadata"),
            "embedding_version": self.embedding_version,
            "t_valid": record.t_valid,
            "t_invalid": record.t_invalid,
            "created_at": record.created_at if record.created_at is not None else now_epoch_s(),
            "expired_at": record.expired_at,
            "superseded_by": record.superseded_by,
            _VECTOR_FIELD: list(embedding),
        }

    @staticmethod
    def _record(hit: Dict[str, Any], score: Optional[float]) -> MemoryRecord:
        return MemoryRecord(
            id=hit["id"],
            tier=MemoryTier(hit["tier"]),
            content=hit[_TEXT_FIELD],
            scope=Scope(
                tenant_id=hit["tenant_id"],
                agent_id=hit.get("agent_id"),
                user_id=hit.get("user_id"),
                team_id=hit.get("team_id"),
                session_id=hit.get("session_id"),
            ),
            # `score` obeys the port's contract -- similarity in [0.0, 1.0], higher better --
            # decided by the caller: the vector leg's Azure @search.score already is one (see the
            # decisions doc's Decision 2), the lexical leg's goes through `_lexical_similarity`
            # first, and `scan`/`get_record` pass `None` for an unranked read.
            score=score,
            substrate=Substrate.VECTOR,
            topics=_topics_from_json(hit.get("topics_json"), hit.get("id")),
            provenance=_from_json(hit.get("provenance_json"), "provenance", hit.get("id")),
            t_valid=hit.get("t_valid"),
            t_invalid=hit.get("t_invalid"),
            created_at=hit.get("created_at"),
            expired_at=hit.get("expired_at"),
            superseded_by=hit.get("superseded_by"),
            metadata=_from_json(hit.get("metadata_json"), "metadata", hit.get("id")),
        )

    @staticmethod
    def _ranked(hits: List[Dict[str, Any]], scores: List[float]) -> List[MemoryRecord]:
        """Order a ranked page deterministically -- matches ``PgVectorMemorySubstrate._ranked``'s
        tiebreak, but does **not** re-sort by the primary measure: both legs' Azure queries already
        return their page in server-ranked order, and re-sorting here would only be needed to break
        ties, which :func:`_tiebreak` still does.
        """
        records = [AzureAISearchMemorySubstrate._record(hit, score) for hit, score in zip(hits, scores)]
        # Stable sort on the tiebreak key alone preserves the server's relative order for any two
        # records the tiebreak key does not itself distinguish -- i.e. it only reorders genuine
        # ties, the same guarantee `_tiebreak` gives pgvector's already-`ORDER BY`-sorted page.
        return [record for _, record in sorted(zip((_tiebreak(r) for r in records), records), key=lambda pair: pair[0])]

    # -- writes -------------------------------------------------------------

    def _checked(self, scope: Scope, records: Sequence[MemoryRecord]) -> List[MemoryRecord]:
        checked = list(records)
        for record in checked:
            self._reject_foreign_scope(scope, record)
        return checked

    @staticmethod
    def _reject_failed_documents(results: Any, documents: List[Dict[str, Any]], *, operation: str) -> None:
        """Refuse a *partially* successful batch -- the failure mode ``merge_or_upload_documents``
        reports in its return value rather than by raising.

        Azure AI Search's batch indexing API is documented non-atomic: a per-document rejection
        (oversized field, illegal value, a key the service will not accept) comes back as one
        ``IndexingResult`` with ``succeeded=False`` inside an otherwise successful response, and
        only a whole-request failure raises ``HttpResponseError``. Discarding the return value
        therefore reports a silently dropped record as a landed write -- strictly weaker than
        ``PgVectorMemorySubstrate.upsert``'s one transactional statement. This backend cannot make
        the batch atomic (Azure AI Search has no multi-document transaction to reach for), so the
        honest substitute is to refuse loudly and name what was dropped, leaving the caller to
        retry the batch rather than believe it landed.

        Pure computation over an already-materialised response, so there is no async twin to add:
        both :meth:`upsert` and :meth:`aupsert` call this one.
        """
        failed = [result for result in (results or ()) if not getattr(result, "succeeded", True)]
        if not failed:
            return
        # The service reports failures by the derived Azure key; the caller only knows its own
        # record ids, so map back rather than printing a base64 blob at them.
        record_ids = {document["doc_key"]: document["id"] for document in documents}
        detail = "; ".join(
            f"{record_ids.get(getattr(result, 'key', None), getattr(result, 'key', None))!r}: "
            f"{getattr(result, 'error_message', None) or getattr(result, 'status_code', None)}"
            for result in failed
        )
        raise MemoryOperationError(
            f"Azure AI Search accepted the {operation} request but rejected {len(failed)} of "
            f"{len(documents)} document(s), which its batch indexing API reports per document "
            f"instead of raising: {detail}. The rejected record(s) are not in the index; retry the "
            "batch rather than treating this write as landed.",
            field_name=operation,
        )

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        if not records:
            return
        checked = self._checked(scope, records)
        embeddings = self.pipeline.embed_texts([record.content for record in checked], scope=scope)
        documents = [self._document(scope, record, embedding) for record, embedding in zip(checked, embeddings)]
        with self._translated("upsert"):
            results = self._search_client().merge_or_upload_documents(documents=documents)
        self._reject_failed_documents(results, documents, operation="upsert")

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        if not records:
            return
        checked = self._checked(scope, records)
        embeddings = await self.pipeline.aembed_texts([record.content for record in checked], scope=scope)
        documents = [self._document(scope, record, embedding) for record, embedding in zip(checked, embeddings)]
        with self._translated("upsert"):
            results = await self._async_search_client().merge_or_upload_documents(documents=documents)
        self._reject_failed_documents(results, documents, operation="upsert")

    # -- reads ----------------------------------------------------------------

    @staticmethod
    def _searchable(query: MemoryQuery, tier: Optional[MemoryTier]) -> Optional[List[str]]:
        """The tier set to search, or ``None`` when the query cannot match anything -- matches
        ``PgVectorMemorySubstrate._searchable`` exactly, including the three cases it answers
        without a round trip."""
        if not query.limit or not query.text.strip():
            return None
        tiers = [tier.value] if tier is not None else [t.value for t in query.resolved_tiers()]
        return tiers or None

    def _vector_query(self, embedding: Sequence[float], limit: int) -> "VectorizedQuery":
        return VectorizedQuery(vector=list(embedding), k_nearest_neighbors=limit, fields=_VECTOR_FIELD)

    def search(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        tiers = self._searchable(query, tier)
        if tiers is None:
            return []
        embedding = self.pipeline.embed_query(query.text, scope=scope)
        filter_ = odata.search_filter(scope, tiers, query.window(), method="search")
        with self._translated("search"):
            results = self._search_client().search(
                search_text=None,
                vector_queries=[self._vector_query(embedding, query.limit)],
                filter=filter_,
                top=query.limit,
            )
            hits = list(results)
        scores = [float(hit["@search.score"]) for hit in hits]
        return self._ranked(hits, scores)

    async def asearch(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        tiers = self._searchable(query, tier)
        if tiers is None:
            return []
        embedding = await self.pipeline.aembed_query(query.text, scope=scope)
        filter_ = odata.search_filter(scope, tiers, query.window(), method="asearch")
        with self._translated("search"):
            results = await self._async_search_client().search(
                search_text=None,
                vector_queries=[self._vector_query(embedding, query.limit)],
                filter=filter_,
                top=query.limit,
            )
            hits = [hit async for hit in results]
        scores = [float(hit["@search.score"]) for hit in hits]
        return self._ranked(hits, scores)

    # -- hybrid candidates (HybridSearchSubstrate) -----------------------------

    def _lexical_scores(self, hits: List[Dict[str, Any]]) -> List[float]:
        return [_lexical_similarity(float(hit["@search.score"])) for hit in hits]

    def search_candidates(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> RetrievalCandidates:
        """Both ranked legs, unfused -- see the module docstring's Decision 2 for why this issues
        two independent single-leg queries rather than one native hybrid/RRF call.

        **No cross-leg snapshot isolation**, unlike ``PgVectorMemorySubstrate.search_candidates``'s
        pinned ``REPEATABLE READ`` transaction: Azure AI Search's REST API has no comparable
        multi-request transaction, so a document committed between the two legs here may be visible
        to one and not the other. Recorded as a real, not cosmetic, behavioural gap from the
        pgvector backend in ``TEST_LOG.md`` rather than silently accepted.
        """
        require_scope(scope, method="search_candidates")
        tiers = self._searchable(query, tier)
        if tiers is None:
            return RetrievalCandidates()
        embedding = self.embedder.get_embedding(query.text)
        filter_ = odata.search_filter(scope, tiers, query.window(), method="search_candidates")
        with self._translated("search_candidates"):
            vector_hits = list(
                self._search_client().search(
                    search_text=None,
                    vector_queries=[self._vector_query(embedding, query.limit)],
                    filter=filter_,
                    top=query.limit,
                )
            )
            lexical_hits = list(
                self._search_client().search(
                    search_text=query.text,
                    vector_queries=None,
                    search_fields=[_TEXT_FIELD],
                    filter=filter_,
                    top=query.limit,
                )
            )
        vector_scores = [float(hit["@search.score"]) for hit in vector_hits]
        return RetrievalCandidates(
            vector=self._ranked(vector_hits, vector_scores),
            lexical=self._ranked(lexical_hits, self._lexical_scores(lexical_hits)),
        )

    async def asearch_candidates(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> RetrievalCandidates:
        """Async twin of :meth:`search_candidates`, making the same two-query decision."""
        require_scope(scope, method="asearch_candidates")
        tiers = self._searchable(query, tier)
        if tiers is None:
            return RetrievalCandidates()
        embedding = await self.embedder.async_get_embedding(query.text)
        filter_ = odata.search_filter(scope, tiers, query.window(), method="asearch_candidates")
        with self._translated("search_candidates"):
            vector_results = await self._async_search_client().search(
                search_text=None,
                vector_queries=[self._vector_query(embedding, query.limit)],
                filter=filter_,
                top=query.limit,
            )
            vector_hits = [hit async for hit in vector_results]
            lexical_results = await self._async_search_client().search(
                search_text=query.text,
                vector_queries=None,
                search_fields=[_TEXT_FIELD],
                filter=filter_,
                top=query.limit,
            )
            lexical_hits = [hit async for hit in lexical_results]
        vector_scores = [float(hit["@search.score"]) for hit in vector_hits]
        return RetrievalCandidates(
            vector=self._ranked(vector_hits, vector_scores),
            lexical=self._ranked(lexical_hits, self._lexical_scores(lexical_hits)),
        )

    # -- scan / get_record ------------------------------------------------------

    @staticmethod
    def _scan_filter(scope: Scope, tier: MemoryTier, after: Optional[ScanCursor], *, method: str) -> str:
        clauses = [odata.scope_filter(scope, method=method), f"tier eq '{odata.escape_odata_literal(tier.value)}'"]
        if after is not None:
            after_created_at, after_id = after
            # `ScanCursor` is a bare `Tuple[int, str]` alias with no runtime enforcement, and
            # `after_created_at` reaches the filter as a raw numeric literal -- no quoting to
            # escape, so the declared type is the only thing keeping arbitrary text out of the
            # filter there. Checked rather than trusted, matching `ValidWindow.__post_init__`, which
            # raises for a non-`int` bound for the identical reason and excludes `bool` the same way
            # (`True` would otherwise emit `created_at gt True`).
            if not isinstance(after_created_at, int) or isinstance(after_created_at, bool):
                raise MemoryOperationError(
                    f"A scan cursor's created_at must be an integer, got {after_created_at!r}. The "
                    "cursor is a (created_at, id) pair as a previous scan returned it, not a "
                    "free-form filter value.",
                    field_name="after",
                )
            if not isinstance(after_id, str):
                raise MemoryOperationError(
                    f"A scan cursor's id must be a string, got {after_id!r}.", field_name="after"
                )
            escaped_id = odata.escape_odata_literal(after_id)
            clauses.append(
                f"(created_at gt {after_created_at} or (created_at eq {after_created_at} and id gt '{escaped_id}'))"
            )
        return " and ".join(clauses)

    def scan(
        self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None, after: Optional[ScanCursor] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        if limit == 0:
            return []
        filter_ = self._scan_filter(scope, tier, after, method="scan")
        with self._translated("scan"):
            results = self._search_client().search(
                search_text=None,
                filter=filter_,
                order_by=["created_at asc", "id asc"],
                top=limit,
            )
            hits = list(results)
        return [self._record(hit, None) for hit in hits]

    async def ascan(
        self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None, after: Optional[ScanCursor] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        if limit == 0:
            return []
        filter_ = self._scan_filter(scope, tier, after, method="ascan")
        with self._translated("scan"):
            results = await self._async_search_client().search(
                search_text=None,
                filter=filter_,
                order_by=["created_at asc", "id asc"],
                top=limit,
            )
            hits = [hit async for hit in results]
        return [self._record(hit, None) for hit in hits]

    def get_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="get_record")
        escaped_id = odata.escape_odata_literal(id)
        filter_ = f"{odata.scope_filter(scope, method='get_record')} and id eq '{escaped_id}'"
        with self._translated("get_record"):
            results = self._search_client().search(search_text=None, filter=filter_, top=1)
            hits = list(results)
        return self._record(hits[0], None) if hits else None

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        escaped_id = odata.escape_odata_literal(id)
        filter_ = f"{odata.scope_filter(scope, method='aget_record')} and id eq '{escaped_id}'"
        with self._translated("get_record"):
            results = await self._async_search_client().search(search_text=None, filter=filter_, top=1)
            hits = [hit async for hit in results]
        return self._record(hits[0], None) if hits else None
