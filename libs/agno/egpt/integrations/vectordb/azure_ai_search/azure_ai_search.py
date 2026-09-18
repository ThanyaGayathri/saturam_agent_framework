"""Azure AI Search :class:`~egpt.vectordb.base.VectorDb` backend (AZ-C, framework half).

The plain ``VectorDb`` for ``egpt.knowledge.Knowledge`` -- K2 prose -- on Azure AI Search, the
knowledge-side counterpart of the memory substrate in
``egpt.integrations.memory.vector.azure_ai_search`` (AZ-B). Registered as the
``"azure_ai_search"`` row in ``egpt.registry._declarations.vectordbs`` beside ``"pgvector"``, whose
behaviour this module mirrors method for method: the same record-id derivation
(``md5(f"{doc.id or md5(content)}_{content_hash}")``), the same ``meta_data``/``filters`` merge on
write, the same ``similarity_score`` stamped into ``meta_data`` on read, the same
``upsert``-by-content-hash shape (delete the hash's old rows, then merge-or-upload the new ones).

**The two decisions this backend embodies were made once, in AZ-B
(``cookbook/95_azure_migration/AZ_B_dimension_and_scoring_decisions.md``), and are inherited
here rather than re-derived -- the migration plan sequences AZ-C after AZ-B for exactly this
reason.**

1. *Dimension* (Decision 1). This backend declares its vector field at whatever width the
   embedder reports (``embedder.dimensions``, or the explicit ``vector_dimensions`` override) and
   never widens it. K1's pipeline resolves 768 for Mode A and the existing 1024 truncation for
   Mode B; K2 indexes at the same number so a pgvector-to-Azure cutover is a backend swap, not a
   re-embedding migration. Azure's own ceiling (3072, :data:`MAX_VECTOR_DIM`) is checked only so a
   misconfigured embedder fails at construction with a field name instead of as an opaque
   ``create_index`` error.

2. *Hybrid scoring* (Decision 2). :meth:`AzureAISearch.hybrid_search` issues two independent
   single-leg queries -- one vector-only, one BM25 text-only -- and blends them in Python as
   ``vector_score_weight * vector + (1 - vector_score_weight) * lexical``, the same formula and the
   same meaning of ``vector_score_weight`` as ``PgVector.hybrid_search``. It does **not** use Azure
   AI Search's native one-call hybrid/RRF query. Decision 2 ratified the two-leg shape and the
   per-leg normalisation for the memory substrate and noted the ``VectorDb`` port *could* use
   native RRF instead; the AZ-C contract chose to inherit the two-leg blend anyway so that the
   ``similarity_score`` this backend stamps keeps pgvector's ``w * vector + (1 - w) * lexical``
   shape and the product's ranking evidence can be read against one formula, not two. The
   per-leg normalisation is Decision 2's verbatim:

   - vector leg: Azure's cosine ``@search.score`` is ``1 / (1 + cosine_distance)``, already a
     similarity in ``(0.0, 1.0]`` and monotonic in the underlying measure, passed through with no
     further transform. Its *magnitude* therefore differs from pgvector's ``1 - cosine_distance``
     (for a distance of 0.2 pgvector reports 0.8, this backend 0.833). Ordering is identical;
     a caller reading the absolute number as calibrated against pgvector's is not, and
     ``similarity_threshold`` is applied to *this* backend's magnitude.
   - lexical leg: Azure's unbounded BM25 ``@search.score`` is squashed with the same saturating
     ``r / (r + 0.1)`` pgvector applies to ``ts_rank_cd`` (:data:`_RANK_SATURATION`), reused rather
     than re-calibrated.

   **What the two-leg blend cannot reproduce from pgvector, stated rather than implied.** pgvector
   computes the blend as one SQL expression over every row, so a document can rank on the sum of a
   modest vector score and a modest text rank even if it is in neither leg's own top-``limit``.
   Here each leg returns at most ``limit`` hits and a document absent from a leg scores 0 on that
   leg, so the blended list is drawn from the union of the two legs' top-``limit`` sets only.
   There is also no cross-leg snapshot isolation (Azure AI Search's REST API has no multi-request
   transaction), so a document indexed between the two calls may be visible to one leg and not the
   other -- the same gap AZ-B recorded for the memory substrate.

**Filters.** ``meta_data`` is stored as one JSON string, which Azure AI Search cannot filter
inside, so every top-level ``meta_data`` key is also flattened into a filterable
``Collection(Edm.String)`` field named ``filters`` as ``"key=value"`` tokens (a non-string value
is rendered with ``json.dumps``). A dict filter becomes a conjunction of
``filters/any(f: f eq 'key=value')`` clauses -- pgvector's JSONB containment for top-level scalar
keys. The ``FilterExpr`` DSL maps ``EQ``, ``NEQ``, ``IN``, ``AND``, ``OR`` and ``NOT`` onto the same
tokens. **Not expressible** on flattened tokens, and refused with a ``ValueError`` naming the
operator rather than silently ignored: ``GT``, ``GTE``, ``LT``, ``LTE`` (an ordering over
``key=value`` strings is not an ordering over the values), ``CONTAINS`` and ``STARTSWITH`` (OData's
string functions do not apply inside a ``Collection(Edm.String)`` ``any`` lambda). pgvector itself
supports only ``EQ``/``IN``/``GT``/``LT``/``NOT``/``AND``/``OR``, so the net difference from it is
``GT``/``LT`` lost and ``NEQ`` gained. Nested (non-scalar) metadata values are matched only as a
whole JSON-rendered token, not by sub-key. Every literal that reaches a ``$filter`` string goes
through :func:`~egpt.integrations.memory.vector.azure_ai_search.odata.escape_odata_literal`,
imported from AZ-B's module rather than re-implemented.

**Keys.** Azure AI Search document keys admit only letters, digits, ``_``, ``-`` and ``=``, so the
record id is URL-safe-base64-encoded into the ``doc_key`` key field (:func:`_document_key`) and
kept verbatim in the filterable ``id`` field, which every id-addressed read and delete matches on.

**Errors.** Construction refusals raise :class:`AzureAISearchConfigError` naming the argument and
the environment variable that could have supplied it -- the shape of
``egpt.providers.model_provider.ModelProviderConfigError``, a ``ValueError`` with ``field_name``.
Service failures are classified by HTTP status exactly as AZ-B does (:func:`_is_retryable`): a
throttle or transient 5xx propagates unchanged so a caller's retry policy can act on it; a
deterministic failure on a *search* is logged and returns ``[]``, matching ``PgVector``'s search
contract; a failure on a write re-raises, matching ``PgVector``'s write contract; a failure on a
``delete_by_*`` returns ``False``, matching ``PgVector``'s.

Sync and async are both native: the async half drives ``azure.search.documents.aio``'s own client
rather than wrapping the sync client in a thread, per the repository rule.
"""

import asyncio
import base64
import json
import os
import re
from copy import deepcopy
from hashlib import md5
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from egpt.filters import FilterExpr
from egpt.integrations.knowledge.reranker.base import Reranker
from egpt.integrations.memory.vector.azure_ai_search.odata import escape_odata_literal
from egpt.knowledge.document import Document
from egpt.knowledge.embedder import Embedder
from egpt.utils.log import log_debug, log_error, log_info, log_warning
from egpt.utils.string import generate_id
from egpt.vectordb.base import VectorDb
from egpt.vectordb.search import SearchType

try:
    from azure.core.credentials import AzureKeyCredential
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
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

__all__ = ["AzureAISearch", "AzureAISearchConfigError"]

#: Environment variables consulted, in order, when ``endpoint``/``api_key`` are not passed. The
#: knowledge-specific name first so K2 can point at a different service than K1; the memory
#: substrate's name second so one service configured once serves both.
ENDPOINT_ENV_VARS = ("EGPT_KNOWLEDGE_AZURE_SEARCH_ENDPOINT", "EGPT_MEMORY_AZURE_SEARCH_ENDPOINT")
API_KEY_ENV_VARS = ("EGPT_KNOWLEDGE_AZURE_SEARCH_API_KEY", "EGPT_MEMORY_AZURE_SEARCH_API_KEY")

#: Azure AI Search's own ceiling on an HNSW vector field's width -- the same number the memory
#: substrate checks (``MAX_VECTOR_DIM`` there). Not the width this backend writes at (Decision 1:
#: the embedder's own, 768/1024 for Mode A/B); only the ceiling a misconfigured one could exceed.
MAX_VECTOR_DIM = 3072

#: Azure AI Search's service defaults for HNSW, the same the memory substrate uses.
_DEFAULT_M = 4
_DEFAULT_EF_CONSTRUCTION = 400
_DEFAULT_EF_SEARCH = 500

#: The BM25 saturation constant, ``r / (r + k)``, reused verbatim from ``PgVector.hybrid_search``
#: (``raw_text_rank / (raw_text_rank + 0.1)``) and from the memory substrate -- see Decision 2.
_RANK_SATURATION = 0.1

#: Field names, in one place so the index definition, the write path and the read path agree.
_KEY_FIELD = "doc_key"
_ID_FIELD = "id"
_CONTENT_FIELD = "content"
_VECTOR_FIELD = "embedding"
_FILTERS_FIELD = "filters"
_META_FIELD = "meta_data"
_USAGE_FIELD = "usage"
_NAME_FIELD = "name"
_CONTENT_HASH_FIELD = "content_hash"
_CONTENT_ID_FIELD = "content_id"

_PROFILE_NAME = "egpt-knowledge-vector-profile"
_ALGORITHM_NAME = "egpt-knowledge-hnsw"

#: Documents per indexing batch. Azure AI Search accepts up to 1000 per request; 100 matches
#: ``PgVector``'s default ``batch_size`` so an ingest behaves the same size-wise on both.
_BATCH_SIZE = 100

#: HTTP statuses a retry can fix -- same set and same reasoning as the memory substrate: azure-core
#: raises one ``HttpResponseError`` class for every status, so the split has to be made on the code.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

#: Filter DSL operators this backend can render onto ``key=value`` tokens, and the ones it cannot.
_SUPPORTED_OPS = frozenset({"EQ", "NEQ", "IN", "AND", "OR", "NOT"})
_UNSUPPORTED_OPS = frozenset({"GT", "GTE", "LT", "LTE", "CONTAINS", "STARTSWITH"})


class AzureAISearchConfigError(ValueError):
    """The backend's configuration is invalid or incomplete.

    Same shape as ``egpt.providers.model_provider.ModelProviderConfigError`` (a ``ValueError``
    carrying ``field_name``), so a caller's ``except ValueError`` keeps holding and the argument
    that needs fixing is machine-readable. Not that class itself: a vector store is not a model
    provider, and its docstring says why it must not be caught as one.
    """

    def __init__(self, message: str, *, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


def _is_retryable(exc: "HttpResponseError") -> bool:
    """Whether a retry could fix this failure -- see :data:`_RETRYABLE_STATUS`. An absent status
    (a transport error that never saw a response) counts as retryable, for the reason the memory
    substrate gives: a wasted round trip is cheaper than a caller giving up on a network blip."""
    status = getattr(exc, "status_code", None)
    return status is None or status in _RETRYABLE_STATUS


def _document_key(record_id: str) -> str:
    """The Azure document key for a record id: URL-safe base64 of its UTF-8 bytes.

    The base64url alphabet plus ``=`` padding is a subset of Azure AI Search's allowed key charset,
    the encoding is deterministic (a re-upsert of the same id lands on the same document, matching
    pgvector's ``ON CONFLICT (id)``) and injective (no two ids share a key). The record id is also
    stored verbatim in the ``id`` field, which every id-addressed read matches on.
    """
    return base64.urlsafe_b64encode(record_id.encode("utf-8")).decode("ascii")


def _filter_token(key: str, value: Any) -> str:
    """One ``key=value`` entry of the flattened ``filters`` collection. A string value is used as
    is; anything else is rendered with ``json.dumps`` (``sort_keys`` so a nested dict renders the
    same way on write and on match). The same function renders both the stored tokens and the
    query-side literals, which is what keeps the two in agreement."""
    if isinstance(value, str):
        return f"{key}={value}"
    return f"{key}={json.dumps(value, sort_keys=True)}"


def _token_clause(key: str, value: Any) -> str:
    return f"{_FILTERS_FIELD}/any(f: f eq '{escape_odata_literal(_filter_token(key, value))}')"


def _dict_filter(filters: Dict[str, Any]) -> Optional[str]:
    """pgvector's ``meta_data @> filters`` for top-level keys: every key must match."""
    clauses = [_token_clause(key, value) for key, value in filters.items()]
    return " and ".join(clauses) if clauses else None


def _dsl_filter(expr: Any) -> str:
    """Render one ``FilterExpr`` (or its ``to_dict`` form) as an OData ``$filter`` fragment."""
    node = expr.to_dict() if hasattr(expr, "to_dict") else expr
    op = node["op"]
    if op == "EQ":
        return _token_clause(node["key"], node["value"])
    if op == "NEQ":
        return f"not {_token_clause(node['key'], node['value'])}"
    if op == "IN":
        values = list(node["values"])
        if not values:
            # `x IN ()` is false for every row in SQL; OData has no empty disjunction, so say so.
            return "false"
        return "(" + " or ".join(_token_clause(node["key"], value) for value in values) + ")"
    if op == "NOT":
        return f"not ({_dsl_filter(node['condition'])})"
    if op in ("AND", "OR"):
        joiner = " and " if op == "AND" else " or "
        return "(" + joiner.join(_dsl_filter(child) for child in node["conditions"]) + ")"
    if op in _UNSUPPORTED_OPS:
        raise ValueError(
            f"Filter operator {op} is not expressible on Azure AI Search's flattened 'key=value' "
            f"metadata tokens (supported: {', '.join(sorted(_SUPPORTED_OPS))}). Ordering and substring "
            "predicates over metadata values need a typed field per key, which this backend does not "
            "declare -- see the module docstring."
        )
    raise ValueError(f"Unknown filter operator: {op}")


def render_filter(filters: Optional[Union[Dict[str, Any], List[FilterExpr]]]) -> Optional[str]:
    """The ``$filter`` string for a ``search()`` call's ``filters`` argument, or ``None`` for none.

    A dict is pgvector's containment (all keys equal); a list of ``FilterExpr`` is ANDed, as
    ``PgVector`` ANDs it. Exposed at module level so the rendering is testable without a client.
    """
    if filters is None:
        return None
    if isinstance(filters, dict):
        return _dict_filter(filters)
    clauses = [_dsl_filter(expr) for expr in filters]
    return " and ".join(clauses) if clauses else None


def _lexical_similarity(rank: float) -> float:
    """Azure's unbounded BM25 ``@search.score`` squashed into ``[0.0, 1.0)`` -- Decision 2's
    lexical-leg normalisation, ``r / (r + k)``, strictly increasing so it never reorders a leg."""
    if rank < 0:
        # Azure documents BM25 scores as non-negative; a negative one is a contract change, and
        # the division would return a negative "similarity" silently rather than fail.
        raise ValueError(f"Azure AI Search returned a negative BM25 @search.score ({rank}).")
    return rank / (rank + _RANK_SATURATION)


def _as_json(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value)
    except TypeError as exc:
        raise ValueError(f"{field} is not JSON-serializable ({exc}).") from exc


def _from_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    return json.loads(value)


class AzureAISearch(VectorDb):
    """Knowledge vectors in one Azure AI Search index -- see the module docstring."""

    def __init__(
        self,
        index_name: str,
        *,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        credential: Optional[Any] = None,
        embedder: Optional[Embedder] = None,
        search_type: SearchType = SearchType.vector,
        vector_dimensions: Optional[int] = None,
        vector_score_weight: float = 0.5,
        name: Optional[str] = None,
        description: Optional[str] = None,
        id: Optional[str] = None,
        reranker: Optional[Reranker] = None,
        similarity_threshold: Optional[float] = None,
        m: int = _DEFAULT_M,
        ef_construction: int = _DEFAULT_EF_CONSTRUCTION,
        ef_search: int = _DEFAULT_EF_SEARCH,
    ):
        """
        Args:
            index_name: Azure AI Search index to store the documents in (lowercase letters, digits
                and dashes, 2-128 characters). Created by :meth:`create` if absent.
            endpoint: Service endpoint, e.g. ``https://<service>.search.windows.net``. Falls back
                to ``EGPT_KNOWLEDGE_AZURE_SEARCH_ENDPOINT``, then ``EGPT_MEMORY_AZURE_SEARCH_ENDPOINT``.
            api_key: Admin/query key. Falls back to ``EGPT_KNOWLEDGE_AZURE_SEARCH_API_KEY``, then
                ``EGPT_MEMORY_AZURE_SEARCH_API_KEY``. Ignored when ``credential`` is given.
            credential: An ``AzureKeyCredential`` or ``TokenCredential`` (e.g. a
                ``DefaultAzureCredential`` for managed identity). Wins over ``api_key``.
            embedder: Embedder for document and query text. Defaults to ``OpenAIEmbedder()``,
                as ``PgVector`` does.
            search_type: ``vector`` (default), ``keyword`` (BM25) or ``hybrid`` (see module docstring).
            vector_dimensions: Width of the index's vector field. Defaults to ``embedder.dimensions``;
                baked into the index at :meth:`create` and checked against an existing index.
            vector_score_weight: Weight of the vector leg in hybrid search, in ``[0, 1]``; the
                lexical leg gets ``1 - vector_score_weight``. Same meaning as ``PgVector``'s.
            name, description, id: The ``VectorDb`` identity fields. ``id`` defaults to a hash of
                ``endpoint#index_name`` so two instances on the same index share one id.
            reranker: Applied to vector and hybrid results, as ``PgVector`` applies it.
            similarity_threshold: Minimum ``similarity_score`` (this backend's magnitude -- see
                the module docstring) a vector or hybrid result must reach.
            m, ef_construction, ef_search: HNSW parameters, within Azure's own bounds.
        """
        self.index_name = self._checked_index_name(index_name)
        self.endpoint = self._resolved_endpoint(endpoint)
        # `Any` for the reason the memory substrate gives: the sync and async SDK clients type
        # their `credential` keyword as two different unions for what is the same object.
        self.credential: Any = self._resolved_credential(credential, api_key)

        if id is None:
            id = generate_id(f"{self.endpoint}#{self.index_name}")
        super().__init__(id=id, name=name, description=description, similarity_threshold=similarity_threshold)

        if embedder is None:
            from egpt.integrations.knowledge.embedder.openai import OpenAIEmbedder

            embedder = OpenAIEmbedder()
            log_debug("Embedder not provided, using OpenAIEmbedder as default.")
        self.embedder: Embedder = embedder

        resolved_dimensions = vector_dimensions if vector_dimensions is not None else self.embedder.dimensions
        if resolved_dimensions is None:
            raise AzureAISearchConfigError(
                "The embedder reports no dimensions and vector_dimensions was not given; the index's "
                "vector field width must be known at construction.",
                field_name="vector_dimensions",
            )
        if not isinstance(resolved_dimensions, int) or isinstance(resolved_dimensions, bool):
            raise AzureAISearchConfigError(
                f"vector_dimensions must be an integer, got {resolved_dimensions!r}.", field_name="vector_dimensions"
            )
        if not 0 < resolved_dimensions <= MAX_VECTOR_DIM:
            raise AzureAISearchConfigError(
                f"vector_dimensions={resolved_dimensions} is outside 1..{MAX_VECTOR_DIM}, Azure AI Search's "
                "own ceiling for a vector field. Fix the embedder or the vector_dimensions override.",
                field_name="vector_dimensions",
            )
        self.dimensions: int = resolved_dimensions

        if not 0 <= vector_score_weight <= 1:
            raise AzureAISearchConfigError(
                f"vector_score_weight must be between 0 and 1, got {vector_score_weight!r}.",
                field_name="vector_score_weight",
            )
        self.vector_score_weight: float = vector_score_weight
        self.search_type: SearchType = search_type
        self.reranker: Optional[Reranker] = reranker
        self.m, self.ef_construction, self.ef_search = self._checked_hnsw_params(m, ef_construction, ef_search)

        self._client: Optional[SearchClient] = None
        self._aclient: Optional[AsyncSearchClient] = None
        self._index_client: Optional[SearchIndexClient] = None
        self._aindex_client: Optional[AsyncSearchIndexClient] = None
        log_debug(f"Initialized AzureAISearch with index '{self.index_name}'")

    # -- construction-time validation -----------------------------------------

    @staticmethod
    def _resolved_endpoint(endpoint: Optional[str]) -> str:
        resolved = endpoint or next((os.getenv(var) for var in ENDPOINT_ENV_VARS if os.getenv(var)), None)
        if not resolved:
            raise AzureAISearchConfigError(
                "AzureAISearch needs a service endpoint: pass endpoint=, or set "
                f"{ENDPOINT_ENV_VARS[0]} (or {ENDPOINT_ENV_VARS[1]}).",
                field_name="endpoint",
            )
        return resolved

    @staticmethod
    def _resolved_credential(credential: Optional[Any], api_key: Optional[str]) -> Any:
        """A caller-supplied credential wins; else an API key from the argument or the environment.

        As in the memory substrate, this module depends only on ``azure-search-documents``, so it
        does not build a ``DefaultAzureCredential`` itself; a managed-identity deployment passes
        one through ``credential=``.
        """
        if credential is not None:
            return credential
        resolved_key = api_key or next((os.getenv(var) for var in API_KEY_ENV_VARS if os.getenv(var)), None)
        if not resolved_key:
            raise AzureAISearchConfigError(
                "AzureAISearch needs a credential: pass credential= (an AzureKeyCredential or "
                f"TokenCredential), or api_key=, or set {API_KEY_ENV_VARS[0]} (or {API_KEY_ENV_VARS[1]}).",
                field_name="credential",
            )
        return AzureKeyCredential(resolved_key)

    @staticmethod
    def _checked_index_name(index_name: str) -> str:
        """Azure AI Search index names: lowercase letters, digits, dashes; 2-128 characters;
        starting and ending with a letter or digit."""
        if not isinstance(index_name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,126}[a-z0-9]", index_name):
            raise AzureAISearchConfigError(
                f"index_name={index_name!r} is not a legal Azure AI Search index name: lowercase letters, "
                "digits and dashes, 2-128 characters, starting and ending with a letter or digit.",
                field_name="index_name",
            )
        return index_name

    @staticmethod
    def _checked_hnsw_params(m: int, ef_construction: int, ef_search: int) -> Tuple[int, int, int]:
        checks = (("m", m, 4, 10), ("ef_construction", ef_construction, 100, 1000), ("ef_search", ef_search, 100, 1000))
        for field_name, value, low, high in checks:
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise AzureAISearchConfigError(
                    f"{field_name} must be an integer in {low}..{high}, got {value!r}.", field_name=field_name
                )
        return m, ef_construction, ef_search

    # -- plumbing -------------------------------------------------------------

    def _search_client(self) -> SearchClient:
        if self._client is None:
            self._client = SearchClient(endpoint=self.endpoint, index_name=self.index_name, credential=self.credential)
        return self._client

    def _async_search_client(self) -> AsyncSearchClient:
        """Built on first use so the client binds to the loop that will actually drive it."""
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

    def close(self) -> None:
        """Close the sync clients. Refuses when an async client is open, for the reason the
        memory substrate gives: its transport belongs to the loop that opened it."""
        if self._aclient is not None or self._aindex_client is not None:
            raise RuntimeError(
                "This backend has an open async client; call aclose() from the same async context instead."
            )
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._index_client is not None:
            self._index_client.close()
            self._index_client = None

    async def aclose(self) -> None:
        """Close both halves."""
        if self._aclient is not None:
            await self._aclient.close()
            self._aclient = None
        if self._aindex_client is not None:
            await self._aindex_client.close()
            self._aindex_client = None
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._index_client is not None:
            self._index_client.close()
            self._index_client = None

    def __deepcopy__(self, memo: Dict[int, Any]) -> "AzureAISearch":
        """Share the SDK clients, the credential and the embedder; deep-copy the rest -- the same
        split ``PgVector.__deepcopy__`` makes for its engine and session."""
        cls = self.__class__
        copied = cls.__new__(cls)
        memo[id(self)] = copied
        shared = {"_client", "_aclient", "_index_client", "_aindex_client", "credential", "embedder", "reranker"}
        for key, value in self.__dict__.items():
            setattr(copied, key, value if key in shared else deepcopy(value, memo))
        return copied

    # -- schema -----------------------------------------------------------------

    def _index_definition(self) -> "SearchIndex":
        fields: List[SearchField] = [
            SimpleField(name=_KEY_FIELD, type=SearchFieldDataType.String, key=True),
            SimpleField(name=_ID_FIELD, type=SearchFieldDataType.String, filterable=True),
            SimpleField(name=_NAME_FIELD, type=SearchFieldDataType.String, filterable=True),
            SimpleField(name=_CONTENT_HASH_FIELD, type=SearchFieldDataType.String, filterable=True),
            SimpleField(name=_CONTENT_ID_FIELD, type=SearchFieldDataType.String, filterable=True),
            SearchableField(name=_CONTENT_FIELD, type=SearchFieldDataType.String, filterable=False),
            SimpleField(name=_META_FIELD, type=SearchFieldDataType.String, filterable=False),
            SimpleField(name=_USAGE_FIELD, type=SearchFieldDataType.String, filterable=False),
            SimpleField(
                name=_FILTERS_FIELD,
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                filterable=True,
            ),
            SearchField(
                name=_VECTOR_FIELD,
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self.dimensions,
                vector_search_profile_name=_PROFILE_NAME,
            ),
        ]
        vector_search = VectorSearch(
            profiles=[VectorSearchProfile(name=_PROFILE_NAME, algorithm_configuration_name=_ALGORITHM_NAME)],
            algorithms=[
                HnswAlgorithmConfiguration(
                    name=_ALGORITHM_NAME,
                    parameters=HnswParameters(
                        m=self.m, ef_construction=self.ef_construction, ef_search=self.ef_search, metric="cosine"
                    ),
                )
            ],
        )
        return SearchIndex(name=self.index_name, fields=fields, vector_search=vector_search)

    def _check_existing_width(self, existing: Any) -> None:
        """Refuse an existing index whose vector field is a different width -- pgvector's
        dimension-agreement check, naming both numbers so the operator knows which side to fix."""
        existing_width: Optional[int] = None
        for field in getattr(existing, "fields", None) or ():
            if getattr(field, "name", None) == _VECTOR_FIELD:
                existing_width = getattr(field, "vector_search_dimensions", None)
                break
        if existing_width is None:
            raise AzureAISearchConfigError(
                f"Index {self.index_name!r} exists but has no '{_VECTOR_FIELD}' vector field; it was not "
                "created by this backend and cannot be reused. Point at another index_name or drop it.",
                field_name="index_name",
            )
        if existing_width != self.dimensions:
            raise AzureAISearchConfigError(
                f"Index {self.index_name!r} already exists with a {existing_width}-dimension vector field, "
                f"but this backend's embedder produces {self.dimensions}-dimension vectors. Azure AI Search "
                "cannot change a vector field's width in place: use an embedder (or vector_dimensions) that "
                f"matches {existing_width}, or create a new index and re-embed.",
                field_name="vector_dimensions",
            )

    def create(self) -> None:
        """Create the index if it does not exist; if it does, check its vector width agrees."""
        client = self._index_search_client()
        try:
            existing = client.get_index(self.index_name)
        except ResourceNotFoundError:
            log_debug(f"Creating index: {self.index_name}")
            client.create_index(self._index_definition())
            return
        self._check_existing_width(existing)

    async def async_create(self) -> None:
        client = self._async_index_search_client()
        try:
            existing = await client.get_index(self.index_name)
        except ResourceNotFoundError:
            log_debug(f"Creating index: {self.index_name}")
            await client.create_index(self._index_definition())
            return
        self._check_existing_width(existing)

    def exists(self) -> bool:
        try:
            self._index_search_client().get_index(self.index_name)
            return True
        except ResourceNotFoundError:
            return False

    async def async_exists(self) -> bool:
        try:
            await self._async_index_search_client().get_index(self.index_name)
            return True
        except ResourceNotFoundError:
            return False

    def drop(self) -> None:
        try:
            self._index_search_client().delete_index(self.index_name)
            log_info(f"Index '{self.index_name}' dropped.")
        except ResourceNotFoundError:
            log_info(f"Index '{self.index_name}' does not exist.")

    async def async_drop(self) -> None:
        try:
            await self._async_index_search_client().delete_index(self.index_name)
            log_info(f"Index '{self.index_name}' dropped.")
        except ResourceNotFoundError:
            log_info(f"Index '{self.index_name}' does not exist.")

    def optimize(self) -> None:
        """Azure AI Search maintains its own HNSW graph; there is no index to (re)build here."""
        log_debug("AzureAISearch.optimize() is a no-op: the service manages its HNSW index.")

    # -- existence checks -------------------------------------------------------

    def _any_match(self, filter_: str) -> bool:
        try:
            hits = self._search_client().search(search_text=None, filter=filter_, top=1, select=[_KEY_FIELD])
            return next(iter(hits), None) is not None
        except HttpResponseError as exc:
            if _is_retryable(exc):
                raise
            log_error(f"Error checking if record exists: {exc}")
            return False

    async def _async_any_match(self, filter_: str) -> bool:
        try:
            hits = await self._async_search_client().search(
                search_text=None, filter=filter_, top=1, select=[_KEY_FIELD]
            )
            async for _ in hits:
                return True
            return False
        except HttpResponseError as exc:
            if _is_retryable(exc):
                raise
            log_error(f"Error checking if record exists: {exc}")
            return False

    @staticmethod
    def _eq(field: str, value: str) -> str:
        return f"{field} eq '{escape_odata_literal(value)}'"

    def name_exists(self, name: str) -> bool:
        return self._any_match(self._eq(_NAME_FIELD, name))

    async def async_name_exists(self, name: str) -> bool:
        return await self._async_any_match(self._eq(_NAME_FIELD, name))

    def id_exists(self, id: str) -> bool:
        return self._any_match(self._eq(_ID_FIELD, id))

    def content_hash_exists(self, content_hash: str) -> bool:
        return self._any_match(self._eq(_CONTENT_HASH_FIELD, content_hash))

    # -- writes -------------------------------------------------------------------

    @staticmethod
    def _clean_content(content: str) -> str:
        return content.replace("\x00", "�")

    def _record(self, doc: Document, filters: Optional[Dict[str, Any]], content_hash: str) -> Dict[str, Any]:
        """The Azure document for ``doc`` -- ``PgVector._get_document_record`` minus the embedding
        call, which the sync and async paths do differently."""
        if doc.embedding is None:
            raise ValueError(f"Document '{doc.name}' has no embedding.")
        if len(doc.embedding) != self.dimensions:
            raise ValueError(
                f"Document '{doc.name}' embedding has {len(doc.embedding)} dimensions; the index's vector "
                f"field is {self.dimensions}-wide."
            )
        cleaned_content = self._clean_content(doc.content)
        # Include content_hash in the id so the same content can land under different hashes --
        # the same derivation PgVector uses, so ids agree across the two backends.
        base_id = doc.id or md5(cleaned_content.encode()).hexdigest()
        record_id = md5(f"{base_id}_{content_hash}".encode()).hexdigest()

        meta_data = dict(doc.meta_data or {})
        if filters:
            meta_data.update(filters)

        return {
            _KEY_FIELD: _document_key(record_id),
            _ID_FIELD: record_id,
            _NAME_FIELD: doc.name,
            _CONTENT_HASH_FIELD: content_hash,
            _CONTENT_ID_FIELD: doc.content_id,
            _CONTENT_FIELD: cleaned_content,
            _META_FIELD: _as_json(meta_data, _META_FIELD),
            _USAGE_FIELD: _as_json(doc.usage, _USAGE_FIELD),
            _FILTERS_FIELD: [_filter_token(key, value) for key, value in meta_data.items()],
            _VECTOR_FIELD: list(doc.embedding),
        }

    def _records(self, docs: Sequence[Document], filters: Optional[Dict[str, Any]], content_hash: str) -> List[Dict]:
        records: Dict[str, Dict[str, Any]] = {}
        for doc in docs:
            try:
                record = self._record(doc, filters, content_hash)
            except Exception as exc:
                log_error(f"Error processing document '{doc.name}': {exc}")
                continue
            # Dedup by key within a batch: Azure rejects a batch naming one key twice.
            records[record[_KEY_FIELD]] = record
        return list(records.values())

    @staticmethod
    def _reject_failed_documents(results: Any, documents: List[Dict[str, Any]], *, operation: str) -> None:
        """Azure's batch indexing API reports a per-document rejection in its return value, not by
        raising; a discarded return value would report a dropped document as a landed write."""
        failed = [result for result in (results or ()) if not getattr(result, "succeeded", True)]
        if not failed:
            return
        record_ids = {document[_KEY_FIELD]: document[_ID_FIELD] for document in documents}
        detail = "; ".join(
            f"{record_ids.get(getattr(result, 'key', None), getattr(result, 'key', None))!r}: "
            f"{getattr(result, 'error_message', None) or getattr(result, 'status_code', None)}"
            for result in failed
        )
        raise RuntimeError(
            f"Azure AI Search accepted the {operation} request but rejected {len(failed)} of "
            f"{len(documents)} document(s): {detail}. The rejected document(s) are not in the index."
        )

    async def _async_embed_documents(self, batch_docs: List[Document]) -> None:
        """Embed a batch, using the embedder's batch API when it offers one -- ``PgVector``'s
        ``_async_embed_documents`` without its rate-limit string sniffing: a failure here is
        re-raised as itself, so a throttle reaches the caller as the SDK's own error."""
        if self.embedder.enable_batch and hasattr(self.embedder, "async_get_embeddings_batch_and_usage"):
            embeddings, usages = await self.embedder.async_get_embeddings_batch_and_usage(
                [doc.content for doc in batch_docs]
            )
            for j, doc in enumerate(batch_docs):
                if j < len(embeddings):
                    doc.embedding = embeddings[j]
                    doc.usage = usages[j] if j < len(usages) else None
            return
        await asyncio.gather(*(doc.async_embed(embedder=self.embedder) for doc in batch_docs))

    def _embed_documents(self, batch_docs: List[Document]) -> None:
        for doc in batch_docs:
            doc.embed(embedder=self.embedder)

    def _write(self, documents: List[Document], content_hash: str, filters: Optional[Dict], *, merge: bool) -> None:
        operation = "upsert" if merge else "insert"
        try:
            for start in range(0, len(documents), _BATCH_SIZE):
                batch_docs = documents[start : start + _BATCH_SIZE]
                self._embed_documents(batch_docs)
                records = self._records(batch_docs, filters, content_hash)
                if not records:
                    continue
                client = self._search_client()
                results = (
                    client.merge_or_upload_documents(documents=records)
                    if merge
                    else client.upload_documents(documents=records)
                )
                self._reject_failed_documents(results, records, operation=operation)
                log_info(f"{operation.capitalize()}ed batch of {len(records)} documents.")
        except Exception as exc:
            log_error(f"Error {operation}ing documents: {exc}")
            raise

    async def _async_write(
        self, documents: List[Document], content_hash: str, filters: Optional[Dict], *, merge: bool
    ) -> None:
        operation = "upsert" if merge else "insert"
        try:
            for start in range(0, len(documents), _BATCH_SIZE):
                batch_docs = documents[start : start + _BATCH_SIZE]
                await self._async_embed_documents(batch_docs)
                records = self._records(batch_docs, filters, content_hash)
                if not records:
                    continue
                client = self._async_search_client()
                results = (
                    await client.merge_or_upload_documents(documents=records)
                    if merge
                    else await client.upload_documents(documents=records)
                )
                self._reject_failed_documents(results, records, operation=operation)
                log_info(f"{operation.capitalize()}ed batch of {len(records)} documents.")
        except Exception as exc:
            log_error(f"Error {operation}ing documents: {exc}")
            raise

    def insert(self, content_hash: str, documents: List[Document], filters: Optional[Dict[str, Any]] = None) -> None:
        self._write(documents, content_hash, filters, merge=False)

    async def async_insert(
        self, content_hash: str, documents: List[Document], filters: Optional[Dict[str, Any]] = None
    ) -> None:
        await self._async_write(documents, content_hash, filters, merge=False)

    def upsert_available(self) -> bool:
        return True

    def upsert(self, content_hash: str, documents: List[Document], filters: Optional[Dict[str, Any]] = None) -> None:
        """Delete every document under ``content_hash``, then merge-or-upload the new ones --
        ``PgVector.upsert``'s shape."""
        if self.content_hash_exists(content_hash):
            self._delete_where(self._eq(_CONTENT_HASH_FIELD, content_hash), what=f"content hash '{content_hash}'")
        self._write(documents, content_hash, filters, merge=True)

    async def async_upsert(
        self, content_hash: str, documents: List[Document], filters: Optional[Dict[str, Any]] = None
    ) -> None:
        if await self._async_any_match(self._eq(_CONTENT_HASH_FIELD, content_hash)):
            await self._async_delete_where(
                self._eq(_CONTENT_HASH_FIELD, content_hash), what=f"content hash '{content_hash}'"
            )
        await self._async_write(documents, content_hash, filters, merge=True)

    def update_metadata(self, content_id: str, metadata: Dict[str, Any]) -> None:
        """Merge ``metadata`` into every document under ``content_id`` (``PgVector`` merges the
        JSONB the same way) and re-flatten the filter tokens to match."""
        try:
            client = self._search_client()
            hits = client.search(
                search_text=None, filter=self._eq(_CONTENT_ID_FIELD, content_id), select=[_KEY_FIELD, _META_FIELD]
            )
            updates = []
            for hit in hits:
                merged = dict(_from_json(hit.get(_META_FIELD)) or {})
                merged.update(metadata)
                updates.append(
                    {
                        _KEY_FIELD: hit[_KEY_FIELD],
                        _META_FIELD: _as_json(merged, _META_FIELD),
                        _FILTERS_FIELD: [_filter_token(key, value) for key, value in merged.items()],
                    }
                )
            if updates:
                results = client.merge_documents(documents=updates)
                self._reject_failed_documents(results, updates, operation="update_metadata")
        except Exception as exc:
            log_error(f"Error updating metadata for document {content_id}: {exc}")
            raise

    # -- reads ----------------------------------------------------------------------

    def _document(self, hit: Dict[str, Any], similarity_score: Optional[float]) -> Document:
        meta_data = dict(_from_json(hit.get(_META_FIELD)) or {})
        if similarity_score is not None:
            meta_data["similarity_score"] = similarity_score
        return Document(
            id=hit.get(_ID_FIELD),
            name=hit.get(_NAME_FIELD),
            meta_data=meta_data,
            content=hit.get(_CONTENT_FIELD) or "",
            embedder=self.embedder,
            embedding=hit.get(_VECTOR_FIELD),
            usage=_from_json(hit.get(_USAGE_FIELD)),
            content_id=hit.get(_CONTENT_ID_FIELD),
        )

    @staticmethod
    def _vector_query(embedding: Sequence[float], limit: int) -> "VectorizedQuery":
        return VectorizedQuery(vector=list(embedding), k_nearest_neighbors=limit, fields=_VECTOR_FIELD)

    def _vector_documents(self, hits: Iterable[Dict[str, Any]]) -> List[Document]:
        """Decision 2's vector leg: Azure's cosine ``@search.score`` is already a similarity in
        ``(0, 1]``, passed through unchanged."""
        documents = []
        for hit in hits:
            score = float(hit["@search.score"])
            if self.similarity_threshold is not None and score < self.similarity_threshold:
                continue
            documents.append(self._document(hit, score))
        return documents

    def _keyword_documents(self, hits: Iterable[Dict[str, Any]]) -> List[Document]:
        # No similarity_score and no reranker on the keyword path, exactly as PgVector.keyword_search.
        return [self._document(hit, None) for hit in hits]

    def _blend(
        self, vector_hits: List[Dict[str, Any]], lexical_hits: List[Dict[str, Any]], limit: int
    ) -> List[Document]:
        """Decision 2's blend: ``w * vector + (1 - w) * lexical`` over the union of both legs, a
        document absent from a leg scoring 0 on it. See the module docstring for what this cannot
        reproduce from pgvector's whole-table expression."""
        text_weight = 1.0 - self.vector_score_weight
        hits_by_key: Dict[str, Dict[str, Any]] = {}
        vector_scores: Dict[str, float] = {}
        lexical_scores: Dict[str, float] = {}
        for hit in vector_hits:
            hits_by_key.setdefault(hit[_KEY_FIELD], hit)
            vector_scores[hit[_KEY_FIELD]] = float(hit["@search.score"])
        for hit in lexical_hits:
            hits_by_key.setdefault(hit[_KEY_FIELD], hit)
            lexical_scores[hit[_KEY_FIELD]] = _lexical_similarity(float(hit["@search.score"]))

        scored = []
        for key, hit in hits_by_key.items():
            hybrid = self.vector_score_weight * vector_scores.get(key, 0.0) + text_weight * lexical_scores.get(key, 0.0)
            if self.similarity_threshold is not None and hybrid < self.similarity_threshold:
                continue
            scored.append((hybrid, hit))
        # Stable sort on the score alone, so equal scores keep first-seen (vector leg first) order.
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [self._document(hit, score) for score, hit in scored[:limit]]

    def _rerank(self, query: str, documents: List[Document]) -> List[Document]:
        if self.reranker and documents:
            return self.reranker.rerank(query=query, documents=documents)
        return documents

    def search(
        self, query: str, limit: int = 5, filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    ) -> List[Document]:
        if self.search_type == SearchType.vector:
            return self.vector_search(query=query, limit=limit, filters=filters)
        if self.search_type == SearchType.keyword:
            return self.keyword_search(query=query, limit=limit, filters=filters)
        if self.search_type == SearchType.hybrid:
            return self.hybrid_search(query=query, limit=limit, filters=filters)
        log_error(f"Invalid search type '{self.search_type}'.")
        return []

    async def async_search(
        self, query: str, limit: int = 5, filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    ) -> List[Document]:
        if self.search_type == SearchType.vector:
            return await self.async_vector_search(query=query, limit=limit, filters=filters)
        if self.search_type == SearchType.keyword:
            return await self.async_keyword_search(query=query, limit=limit, filters=filters)
        if self.search_type == SearchType.hybrid:
            return await self.async_hybrid_search(query=query, limit=limit, filters=filters)
        log_error(f"Invalid search type '{self.search_type}'.")
        return []

    @staticmethod
    def _search_failed(kind: str, exc: Exception) -> List[Document]:
        """``PgVector``'s search contract for a deterministic failure -- log and return nothing --
        applied only to a failure a retry cannot fix; a retryable one is re-raised so the caller's
        retry policy sees it instead of an empty answer."""
        if isinstance(exc, HttpResponseError) and _is_retryable(exc):
            raise exc
        log_error(f"Error during {kind} search: {exc}")
        return []

    def vector_search(
        self, query: str, limit: int = 5, filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    ) -> List[Document]:
        try:
            filter_ = render_filter(filters)
            embedding = self.embedder.get_embedding(query)
            hits = self._search_client().search(
                search_text=None, vector_queries=[self._vector_query(embedding, limit)], filter=filter_, top=limit
            )
            documents = self._rerank(query, self._vector_documents(list(hits)))
            log_info(f"Found {len(documents)} documents")
            return documents
        except Exception as exc:
            return self._search_failed("vector", exc)

    async def async_vector_search(
        self, query: str, limit: int = 5, filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    ) -> List[Document]:
        try:
            filter_ = render_filter(filters)
            embedding = await self.embedder.async_get_embedding(query)
            results = await self._async_search_client().search(
                search_text=None, vector_queries=[self._vector_query(embedding, limit)], filter=filter_, top=limit
            )
            hits = [hit async for hit in results]
            documents = self._rerank(query, self._vector_documents(hits))
            log_info(f"Found {len(documents)} documents")
            return documents
        except Exception as exc:
            return self._search_failed("vector", exc)

    def keyword_search(
        self, query: str, limit: int = 5, filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    ) -> List[Document]:
        try:
            filter_ = render_filter(filters)
            hits = self._search_client().search(
                search_text=query, search_fields=[_CONTENT_FIELD], filter=filter_, top=limit
            )
            documents = self._keyword_documents(list(hits))
            log_info(f"Found {len(documents)} documents")
            return documents
        except Exception as exc:
            return self._search_failed("keyword", exc)

    async def async_keyword_search(
        self, query: str, limit: int = 5, filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    ) -> List[Document]:
        try:
            filter_ = render_filter(filters)
            results = await self._async_search_client().search(
                search_text=query, search_fields=[_CONTENT_FIELD], filter=filter_, top=limit
            )
            hits = [hit async for hit in results]
            documents = self._keyword_documents(hits)
            log_info(f"Found {len(documents)} documents")
            return documents
        except Exception as exc:
            return self._search_failed("keyword", exc)

    def hybrid_search(
        self, query: str, limit: int = 5, filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    ) -> List[Document]:
        """Two single-leg queries blended in Python -- Decision 2 inherited; see the module docstring."""
        try:
            filter_ = render_filter(filters)
            embedding = self.embedder.get_embedding(query)
            client = self._search_client()
            vector_hits = list(
                client.search(
                    search_text=None, vector_queries=[self._vector_query(embedding, limit)], filter=filter_, top=limit
                )
            )
            lexical_hits = list(
                client.search(search_text=query, search_fields=[_CONTENT_FIELD], filter=filter_, top=limit)
            )
            documents = self._rerank(query, self._blend(vector_hits, lexical_hits, limit))
            log_info(f"Found {len(documents)} documents")
            return documents
        except Exception as exc:
            return self._search_failed("hybrid", exc)

    async def async_hybrid_search(
        self, query: str, limit: int = 5, filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    ) -> List[Document]:
        try:
            filter_ = render_filter(filters)
            embedding = await self.embedder.async_get_embedding(query)
            client = self._async_search_client()
            vector_results = await client.search(
                search_text=None, vector_queries=[self._vector_query(embedding, limit)], filter=filter_, top=limit
            )
            vector_hits = [hit async for hit in vector_results]
            lexical_results = await client.search(
                search_text=query, search_fields=[_CONTENT_FIELD], filter=filter_, top=limit
            )
            lexical_hits = [hit async for hit in lexical_results]
            documents = self._rerank(query, self._blend(vector_hits, lexical_hits, limit))
            log_info(f"Found {len(documents)} documents")
            return documents
        except Exception as exc:
            return self._search_failed("hybrid", exc)

    # -- deletes --------------------------------------------------------------------

    def _delete_where(self, filter_: Optional[str], *, what: str) -> bool:
        """Delete every document matching ``filter_`` (all documents when ``None``): Azure has no
        delete-by-query, so this pages the matching keys and deletes them in batches."""
        try:
            client = self._search_client()
            hits = client.search(search_text="*", filter=filter_, select=[_KEY_FIELD])
            keys = [{_KEY_FIELD: hit[_KEY_FIELD]} for hit in hits]
            for start in range(0, len(keys), _BATCH_SIZE):
                client.delete_documents(documents=keys[start : start + _BATCH_SIZE])
            log_info(f"Deleted {len(keys)} records with {what} from index '{self.index_name}'.")
            return True
        except Exception as exc:
            log_error(f"Error deleting records with {what} from index '{self.index_name}': {exc}")
            return False

    async def _async_delete_where(self, filter_: Optional[str], *, what: str) -> bool:
        try:
            client = self._async_search_client()
            results = await client.search(search_text="*", filter=filter_, select=[_KEY_FIELD])
            keys = [{_KEY_FIELD: hit[_KEY_FIELD]} async for hit in results]
            for start in range(0, len(keys), _BATCH_SIZE):
                await client.delete_documents(documents=keys[start : start + _BATCH_SIZE])
            log_info(f"Deleted {len(keys)} records with {what} from index '{self.index_name}'.")
            return True
        except Exception as exc:
            log_error(f"Error deleting records with {what} from index '{self.index_name}': {exc}")
            return False

    def delete(self) -> bool:
        return self._delete_where(None, what="any key")

    def delete_by_id(self, id: str) -> bool:
        return self._delete_where(self._eq(_ID_FIELD, id), what=f"id '{id}'")

    def delete_by_name(self, name: str) -> bool:
        return self._delete_where(self._eq(_NAME_FIELD, name), what=f"name '{name}'")

    def delete_by_metadata(self, metadata: Dict[str, Any]) -> bool:
        filter_ = _dict_filter(metadata)
        if filter_ is None:
            log_warning("delete_by_metadata called with empty metadata; nothing deleted.")
            return False
        return self._delete_where(filter_, what=f"metadata {metadata!r}")

    def delete_by_content_id(self, content_id: str) -> bool:
        return self._delete_where(self._eq(_CONTENT_ID_FIELD, content_id), what=f"content ID '{content_id}'")

    def get_supported_search_types(self) -> List[str]:
        return [SearchType.vector, SearchType.keyword, SearchType.hybrid]
