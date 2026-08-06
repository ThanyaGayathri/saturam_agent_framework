# Finding: egpt SentenceTransformerReranker requires sentence-transformers >= 4.0

**Severity:** low (compatibility / pinning) — **not a core logic defect.**
**Component:** `libs/agno/egpt/knowledge/reranker/sentence_transformer.py`
**Status:** worked around in benchmark scope; core untouched.

## What

The adapter builds its cross-encoder as:

```python
self.cross_encoder = CrossEncoder(model_name_or_path=self.model, model_kwargs=self.model_kwargs)
```

`model_name_or_path=` and `model_kwargs=` are the **sentence-transformers >= 4.0**
CrossEncoder signature. In sentence-transformers <= 3.4.1 the constructor is
`CrossEncoder(model_name=..., automodel_args=...)`, so the call raises
`TypeError: CrossEncoder.__init__() got an unexpected keyword argument 'model_name_or_path'`
and the adapter's try/except swallows it and returns the documents **unreranked**
(a silent quality regression, not a crash).

## Why it surfaced here

The measurement host is an Intel (x86_64) Mac, where the last available PyTorch wheel
is 2.2.2. sentence-transformers >= 4.0 pulls a `transformers` that requires torch >= 2.4,
so the host is capped at sentence-transformers 3.4.1 — below the adapter's target API.

On the intended deployment stack (Linux, torch >= 2.4, sentence-transformers >= 4.0)
the adapter runs unmodified. So this is an environment/pinning seam, not a bug in the
rerank logic.

## Workaround used (benchmark only, core untouched)

`benchmarks/memory_retrieval/rerank.py` injects a positionally-constructed
`CrossEncoder(model)` (the first positional arg is stable across ST versions) into the
adapter's own `cross_encoder=` field. egpt's `_rerank` logic (pair building, `.predict`,
sort, top_n) then runs **unchanged**. No `libs/agno/**` edit.

## Recommendation for the implementation task (SM-H2/H3)

- Pin `sentence-transformers >= 4.0` for the reranker path, **or** make the adapter
  version-tolerant (try `model_name_or_path=`, fall back to positional / `model_name=`).
- Consider not swallowing the CrossEncoder construction error into a silent
  no-op rerank — a misconfigured reranker should fail loudly, not degrade quality invisibly.
