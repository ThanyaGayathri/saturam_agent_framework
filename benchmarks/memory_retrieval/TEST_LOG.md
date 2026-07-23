# SM-R8 — TEST_LOG

Run log for the retrieval fusion + reranker decision benchmark. Newest first.

---

### Phase 3 — option matrix + sweeps (measurement)

**Status:** PASS (4 datasets fused; reranker on 3; sweeps done)

**Environment:** venv `.venvs/smr8`; `OMP_NUM_THREADS=8`. Dense embedder switched to local
`all-MiniLM-L6-v2` (egpt `SentenceTransformerEmbedder`, CPU) after Azure hit a TPM rate limit
on LongMemEval's long-doc corpora (findings/embedding-async-batch-hang.md). Reranker via egpt
`SentenceTransformerReranker` with an injected CPU CrossEncoder (findings/reranker-st-version-seam.md).

**Runs (committed under results/):**
- `matrixLocal` — options a–e on scifact/nfcorpus/longmemeval (local embedder). Full nDCG@10 /
  recall / MRR / latency + paired-bootstrap significance.
- `fiqaFusion` — options a,b,c,e on fiqa (57k-corpus; ~68 min one-time embed on CPU). Reranker
  (d) skipped on fiqa (conclusion settled; CPU rerank of 57k-corpus prohibitively slow).
- `matrixA` — earlier Azure `text-embedding-3-small` partials on scifact/nfcorpus (strong-embedder
  cross-check).
- `sweeps` — RRF k, weighted vector_weight, composite recency_weight.
- `rerankBge` — bounded bge-reranker-v2-m3 quality/latency spot-check (CPU).

**Headline results (nDCG@10):** weighted fusion **significantly beats RRF on all 4 datasets**
(scifact +0.034, nfcorpus +0.010, longmemeval +0.028, fiqa +0.027; all p<0.01). MiniLM rerank
neutral/harmful (significant −0.052 loss on the memory set) at ~2.0–2.5 s/query CPU. Composite
recency monotonically hurts (significant −0.060 on memory). Details in DECISION.md / results/.

**Verification:** metrics via pytrec_eval; every ranked gap backed by a paired-bootstrap CI
excluding 0; sync==async parity checked on all 5 options during the Phase-2 smoke.

---

### Phase 1 — eval-data + metrics (data-quality gate)

**Status:** PASS

**Environment:** dedicated venv `.venvs/smr8` (Python 3.12), egpt editable +
`numpy pytrec_eval openai python-dotenv datasets requests`. Azure embeddings
deployments confirmed live: `text-embedding-3-small` (1536-d), `text-embedding-3-large`
(3072-d). egpt `AzureOpenAIEmbedder` verified end-to-end (sync + async) wired to `.env`.

**What was tested:**
- `data.py` loaders for BEIR (scifact/nfcorpus/fiqa) and LongMemEval-S, unified behind
  `RetrievalTask`. Downloaded + parsed all four; printed corpus/query/qrels stats.
- `metrics.py` (pytrec_eval nDCG@10 / recall@{10,50,100} / MRR + bootstrap CI +
  paired-bootstrap significance). Known-answer sanity: perfect ranking -> all 1.0;
  worst ranking -> nDCG 0.501, MRR 0.25 (1/4). Paired a-vs-b delta 0.40 CI [0.1,0.7]
  significant; a-vs-a tie p=1.0 significant=0.

**Dataset stats (measured):**
| dataset | queries | docs | rel/q | labels | time |
| --- | --- | --- | --- | --- | --- |
| beir/scifact | 300 | 5,183 | 1.13 | binary | no |
| beir/nfcorpus | 323 | 3,633 | 38.19 | **graded** | no |
| beir/fiqa | 648 | 57,638 | 2.63 | binary | no |
| longmemeval/longmemeval_s | 470 | 23,566 | 1.89 | binary | **yes** |

**Notes / limits:** LongMemEval is per-question (~50-doc) corpora, so recall@{50,100}
saturate there -- nDCG@10 / recall@10 / MRR are the discriminating metrics for it;
recall@100 discriminates on the large BEIR corpora. BEIR carries no timestamps, so
composite time-aware scoring (option e) is measured only on LongMemEval. 30 LongMemEval
abstention questions excluded (no relevant doc to rank).

---
