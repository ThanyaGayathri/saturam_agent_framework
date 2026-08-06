# Findings — egpt embedder adapters (for the SM-C* implementation phase)

Issues found in `libs/agno/egpt/knowledge/embedder/**` while running the SM-R4
benchmark. **Per the decision-task working model, none of these are committed to
core here.** Where a fix was needed to obtain true (fixed-code) numbers, the
patch is saved next to this file and the benchmark result is footnoted; apply
the patch in the implementation task.

| # | File | Severity | Fix included | Blocks benchmarking? |
|---|------|----------|--------------|----------------------|
| 1 | `sentence_transformer.py` | medium | `st_trust_remote_code.patch` | Yes — blocks nomic-embed (custom modeling code) |
| 2 | `huggingface.py` | medium | none (design note) | No |

---

## Finding 1 — `SentenceTransformerEmbedder` cannot load custom-code models

**File:** `libs/agno/egpt/knowledge/embedder/sentence_transformer.py`

`__post_init__` constructs the model with only the id:

```python
self.sentence_transformer_client = SentenceTransformer(model_name_or_path=self.id)
```

Models whose repo ships custom modeling code — notably
`nomic-ai/nomic-embed-text-v1.5` — require `trust_remote_code=True` (and often a
`model_kwargs` / `device` passthrough). Because the adapter exposes no way to
forward these, `SentenceTransformer(...)` raises and the model cannot be used at
all. This is a real capability gap: the memory tier cannot select an otherwise
strong Matryoshka model through the current adapter.

**Fix (see `st_trust_remote_code.patch`):** add optional `trust_remote_code`,
`device`, and `model_kwargs` fields and forward them to the `SentenceTransformer`
constructor. Backwards compatible (all default to off/None).

**Effect on this benchmark:** the patch was applied *locally and uncommitted* to
measure `nomic-1.5`. Its row in `results/` and `DECISION.md` is marked with a
footnote `[patched]`. Core was reverted before committing.

---

## Finding 2 — `HuggingfaceCustomEmbedder` is not air-gapped despite being listed as self-hostable

**File:** `libs/agno/egpt/knowledge/embedder/huggingface.py`

The issue's seed context lists `huggingface` among the **self-hostable** embedders,
but the adapter as written calls the **HuggingFace Inference API** over the
network:

```python
from huggingface_hub import AsyncInferenceClient, InferenceClient
...
self.client.feature_extraction(text=text, model=self.id)   # remote HTTP call
```

With `api_key` unset it hits the public inference endpoint; either way it makes
an outbound call and is **not usable in Mode A** (air-gapped VPC). For local
inference the correct adapters are `SentenceTransformerEmbedder`,
`FastEmbedEmbedder`, or the `vllm`/`ollama` server adapters pointed at an
in-VPC endpoint.

**Implication for SM-R4:** treat `huggingface` (as implemented) as a Mode-B-style
*remote* embedder, not a Mode A option. If a HF-hosted TEI/vLLM endpoint inside
the VPC is desired, use `openai_like`/`vllm` against that endpoint instead. No
code change strictly required for the decision, but the adapter's docstring/name
is misleading and should be clarified in the implementation phase.
