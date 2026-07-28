# SM-R8 — TEST_LOG

Run log for the retrieval fusion + reranker decision benchmark. Newest first.

---

### Phase 5 — PR #16 full review response (paranthaman-commits, 2 major + 2 minor)

**Status:** PASS — all 4 review points addressed with real measurement or explicit
reconciliation, none hand-waved.

1. **Major — 3-leg fusion untested (`DECISION.md:25`):** added
   `graph_leg_experiment.py` — a real Personalized PageRank graph leg (k-NN over the
   corpus' own dense embeddings, no LLM/KG needed) fused with dense+BM25. Measured on
   scifact (n=300, real paired-bootstrap significance): weighted beats RRF by **+0.076**
   in the 3-leg case (p=0.0001), *wider* than the 2-leg gap (+0.023) — the opposite of
   the doc's "RRF wins once graph joins" theory. `DECISION.md` §3.5 added; §1.1/§3/§6/§8/§9
   downgraded the RRF-switch from asserted to explicitly provisional, gated on measuring
   against SM-G9's real graph retriever.
2. **Major — embedder mismatch, LongMemEval specifically flagged (`DECISION.md:79`):**
   Phase 4 (below) had already re-validated scifact+nfcorpus on the corrected
   `bge-base-en-v1.5` embedder; extended to **LongMemEval** — the dataset the reviewer
   called out as most load-bearing (biggest measured fusion gain, +0.094 with MiniLM,
   previously untested on a strong embedder due to an Azure TPM limit).
   `results_20260728T090818Z_smR4EmbedderVerifyLME.json`: vector 0.8717, RRF 0.9104,
   weighted 0.9297 — **every relationship (weighted>RRF, RRF>vector, weighted>vector)
   stays significant on the correct embedder** (all p<0.001), unlike scifact/nfcorpus
   where the advantage collapsed to ties. So the reviewer's specific worry — biggest gain
   sitting on the weakest embedder — does **not** hold up: the memory-relevant gain
   shrinks but survives; the general-IR gain (smaller to begin with) is what washes out.
   `DECISION.md` §3.4 rewritten with the per-domain finding.
3. **Minor — reranker quality verdict overstated (`DECISION.md:104`):** `DECISION.md`
   §3.1/§3.1c reframed: "neutral/harmful" was really dataset-specific (hurt longmemeval,
   helped nfcorpus, tied scifact — n=1 for the harm), and the recommended Mode-A reranker
   (`bge-reranker-v2-m3`) was latency-tested but never quality-tested (only the weaker
   MiniLM cross-encoder was). Flagged "which reranker if enabled" as fully open, not
   settled — recommend benchmarking `bge-reranker-v2-m3`/`infinity` on quality in SM-H3.
4. **Minor — recency vs SM-R5 (`DECISION.md:41`):** read SM-R5's actual
   `benchmarks/memory_decay/DECISION.md` (not assumed). Both decisions independently
   measured that adding recency to ranking *costs* nDCG/recall — SM-R5 says so directly
   (§2.3: "no_decay significantly beats [importance_weighted] on raw recall/nDCG"). They
   diverge only in what that cost buys (SM-R8: nothing, at the pure-retrieval layer it
   measures; SM-R5: bounded growth + reduced staleness, at the memory-tier layer). Added
   `DECISION.md` §3.6 with the reconciliation, plus two flagged-not-resolved loose ends
   (SM-E7 vs SM-E4/SM-E9 epic-naming mismatch; LongMemEval vs supersede-driven recency
   semantics don't fully match).

**Tooling note:** `gh` CLI was installed (no Homebrew — downloaded the release zip
directly to `~/.local/bin`) and authenticated by the user to pull the actual PR review
JSON (inline comments + top-level review) via `gh api`, replacing an earlier turn's
paraphrase-only response.

---

### Phase 4 — PR #16 review: SM-R4 embedder correction + re-validation

**Status:** PASS (code fixed; 3 of 4 datasets re-measured on the corrected embedder as of
Phase 5's LongMemEval extension)

**Trigger:** PR review flagged that this task defaulted to a placeholder embedder
(`all-MiniLM-L6-v2` / `text-embedding-3-small`) instead of SM-R4's ratified choice
(`bge-base-en-v1.5` Mode A / `text-embedding-3-large`@1024 Mode B) — this branch forked
before SM-R4 merged into main.

**Fixed:** `run.py::build_embedder` defaults now match SM-R4 §1; `data.py`/`embcache.py`
also had a hardcoded fallback cache path from a different machine's scratchpad (unrelated
portability bug found while in this code), replaced with a `tempfile`-based default.

**Re-measured (real run, `.venvs/smr8`, `BAAI/bge-base-en-v1.5`, CPU):** options a/b/c on
scifact + nfcorpus (`results_20260728T084006Z_smR4EmbedderVerify.json` + `summary_...md`)
and longmemeval (`results_20260728T090818Z_smR4EmbedderVerifyLME.json`, Phase 5). Confirms
weighted-fusion-beats-RRF holds on the correct embedder on all 3 (p<0.01); the
RRF/weighted edge over vector-only narrows to a **tie** on scifact/nfcorpus but **stays
significant on longmemeval** — see DECISION.md §3.4.

**Not re-run:** fiqa, options d/e — out of scope for this correction (see §3.4 for why);
flagged as follow-up before ratification.

**Side note:** macOS Accelerate BLAS emits spurious divide-by-zero/overflow/invalid
RuntimeWarnings on the dense-retriever matmul; verified the actual output has no NaN/Inf
(checked cached embedding matrices + raw matmul output directly) before trusting the
re-measured numbers above. Suppressed in `retrievers.py` since it's cosmetic, not a
correctness issue.

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
