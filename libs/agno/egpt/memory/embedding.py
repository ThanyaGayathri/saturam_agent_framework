"""The memory embedding pipeline: one embedder, chosen per mode, used on write and on read (SM-V2 #96).

SM-V1 (#95) gave the vector substrate an embedder-shaped hole and said explicitly that *which*
embedder fills it is this ticket's. This module fills it, and it is the only place that decides:
the model, its dimension, the version tag that stamps every row, and which embedders a mode is
allowed to use at all.

**Why the selection lives here and not in the substrate.** SM-R4's ``DECISION.md`` picks one model
per mode, and ADR 0003 section A.3c makes Mode A's offline capability a hard constraint. Both are
properties of the *deployment*, not of Postgres -- a Qdrant substrate (SM-V4 #98) inherits the same
two rules. Deciding it inside one backend would mean re-deciding it in the next.

**Write and read take the same path** (SM-R4 decision 2). ``embed_records`` and ``embed_query``
are two entry points onto one resolved embedder, so a deployment cannot end up indexing with one
model and querying with another -- vectors from two models are not comparable, and the failure is
silent: retrieval simply gets worse.

``egpt/memory/`` is services-layer (ADR 0001), so nothing here imports a concrete embedder. Mode A's
allow-list is registry *keys*, and the class-level check reads a resolved instance's own module
path rather than importing anything to compare against.

Deliberately not here, each with an owner: the re-embedding migration D8 describes (dual-write,
backfill, cutover) -- ``embedding_version`` is the tag that migration keys on, but running one is a
schema operation with no ticket yet; the query-embed cache that ADR section A.4 names as a latency
mitigation; hybrid BM25 (SM-V3 #97); rank fusion and its knobs (SM-H2 #132).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from egpt.knowledge.embedder import Embedder
from egpt.memory.config import MemoryProviderConfigError
from egpt.providers.model_provider import ProviderMode

__all__ = [
    "MODE_DEFAULTS",
    "OFFLINE_CAPABLE",
    "MemoryEmbedder",
    "resolve_memory_embedder",
]

#: Registry keys whose embedder runs without reaching the network, and so may serve Mode A.
#:
#: **An allow-list, because the constraint is a property and the exceptions are not enumerable.**
#: ADR section A.3c carries forward one named trap -- ``HuggingfaceCustomEmbedder`` is an HF
#: Inference API client despite sitting in the self-hostable list -- and asks that it be rejected
#: rather than documented. Rejecting *that key* would leave ``jina``, ``cohere``, ``openai``,
#: ``voyageai``, ``azure_openai`` and the rest of the eighteen just as remote and just as accepted,
#: which is the same defect with a different name in the message. So the rule is stated the way
#: A.3c states it -- offline capability is required -- and ``huggingface`` is refused as one
#: instance of that rule rather than as the only case anybody remembered.
#:
#: The four keys are the ones A.3c names: ``sentence_transformer`` / ``fastembed`` (ONNX) run the
#: model in-process, ``vllm`` / ``ollama`` reach an in-VPC server that is inside the air gap.
OFFLINE_CAPABLE = frozenset({"sentence_transformer", "fastembed", "vllm", "ollama"})

#: The per-mode default: registry key, model id, dimension. Straight from SM-R4's merged
#: ``DECISION.md`` section 1.
#:
#: Mode A is ``bge-base-en-v1.5`` at 768 -- significantly ahead of bge-small@384 (Holm p=0.002) and
#: of gte-base (p=0.016) on the memory-representative eval, MIT-licensed, ONNX-capable. **Not**
#: ``gte-base``: the SM-R4 PR body still says so and ADR 0003's Context section flags the
#: discrepancy; ``DECISION.md`` is the artifact of record.
#:
#: Mode B is Azure ``text-embedding-3-large`` truncated to 1024 -- a statistical tie with Mode A
#: rather than an upgrade (Holm n.s.), and truncated because the full 3072 exceeds pgvector's
#: 2000-dim ``vector`` limit while 1024 ties it (p=0.82).
MODE_DEFAULTS: Dict[ProviderMode, Dict[str, Any]] = {
    ProviderMode.LOCAL: {"backend": "sentence_transformer", "id": "BAAI/bge-base-en-v1.5", "dimensions": 768},
    ProviderMode.FRONTIER: {"backend": "azure_openai", "id": "text-embedding-3-large", "dimensions": 1024},
}

#: Similarity metric and pipeline revision, the two halves of the version tag SM-R4 section 7 does
#: not take from the embedder. Cosine because every candidate was evaluated normalized, and the
#: substrate's own score normalization assumes it.
_METRIC = "cos"
_REVISION = "v1"


def _model_id(embedder: Any) -> str:
    """The embedder's model identifier, for the version tag.

    Every shipped embedder carries ``id``; the fallback is for a duck-typed one, and it names the
    class rather than inventing an id -- two different classes must not tag their vectors alike.
    """
    declared = getattr(embedder, "id", None)
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    return type(embedder).__name__


def _reject_online_for_local(key: Optional[str], embedder: Any) -> None:
    """Refuse an embedder that reaches the public network when the deployment is air-gapped.

    Two ways to be offline-capable, and an instance needs one of them:

    * it was built from a key in :data:`OFFLINE_CAPABLE`, or its class lives in that key's module
      -- which covers the shipped embedders, including an instance a deployment built itself in
      order to pass ``device`` or ``trust_remote_code`` (SM-R4 section 5 records that
      ``SentenceTransformerEmbedder`` has no passthrough for either, so this is a real path);
    * it **declares** ``offline_capable = True``.

    The declaration is there because a module path is a proxy for the property, not the property. A
    private in-process model outside this tree is genuinely offline and the proxy cannot tell; left
    to the proxy alone the rule refuses correct deployments, which is how a safety check gets an
    ``if`` around it a release later. What A.3c actually forbids is the air gap breaking *silently*,
    and an explicit attribute on the object is the opposite of silent -- somebody asserted it.
    """
    if key is not None:
        if key in OFFLINE_CAPABLE:
            return
        raise MemoryProviderConfigError(
            f"Embedder {key!r} is not offline-capable, and mode={ProviderMode.LOCAL.value!r} (Mode A) is the "
            f"air-gapped deployment path where offline capability is a hard constraint (ADR 0003 A.3c). "
            f"Offline-capable: {', '.join(sorted(OFFLINE_CAPABLE))}. Note that 'huggingface' is *not* among "
            "them despite being listed as self-hostable -- HuggingfaceCustomEmbedder is a remote HF "
            "Inference API client, and selecting it here breaks the air gap silently.",
            field_name="embedder",
        )
    if getattr(embedder, "offline_capable", False) is True:
        return
    module = type(embedder).__module__ or ""
    if module.rsplit(".", 1)[-1] in OFFLINE_CAPABLE:
        return
    raise MemoryProviderConfigError(
        f"{type(embedder).__name__} (from {module!r}) is not one of the offline-capable embedders, and "
        f"mode={ProviderMode.LOCAL.value!r} (Mode A) is the air-gapped deployment path (ADR 0003 A.3c). "
        f"Offline-capable: {', '.join(sorted(OFFLINE_CAPABLE))}. Pass one of those; or set "
        "offline_capable = True on it, which asserts that it reaches nothing outside the air gap; or run "
        f"this tenant in mode={ProviderMode.FRONTIER.value!r} if reaching a provider is intended.",
        field_name="embedder",
    )


@dataclass
class MemoryEmbedder:
    """One resolved embedder, plus the dimension and version tag every stored vector carries.

    The wrapper exists for three things the raw ``Embedder`` does not give the memory path:

    * **A single dimension authority.** ``dimensions`` here is what the vector column is built at
      and what a stored vector is checked against. SM-V1 derives its ``dim`` from this rather than
      from a second setting, because a column width restated beside the model that determines it
      drifts the first time the model changes.
    * **Batch on write-back** (this ticket's fourth criterion). SM-V1 embeds one record per call
      and names this module as the owner, on the grounds that whether a batch may be embedded at
      once is the embedder's property and not the substrate's.
    * **One version tag.** ``embedding_version`` stamps every row so a re-embedding migration has
      something to key its cutover on (SM-R4 section 7, D8).
    """

    embedder: Embedder
    dimensions: int
    embedding_version: str

    def _batch_size(self) -> int:
        """How many texts go to the embedder at once.

        Read from the embedder's own ``batch_size``, which the ``Embedder`` base declares, so a
        provider with a documented request ceiling is honoured rather than overridden from here.
        A non-positive or non-integer value would make the chunk loop either spin or send the whole
        batch, so it falls back rather than either.
        """
        declared = getattr(self.embedder, "batch_size", None)
        if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
            return declared
        return 1

    def _accepts_batches(self) -> bool:
        """Whether this embedder's ``get_embedding`` takes a list of texts.

        ``Embedder.enable_batch`` is the seed's own flag for exactly this, and it is off by default
        -- so an embedder that has not opted in is called one text at a time. Guessing the other
        way is not a performance question: an embedder that takes a ``str`` and is handed a ``list``
        returns one vector for the concatenation, which is silently wrong rather than an error.
        """
        return bool(getattr(self.embedder, "enable_batch", False))

    def _chunks(self, texts: Sequence[str]) -> List[Sequence[str]]:
        if not self._accepts_batches():
            return [[text] for text in texts]
        size = self._batch_size()
        return [texts[start : start + size] for start in range(0, len(texts), size)]

    def _as_vectors(self, result: Any, count: int) -> List[List[float]]:
        """Normalize one embedder call's return into ``count`` vectors, refusing a mismatch.

        A batching embedder returns a list of vectors and a single-text one returns a bare vector;
        both arrive here. The count check is what stops a batched call that silently returned one
        vector for the concatenation from being zipped against the wrong records -- which files
        every record's content under its neighbour's vector, and nothing downstream can see it.
        """
        vectors = [list(result)] if result and not isinstance(result[0], (list, tuple)) else [list(v) for v in result]
        if len(vectors) != count:
            raise MemoryProviderConfigError(
                f"{type(self.embedder).__name__} returned {len(vectors)} embedding(s) for {count} text(s). "
                "A batch must come back one vector per text and in order, or vectors get attached to the "
                "wrong records with nothing downstream able to detect it.",
                field_name="embedder",
            )
        return vectors

    def _checked(self, vectors: List[List[float]]) -> List[List[float]]:
        """Every vector must be the width the column was built at.

        Checked here rather than only at the substrate: an embedder whose ``dimensions`` disagrees
        with what it actually returns otherwise reaches Postgres as ``expected 768 dimensions, not
        384`` on the first write, naming the column instead of the model that caused it.
        """
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise MemoryProviderConfigError(
                    f"{type(self.embedder).__name__} returned a {len(vector)}-dimension vector but this "
                    f"pipeline is configured for {self.dimensions} ({self.embedding_version}). Changing "
                    "dimension is a re-embedding migration, not a restart.",
                    field_name="dimensions",
                )
        return vectors

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a batch, in order, one vector per text."""
        vectors: List[List[float]] = []
        for chunk in self._chunks(texts):
            vectors.extend(self._as_vectors(self.embedder.get_embedding(_call_arg(chunk)), len(chunk)))
        return self._checked(vectors)

    async def aembed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """Async variant of :meth:`embed_texts`, chunked identically.

        Chunks are awaited in sequence rather than gathered. Concurrency across chunks is a real
        speed-up for a network embedder and an over-subscription for a local model that already
        uses every core, and this wrapper serves both -- so it is a per-deployment decision, not
        one to take here on the async half alone and leave the sync half without.
        """
        vectors: List[List[float]] = []
        for chunk in self._chunks(texts):
            result = await self.embedder.async_get_embedding(_call_arg(chunk))
            vectors.extend(self._as_vectors(result, len(chunk)))
        return self._checked(vectors)

    def embed_query(self, text: str) -> List[float]:
        """Embed a query with the **same** embedder the write path used (SM-R4 decision 2)."""
        return self.embed_texts([text])[0]

    async def aembed_query(self, text: str) -> List[float]:
        """Async variant of :meth:`embed_query`."""
        return (await self.aembed_texts([text]))[0]


def _call_arg(chunk: Sequence[str]) -> Any:
    """What one chunk is passed as: the bare text for a single, the list for a batch.

    A one-element chunk is a single call, not a batch of one -- an embedder that does not declare
    ``enable_batch`` takes a ``str``, and every chunk is one element in that case.
    """
    return chunk[0] if len(chunk) == 1 else list(chunk)


def resolve_memory_embedder(
    mode: ProviderMode,
    embedder: Any = None,
    options: Optional[Dict[str, Any]] = None,
    dimensions: Optional[int] = None,
    dimensions_field: str = "dimensions",
) -> MemoryEmbedder:
    """The memory embedding pipeline for ``mode`` -- default, named, or injected.

    Three ways to arrive, one set of rules applied to all of them:

    * ``embedder=None`` takes the mode's SM-R4 default from :data:`MODE_DEFAULTS`.
    * ``embedder="<key>"`` builds a registered embedder by name, so a deployment configures the
      model in the same config object it configures the DSN in.
    * ``embedder=<instance>`` takes an already-built one, which is how a test injects a double and
      how a deployment passes constructor arguments the registry row does not carry.

    ``options`` are constructor keyword arguments and apply only to the first two -- an instance is
    configured by whoever built it, so silently dropping them would leave a deployment's ``device``
    or ``trust_remote_code`` setting inert with nothing to show for it.

    ``dimensions_field`` is the caller's own name for the width, carried onto ``field_name`` of any
    refusal about it -- the same reason ``resolve_substrate`` takes a ``field``. The vector store
    spells this ``dim``, and a caller who passed ``dim=`` and is told ``dimensions`` cannot map the
    refusal back to the argument that caused it.

    Raises:
        MemoryProviderConfigError: the mode is unknown, the embedder is not offline-capable under
            Mode A, the name is not registered, or the dimension cannot be established.
    """
    if not isinstance(mode, ProviderMode):
        raise MemoryProviderConfigError(
            f"mode must be a ProviderMode, got {mode!r}. Mode A is {ProviderMode.LOCAL.value!r} "
            f"(co-located, air-gapped) and Mode B is {ProviderMode.FRONTIER.value!r} (external).",
            field_name="mode",
        )

    key: Optional[str] = None
    declared_dim: Optional[int] = dimensions
    if embedder is None:
        default = MODE_DEFAULTS.get(mode)
        if default is None:
            raise MemoryProviderConfigError(
                f"No default embedder for mode {mode.value!r}; SM-R4's DECISION.md picks one for "
                f"{', '.join(sorted(m.value for m in MODE_DEFAULTS))}. Name an embedder explicitly.",
                field_name="mode",
            )
        key = str(default["backend"])
        # The model id is a constructor argument, and the caller's own `options` win over the
        # default's -- a deployment pinning a different bge revision should not have to stop using
        # the mode default to do it.
        options = {"id": default["id"], **(options or {})}
        if declared_dim is None:
            declared_dim = int(default["dimensions"])
    elif isinstance(embedder, str):
        key = embedder.strip()
        if not key:
            raise MemoryProviderConfigError(
                "embedder is an empty string; pass a registered embedder name, an Embedder instance, "
                "or None to take the mode default.",
                field_name="embedder",
            )
    elif options:
        raise MemoryProviderConfigError(
            "embedder_options applies only when 'embedder' names a registered embedder to build or is "
            "unset; an already-built instance is configured by its own constructor, so these would be "
            "silently dropped.",
            field_name="embedder_options",
        )

    # Refused **before** construction when there is a key: building a remote client to then reject
    # it can read a token off the environment and open a connection, which is precisely what the
    # air gap forbids.
    if mode is ProviderMode.LOCAL and key is not None:
        _reject_online_for_local(key, None)

    resolved = _construct(key, options) if key is not None else embedder

    if mode is ProviderMode.LOCAL and key is None:
        _reject_online_for_local(None, resolved)

    for method in ("get_embedding", "async_get_embedding"):
        if not callable(getattr(resolved, method, None)):
            raise MemoryProviderConfigError(
                f"{type(resolved).__name__} has no callable {method}(); both halves of the memory path "
                "embed, so an embedder missing either one leaves one half broken.",
                field_name="embedder",
            )

    resolved_dim = _resolved_dimensions(resolved, declared_dim, dimensions_field)
    return MemoryEmbedder(
        embedder=resolved,
        dimensions=resolved_dim,
        embedding_version=f"{_model_id(resolved)}/d{resolved_dim}/{_METRIC}/{_REVISION}",
    )


def _construct(key: str, options: Optional[Dict[str, Any]]) -> Any:
    """Build a registered embedder, reporting an unknown name as a configuration fault.

    The name is checked against the registry rather than caught from ``construct``: that signals an
    unknown name with ``ValueError``, and so does any embedder validating its own constructor
    arguments, so catching would relabel a bad ``device`` as a bad embedder name.
    """
    from egpt.registry import discovery

    known = set(discovery.names("embedder"))
    if key not in known:
        raise MemoryProviderConfigError(
            f"Unknown embedder {key!r}. Registered: {', '.join(sorted(known)) or '<none>'}.",
            field_name="embedder",
        )
    # An ImportError from a missing SDK propagates: the embedder's own "pip install
    # sentence-transformers" message is more useful than anything this frame could add.
    try:
        return discovery.construct("embedder", key, **(options or {}))
    except (ImportError, MemoryProviderConfigError):
        raise
    except Exception as exc:
        raise MemoryProviderConfigError(
            f"Could not construct the embedder {key!r}: {exc}", field_name="embedder"
        ) from exc


def _resolved_dimensions(embedder: Any, declared: Optional[int], field: str) -> int:
    """The pipeline's dimension: stated, or taken from the embedder -- and refused when they differ.

    A disagreement is a genuine ambiguity rather than a precedence question. The stated value is
    what the column gets built at and the embedder's is what it will actually return, so whichever
    one loses, every write fails against a table that has to be dropped to fix it.
    """
    known = getattr(embedder, "dimensions", None)
    known = known if isinstance(known, int) and not isinstance(known, bool) and known > 0 else None
    if declared is None:
        if known is None:
            raise MemoryProviderConfigError(
                f"dimensions is unset and {type(embedder).__name__}.dimensions is "
                f"{getattr(embedder, 'dimensions', None)!r}, so the vector column has no width. Pass "
                "dimensions explicitly.",
                field_name=field,
            )
        return known
    if isinstance(declared, bool) or not isinstance(declared, int) or declared <= 0:
        raise MemoryProviderConfigError(
            f"dimensions must be a positive integer, got {declared!r}.",
            field_name=field,
        )
    if known is not None and known != declared:
        raise MemoryProviderConfigError(
            f"dimensions is {declared} but {type(embedder).__name__}.dimensions is {known}. One of them "
            "is wrong, and picking either builds a column the other cannot write to.",
            field_name=field,
        )
    return declared
