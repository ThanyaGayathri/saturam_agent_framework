"""Choosing the memory embedder per deployment mode (SM-V2 #96).

`13_vector_substrate_pgvector.py` shows the store; this shows what fills the embedder-shaped hole
in it. Nothing here needs a database -- the pipeline is resolved directly, because what SM-V2
decides happens before any row is written.

Three things it decides, each shown below:

1. **Which model, per mode.** Mode A (`ProviderMode.LOCAL`, air-gapped) gets bge-base-en-v1.5 at
   768; Mode B (`FRONTIER`) gets Azure text-embedding-3-large truncated to 1024. Both are SM-R4's
   measured choices, and 1024 rather than the native 3072 because pgvector's `vector` type stops at
   2000 dimensions.
2. **That Mode A refuses an embedder which reaches the network.** ADR 0003 section A.3c makes
   offline capability a hard constraint there, and names `HuggingfaceCustomEmbedder` as the trap:
   it is a remote HF Inference API client despite being listed among the self-hostable embedders,
   so selecting it in an air-gapped deployment breaks the air gap with nothing to show for it.
3. **One embedder for write and for query.** Vectors from two models are not comparable, and the
   failure is silent -- nothing raises, retrieval just quietly gets worse.

Run it:

    .venvs/demo/bin/python cookbook/11_memory/14_memory_embedding_pipeline.py

The Mode A default downloads bge-base (~440 MB) the first time. The stub below is used for the
behaviour that does not need a real model, so the download happens once, at the end.
"""

from egpt.integrations.knowledge.embedder.sentence_transformer import (
    SentenceTransformerEmbedder,
)
from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.embedding import (
    MODE_DEFAULTS,
    OFFLINE_CAPABLE,
    resolve_memory_embedder,
)
from egpt.providers.model_provider import ProviderMode


class LocalStub:
    """A deterministic in-process embedder, standing in for a private model.

    `offline_capable` is how an embedder outside the framework's own list says it reaches nothing
    beyond the air gap. A module path cannot answer that question, and a rule that refused every
    custom embedder would be one deployments route around.
    """

    id = "local-stub"
    dimensions = 4
    offline_capable = True

    def get_embedding(self, text):
        return [float(len(text)), 0.0, 0.0, 0.0]

    async def async_get_embedding(self, text):
        return self.get_embedding(text)


print("What each mode selects, before anything is built:")
for mode, default in MODE_DEFAULTS.items():
    label = "Mode A (air-gapped)" if mode is ProviderMode.LOCAL else "Mode B (provider)"
    print(
        f"  {label}: {default['backend']} / {default['id']} at {default['dimensions']}d"
    )

print("\nOffline-capable embedders, the only ones Mode A accepts:")
print(f"  {', '.join(sorted(OFFLINE_CAPABLE))}")

# The carried-forward A.3c check. Refused at config validation rather than documented -- and
# refused before construction, because building a remote client to then reject it can read a token
# off the environment and open a connection, which is the thing the air gap forbids.
print("\nMode A refuses an embedder that reaches the network:")
for name in ("huggingface", "openai", "cohere"):
    try:
        resolve_memory_embedder(ProviderMode.LOCAL, name)
    except MemoryProviderConfigError as error:
        print(f"  {name}: refused ({error.field_name})")

# The same names are fine in Mode B, which is the deployment where reaching a provider is the point.
# A check that fired in both modes would be one nothing could satisfy.
print("\nMode B accepts them; only the air-gapped mode constrains the choice.")

pipeline = resolve_memory_embedder(ProviderMode.LOCAL, LocalStub())

# Every stored vector carries this tag: model, dimension, metric, revision. It is what a
# re-embedding migration keys its cutover on, so it is derived from the resolved embedder rather
# than written beside it -- a copy drifts the first time the model changes.
print(f"\nVersion tag stamped on every row: {pipeline.embedding_version}")

# Write-back embeds the batch; a query embeds one text. Both go through the same resolved embedder,
# which is what makes the resulting vectors comparable at all.
stored = pipeline.embed_texts(
    [
        "ada moved to berlin in march",
        "bob prefers the lisbon office",
        "the customer is on the enterprise plan",
    ]
)
asked = pipeline.embed_query("which city did ada relocate to")
print(
    f"Embedded {len(stored)} memories on write and 1 query on read, all at {pipeline.dimensions}d"
)

# A batching embedder is called once per chunk rather than once per memory. The capability is
# declared as `accepts_text_batches` on the embedder -- deliberately not `Embedder.enable_batch`,
# which gates a different (async-only) batch API used by the knowledge pipeline. `LocalStub` does
# not declare it, so it is called one text at a time; an embedder that takes a string and is handed
# a list returns one vector for the concatenation, which is silently wrong.
print(
    f"Batches accepted by this embedder: {getattr(LocalStub, 'accepts_text_batches', False)}"
)
print(
    f"The shipped Mode A embedder declares it: {SentenceTransformerEmbedder.accepts_text_batches}"
)

# The real Mode A default, last because it downloads the model.
print("\nResolving the real Mode A default (downloads bge-base on first run)...")
mode_a = resolve_memory_embedder(ProviderMode.LOCAL)
print(f"  {mode_a.embedding_version} at {mode_a.dimensions}d")
print(
    f"  a query vector has {len(mode_a.embed_query('which city did ada relocate to'))} dimensions"
)
