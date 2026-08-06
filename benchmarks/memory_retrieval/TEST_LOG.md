# SM-R8 — TEST_LOG

Run log for the retrieval fusion + reranker decision benchmark. Newest first.

---

### Phase 7 — second-pass self-review: verify the verifier + check what's changed

**Status:** PASS — Phase 6's own adversarial control had a real, undisclosed gap; found,
measured, and closed. Cross-PR state re-checked from source, not assumed from PR status.

**Trigger:** a follow-up review asked to verify Phase 6's verification itself (not repeat
it), specifically: is `LexicalGraphRetriever` actually decorrelated, is the overlap
diagnostic measuring the right thing, is §3.5 honest about the effect-size drop, and has
SM-R9 (flagged stale in Phase 6) since changed in ways SM-R8 should know about.

1. **`_diagnose_overlap` measures top-k *result* overlap (post-PPR), not graph edge/
   adjacency overlap** — confirmed by reading the function directly. This is arguably the
   more relevant metric for the stated concern (it captures both shared graph structure
   *and* shared seeding), but its scope was incomplete: it only ever checked overlap vs
   `dense`, never vs `bm25`.
2. **Gap found: `LexicalGraphRetriever` decorrelates from dense but correlates with bm25.**
   It's seeded from bm25's own top hits and built on TF-IDF (a lexical signal close to
   BM25) — measured: top-10 overlap with bm25 is **0.52**, higher than its 0.29 overlap
   with dense (`verify_decorrelation.py`, reproduced by the official run — see updated
   `graph_leg_experiment.json`). A separate, non-query-dependent check (raw k-NN edge-set
   Jaccard between the two graph *structures*) is 0.22 — genuinely different topology — so
   the bm25-correlation comes from the shared seed vector, not the graph edges. Added a
   `bm25`-overlap diagnostic call to `graph_leg_experiment.py` (previously only checked
   `dense`) and disclosed the finding in `DECISION.md` §3.5: the control is honest for the
   *dense*-circularity concern specifically (which is what was asked for), not proof of a
   fully independent third signal.
3. **§3.5's +0.076→+0.060 drop:** confirmed the doc already stated both numbers side by
   side and called the smaller one out explicitly — strengthened to state the ~21%
   reduction as a number, not just "smaller than," so it can't be read as more confirmation
   than it is. The "one dataset, N synthetic graph constructions" caveat was already
   current (says "two", correctly, post-Phase-6), not a stale "one" left over.
4. **SM-R9 (PR #15) re-checked from `origin/feat/memory-SM-R9` directly, not the PR's
   "Ready for re-review" status:** the embedder fix is real and confirmed (`2c15ee9`/
   `a363152`, live SM-R4 Mode A). Two things surfaced that Phase 6 didn't have yet:
   - SM-R9's own consistency audit found this doc's §9 cross-PR list was **incomplete** —
     `HybridRetriever` (dense+PPR) also hardcodes RRF, the same pattern flagged on PR #20,
     which Phase 6 missed on that spike.
   - SM-R9 then **measured weighted-vs-RRF on a real PPR leg** (LLM+OpenIE-built KG) —
     literally the real-graph-retriever measurement §3.5 asked for. Result: RRF numerically
     ahead (not significant, p=0.26), and their CI **excludes** this doc's −0.076 effect —
     an independent real measurement pointing a different numeric direction, neither side
     conclusive. `DECISION.md` §3.5/§9 updated with this cross-validation; a stale "still
     open" comment on PR #15 was followed up with a correction.

**Hygiene re-confirmed:** `git diff origin/main -- libs/` still empty;
`graph_leg_experiment.py` alone is ruff-clean (0 findings); the ~86 ruff findings across
`retrievers.py`/`run.py`/`data.py`/`embcache.py` are all pre-existing `UP*`-style debt
predating every SM-R8 review-response commit (diffed line-by-line against `26c84f6`, the
commit before any of these fixes, to confirm — none are in code this task touched); mypy
remains unavailable on this host (consistent with `format.sh`/`validate.sh` not covering
`benchmarks/` at all).

---

### Phase 6 — verification audit of Phase 5's fixes (adversarial circularity check)

**Status:** PASS — all Phase 5 claims re-verified against source data; one gap found and
closed (adversarial control for the 3-leg graph experiment), everything else confirmed
accurate as written.

**Trigger:** a follow-up review asked to verify (not just re-assert) each Phase 5 fix,
explicitly citing this project's history of benchmarks with baked-in circularity that
only surfaced under adversarial review (SM-R5's importance-oracle confound, SM-R6's
clean-boundary bug) as the reason to check the 3-leg graph experiment specifically.

**What was checked and confirmed by direct inspection (not re-asserted):**
- `run.py::build_embedder` defaults are literally `BAAI/bge-base-en-v1.5` /
  `text-embedding-3-large`@1024 (grep'd the source); the actual result JSONs' recorded
  `embedding_model` field confirms `local:bge-base-en-v1.5` was really used, not a
  near-miss substitute.
- The scifact weighted-vs-RRF delta cited in both the embedder re-run table and the
  3-leg writeup are **bit-for-bit identical floats** (0.7216702635928084 /
  0.7445368854580307 in both JSONs) — genuinely the same underlying measurement, not
  coincidentally close numbers.
- fiqa is flagged as outstanding directly in `DECISION.md` (not only in the PR reply).
- Reranker per-dataset breakdown and the bge-reranker-v2-m3 latency-only flag are present
  in §3.1/§3.1c as written.
- SM-R5's `no_decay` nDCG (0.893→0.903) really is the highest of every strategy in its
  own table, and its own §2.3 really does say `no_decay` significantly beats
  `importance_weighted` on raw recall/nDCG — re-fetched and grep'd the source doc again
  rather than trusting the earlier transcription.
- The SM-E7-vs-SM-E4/SM-E9 open question is still flagged in §3.6/§6, not dropped.

**Gap found and closed — adversarial control for the 3-leg graph experiment:** the
reviewer's concern was real: `GraphRetriever` builds its k-NN graph from dense
embeddings *and* seeds PPR from dense's own top hits, so the +0.076 result could have
been circular (graph leg = smoothed copy of dense, making "weighted absorbs it" trivial).
Added `LexicalGraphRetriever` — same PPR mechanics, but k-NN edges *and* seeds built
entirely from BM25/TF-IDF, zero shared construction path with dense — plus
`_diagnose_overlap` to quantify the actual correlation. Measured: top-10 overlap with
dense is 0.62 for the dense-derived graph vs **0.29** for the lexical one (confirms real
decorrelation, not just a claim). Re-ran the 3-leg comparison with the lexical leg:
weighted still beats RRF by **+0.060** (p=0.0001) — smaller than +0.076 but still far
above the 2-leg baseline (+0.023) and still clearly significant. **The finding survives
decorrelation.** `DECISION.md` §3.5 updated with this control; `graph_leg_experiment.json`
has the full numbers for both graph variants + the overlap diagnostic.

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
