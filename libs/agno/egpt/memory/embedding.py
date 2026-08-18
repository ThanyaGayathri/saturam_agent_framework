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
from egpt.utils.log import log_warning

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
#:
#: **1024 is a constraint, not this class's own default.** ``AzureOpenAIEmbedder.__post_init__``
#: sets ``dimensions=1536`` for any ``text-embedding-3-*`` id when none is given, so an
#: already-built instance passed to ``embedder=`` without an explicit ``dimensions=1024`` resolves
#: to 1536 and fails the dimension-agreement check with a number that names neither pgvector's
#: limit nor SM-R4's choice. Going through this default, or passing ``dimensions=1024`` alongside
#: an injected instance, avoids it.
MODE_DEFAULTS: Dict[ProviderMode, Dict[str, Any]] = {
    ProviderMode.LOCAL: {"backend": "sentence_transformer", "id": "BAAI/bge-base-en-v1.5", "dimensions": 768},
    ProviderMode.FRONTIER: {"backend": "azure_openai", "id": "text-embedding-3-large", "dimensions": 1024},
}

#: Similarity metric and pipeline revision, the two halves of the version tag SM-R4 section 7 does
#: not take from the embedder. Cosine because every candidate was evaluated normalized, and the
#: substrate's own score normalization assumes it.
_METRIC = "cos"
#: Bump when the metric, a truncation strategy, or query preprocessing changes -- anything that
#: makes a vector stored under this tag no longer comparable to one embedded fresh. The
#: re-embedding migration in SPEC §11.3 keys its cutover on this value; an unbumped revision after
#: such a change makes stale and fresh vectors indistinguishable by tag, silently invalidating
#: retrieval rather than triggering a migration.
_REVISION = "v1"

#: Field separator for :data:`MemoryEmbedder.embedding_version`.
#:
#: **Not ``/``**, which SM-R4 section 7's example uses. That example writes the model as
#: ``bge-base-en-v1.5``, but the id an embedder actually carries is the canonical HF
#: ``BAAI/bge-base-en-v1.5`` -- so a ``/`` separator yields five fields where the migration keying on
#: this tag expects four, and no amount of care at the parsing end recovers which ``/`` was which.
_TAG_SEPARATOR = "|"


def _version_tag(model_id: str, dimensions: int) -> str:
    """``<model>|d<dim>|<metric>|<rev>`` -- four fields, and the separator cannot appear inside one.

    Refused rather than escaped or silently joined. This tag is the key a re-embedding migration
    cuts over on, so a model id carrying the separator would split into a field count that does not
    match the format, and the migration would mis-key rather than fail -- reading some other field
    as the dimension. No shipped id contains ``|``; the check is here so that stays a fact rather
    than an assumption about ids nobody has published yet.
    """
    if _TAG_SEPARATOR in model_id:
        raise MemoryProviderConfigError(
            f"Embedder id {model_id!r} contains {_TAG_SEPARATOR!r}, which separates the fields of the "
            f"embedding version tag, so the tag would not parse back into four fields. A re-embedding "
            "migration keys its cutover on that tag (SM-R4 section 7). Rename the model id.",
            field_name="embedder",
        )
    return _TAG_SEPARATOR.join((model_id, f"d{dimensions}", _METRIC, _REVISION))


def _model_id(embedder: Any) -> str:
    """The embedder's model identifier, for the version tag.

    Every shipped embedder carries ``id``; the fallback is for a duck-typed one, and it names the
    class rather than inventing an id -- two different classes must not tag their vectors alike.

    **Module-qualified, not just the bare class name.** ``__name__`` alone collides for two
    same-named classes from different modules -- a deployment swapping in its own duck-typed
    embedder and landing on a common name (``CustomEmbedder``, ``Wrapper``) would tag both models'
    vectors identically, and nothing downstream can tell the difference back apart. ``__module__``
    plus ``__qualname__`` (not bare ``__name__``, which drops a class nested inside a function or
    another class) is unique per class object -- Python does not allow two distinct classes to
    share both.
    """
    declared = getattr(embedder, "id", None)
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    cls = type(embedder)
    return f"{cls.__module__}.{cls.__qualname__}"


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

    **The module-path arm is a convenience for the four in-tree embedders, not a security boundary,
    and nothing here is one.** It matches on the last dotted segment, so a third-party
    ``anything.sentence_transformer`` passes without being offline in any sense; and the
    ``offline_capable`` arm above it is an unverified self-assertion by design. Neither can be
    tightened into a guarantee -- no check available in this process can prove a Python object will
    not open a socket. What this rule buys is that reaching the network under Mode A takes a
    deliberate act (naming a module that looks in-tree, or declaring an attribute that says so)
    rather than being the default outcome of a config typo, which is the silent breakage A.3c is
    about. Deployments that need the guarantee enforce it at the network layer.
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


@dataclass(frozen=True)
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

    **Frozen.** The same-embedder-for-write-and-read guarantee (SM-R4 decision 2) is this class's
    whole reason to exist, and a mutable ``dimensions`` or ``embedding_version`` would let it be
    silently violated after construction -- every stored vector under a changed tag reads as
    belonging to a model it was never embedded by. All three fields are set once, at resolution
    time, so freezing costs nothing a caller was relying on.
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
        if self._accepts_batches():
            # Only worth a log for an embedder that actually batches -- a non-batching one is
            # every call at size 1 regardless, so the fallback is not silently costing anything.
            log_warning(
                f"{type(self.embedder).__name__} declares batching but batch_size is "
                f"{declared!r}, not a positive int; falling back to one text per call."
            )
        return 1

    def _accepts_batches(self) -> bool:
        """Whether this embedder's ``get_embedding`` takes a **list** of texts and returns one
        vector per element.

        **Deliberately not ``Embedder.enable_batch``**, which is a different proposition. Everywhere
        else in this tree that flag gates a *separate* batch API -- every vectordb reads it as
        ``enable_batch and hasattr(embedder, "async_get_embeddings_batch_and_usage")`` -- and eight
        embedders implement that API. ``get_embedding`` taking a list is an unrelated property that
        exactly one shipped embedder has. Reading ``enable_batch`` for it inverts the meaning where
        the two disagree: ``AzureOpenAIEmbedder`` has the batch API and would legitimately carry
        ``enable_batch=True``, but its ``get_embedding`` returns ``data[0].embedding`` -- one vector
        for N texts.

        That batch API is not the mechanism here either, for a reason that outlives this class: it
        is **async-only** (zero sync implementations in the tree). Routing through it would batch
        the async half and leave the sync half calling per text, and a twin that makes a different
        decision is the defect class this package guards hardest. ``get_embedding(list)`` keeps both
        halves identical, because a batching embedder's ``async_get_embedding`` delegates to the
        sync one.

        So the capability is **declared**, like ``offline_capable`` above it and for the same
        reason: a flag that means something adjacent is a proxy for the property, not the property.
        Undeclared means per-text, which is always correct and sometimes slower -- the safe default,
        since an embedder that takes a ``str`` and is handed a ``list`` embeds the concatenation and
        returns one vector, and only the count check downstream would notice.
        """
        return getattr(self.embedder, "accepts_text_batches", False) is True

    def _chunks(self, texts: Sequence[str]) -> List[Sequence[str]]:
        if not self._accepts_batches():
            return [[text] for text in texts]
        size = self._batch_size()
        return [texts[start : start + size] for start in range(0, len(texts), size)]

    def _call_arg(self, chunk: Sequence[str]) -> Any:
        """What one chunk is passed as -- **decided by the embedder's declared shape, not the chunk's
        length.**

        A batching embedder always gets a list, including a one-element list, and a non-batching one
        always gets the bare text. Length-based unwrapping looks equivalent and is not: it makes
        every ``embed_query`` -- always exactly one text -- take the bare-string branch regardless of
        what the embedder declared, so a strict batch-only embedder works on write and fails on
        read. One call shape per embedder, whatever the batch happens to contain.
        """
        return list(chunk) if self._accepts_batches() else chunk[0]

    def _as_vectors(self, result: Any, count: int) -> List[List[float]]:
        """Normalize one embedder call's return into ``count`` vectors, refusing a mismatch.

        A batching embedder returns a list of vectors and a single-text one returns a bare vector;
        both arrive here. The count check is what stops a batched call that silently returned one
        vector for the concatenation from being zipped against the wrong records -- which files
        every record's content under its neighbour's vector, and nothing downstream can see it.

        **Everything this inspects is refused rather than assumed**, because the input is a
        third-party return value and every shortcut here raises something that is not this seam's
        error type. ``if result`` on a 2D numpy array raises ``ValueError: truth value ... is
        ambiguous`` from numpy; iterating ``None`` raises ``TypeError``. Both escaped a seam whose
        contract is that a bad embedder arrives as ``MemoryProviderConfigError`` naming the
        embedder, so a caller narrowing to that type saw an unrelated exception type instead.

        Nesting is decided by whether the first element is **iterable**, not by whether it is a
        ``list`` or ``tuple``: a row of a 2D numpy array is an ``ndarray``, which is neither, so the
        stricter test read a batch of N vectors as one very wide vector and reported a dimension
        mismatch pointing at the model. ``str`` is excluded because it is iterable and is never a
        vector element.
        """
        # One guard, not a `None` arm beside it: `len(None)` raises `TypeError` and lands here
        # already, so a separate `result is None` branch changed the message and nothing else. A
        # branch no test can distinguish is one that rots, and `None` is named in the message
        # instead -- it is the case that actually happens, from an embedder swallowing its own
        # failure.
        try:
            length = len(result)
        except TypeError:
            raise MemoryProviderConfigError(
                f"{type(self.embedder).__name__}.get_embedding returned {type(result).__name__} for "
                f"{count} text(s), which has no length, so it is neither a vector nor a sequence of "
                "them. An embedder that swallows its own failure returns None here, and the record "
                "would otherwise be stored with no usable embedding.",
                field_name="embedder",
            ) from None
        if length == 0:
            raise MemoryProviderConfigError(
                f"{type(self.embedder).__name__}.get_embedding returned an empty result for {count} text(s).",
                field_name="embedder",
            )
        first = result[0]
        nested = not isinstance(first, (str, bytes)) and hasattr(first, "__iter__")
        vectors = [list(v) for v in result] if nested else [list(result)]
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

        **Called per chunk, not once over the whole batch** -- both loops below check each chunk's
        vectors before asking the embedder for the next one, so a wrong-dimension embedder is
        refused after the first API call it makes, not after every chunk in a large batch has
        already been paid for and only the last one's result is inspected.
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
            result = self._as_vectors(self.embedder.get_embedding(self._call_arg(chunk)), len(chunk))
            vectors.extend(self._checked(result))
        return vectors

    async def aembed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """Async variant of :meth:`embed_texts`, chunked identically.

        Chunks are awaited in sequence rather than gathered. Concurrency across chunks is a real
        speed-up for a network embedder and an over-subscription for a local model that already
        uses every core, and this wrapper serves both -- so it is a per-deployment decision, not
        one to take here on the async half alone and leave the sync half without.
        """
        vectors: List[List[float]] = []
        for chunk in self._chunks(texts):
            result = self._as_vectors(await self.embedder.async_get_embedding(self._call_arg(chunk)), len(chunk))
            vectors.extend(self._checked(result))
        return vectors

    def embed_query(self, text: str) -> List[float]:
        """Embed a query with the **same** embedder the write path used (SM-R4 decision 2)."""
        return self.embed_texts([text])[0]

    async def aembed_query(self, text: str) -> List[float]:
        """Async variant of :meth:`embed_query`."""
        return (await self.aembed_texts([text]))[0]


def resolve_memory_embedder(
    mode: ProviderMode,
    embedder: Any = None,
    embedder_options: Optional[Dict[str, Any]] = None,
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

    ``embedder_options`` are constructor keyword arguments and apply only to the first two -- an
    instance is configured by whoever built it, so silently dropping them would leave a
    deployment's ``device`` or ``trust_remote_code`` setting inert with nothing to show for it.
    Named to match the ``field_name`` a refusal about it carries, rather than the shorter
    ``options`` this parameter used to be -- a caller reading that refusal to fix their config
    looked for a parameter with that exact name.

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
        # Both the id **and** the dimension are constructor arguments, and the caller's own
        # `embedder_options` win over the default's -- a deployment pinning a different bge
        # revision should not have to stop using the mode default to do it.
        #
        # `dimensions` is passed rather than only recorded because an embedder does not infer it
        # from the model: `SentenceTransformerEmbedder.dimensions` is 384 whatever it loads, so the
        # Mode A default resolved to a 768-model declaring 384 and the pipeline refused its own
        # default. Found by running the cookbook, not by a unit test -- every double declares the
        # width it returns, which is the one thing the real class does not do.
        embedder_options = {"id": default["id"], "dimensions": int(default["dimensions"]), **(embedder_options or {})}
        if declared_dim is None:
            # From the **merged** options, not the mode default. These are the same value until a
            # caller passes `embedder_options={"dimensions": N}`, at which point the constructed
            # embedder is N wide and taking the default here refuses the caller's own setting while
            # quoting a number they never passed -- "dimensions is 768 but Embedder.dimensions is
            # 512", where 768 came from this line. One source of truth: whatever the embedder is
            # being built at.
            declared_dim = embedder_options["dimensions"]
    elif isinstance(embedder, str):
        key = embedder.strip()
        if not key:
            raise MemoryProviderConfigError(
                "embedder is an empty string; pass a registered embedder name, an Embedder instance, "
                "or None to take the mode default.",
                field_name="embedder",
            )
    elif embedder_options:
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

    resolved = _construct(key, embedder_options) if key is not None else embedder

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
        embedding_version=_version_tag(_model_id(resolved), resolved_dim),
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
