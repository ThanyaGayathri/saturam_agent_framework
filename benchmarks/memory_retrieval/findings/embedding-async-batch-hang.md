# Finding: egpt async batch embedding hangs + swallows errors; Azure TPM limits long-doc corpora

**Severity:** medium (measurement reliability). **Not a fix to core here** — worked
around in benchmark scope; documented for the implementation task.
**Component:** `libs/agno/egpt/knowledge/embedder/azure_openai.py`
(`async_get_embeddings_batch_and_usage`) and deployment config.

## What we hit

1. **The async batch helper hangs.** Driving `AzureOpenAIEmbedder.async_get_embeddings_batch_and_usage`
   across many per-task corpora, calls would print `Getting embeddings for N texts`
   and then hang indefinitely with the process at 0% CPU — a dropped connection with
   no client-side timeout. The default client sets no `timeout`, so a stalled socket
   never raises.
2. **Errors are swallowed.** On failure the helper logs a warning and appends `[]` for
   the failed rows instead of raising, so a caller gets a silently truncated/ragged
   embedding set (here, an inhomogeneous-array crash downstream; in production, silent
   under-embedding).
3. **Azure TPM rate limit on long-doc corpora.** LongMemEval-S tasks are ~125k tokens
   each (long chat sessions). Firing task corpora back-to-back returns
   `429 RateLimitReached` (confirmed: 8 rapid full-task calls → 7×429). Embedding all
   470 tasks is ~59M tokens ≈ ~8h at this deployment's TPM — impractical for a
   reproducible benchmark.

## Workaround used (benchmark scope, core untouched)

- `embcache.py` uses the embedder's **sync** client with explicit batching + our own
  retry/backoff + dimension validation for OpenAI/Azure embedders, and the **native
  list-batch** path for local `SentenceTransformerEmbedder`.
- Per the SM-R8 measurement-conditions decision, the **dense embedder was switched to a
  local offline sentence-transformers model** (`all-MiniLM-L6-v2`) uniformly. This is
  reproducible, offline (matches the air-gapped Mode-A path), and rate-limit-free. The
  partial Azure `text-embedding-3-small` BEIR numbers we did collect are retained as a
  strong-embedder cross-check.

## Recommendations for the implementation task (SM-R4 / SM-Hx)

- Set a per-request `timeout` and rely on the SDK `max_retries` on the Azure/OpenAI
  embedder client so a dropped connection raises instead of hanging.
- Do not swallow batch-embedding failures into `[]`; surface them (or retry), so
  callers can't silently under-embed a corpus.
- Size the Azure embedding deployment's TPM for the memory-ingest workload, or push
  embedding to an offline/self-hosted embedder for Mode A (air-gapped) as SM-R8 assumes.
