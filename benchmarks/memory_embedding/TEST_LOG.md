# TEST_LOG — SM-R4 memory-embedding benchmark (v2 rigorous re-run)

Environment: macOS x86_64, Python 3.12 (`.venvs/mememb`), CPU (device pinned).
egpt imported from source (`PYTHONPATH=libs/agno`); **no core code committed**
(`git diff main -- libs/` empty, verified twice).

Note: the shared host was under heavy external load during the runs (load avg
~15–83 on 16 cores; a VM consuming ~5 cores). This drove three deliberate,
documented adaptations: batched corpus embedding, CPU pinning, and capped BEIR
slices. See below.

---

### data/generate_synthetic_v2.py + data/label_relevance.py

**Status:** PASS

**Description:** Build the independent memory set — LLM (Azure gpt-5.4) authors
the corpus and queries in separate passes; a separate **blind** judge pass grades
relevance (candidate ids anonymized/shuffled so it can't copy the generator).

**Result:** 225 memories / 180 queries / 15 diverse personas (no clustering after
diversity fix). Graded qrels: avg 2.49 positives/query, 0 empty; grades 179×2,
270×1. Generator/judge agreement on intended memories **174/176 (99%)** —
independent confirmation the labels are sound and not circular.

---

### metrics + stats sanity (harness/metrics.py, harness/stats.py)

**Status:** PASS (bug caught by the run-twice rule)

**Description:** Graded nDCG/recall/MRR against a hand-computed example; bootstrap
CI + paired significance against known cases. Run twice.

**Result:** Metrics match hand calc (nDCG@10 0.950234). First run flagged a stats
bug — two identical models reported "significant"; fixed (two-sided p used
`1 - prop_le0`, mishandling bootstrap mass at 0). Re-verified: identical→tie,
clear diff→significant, near-tie→tie. Both runs agree.

---

### harness.run --dataset synthetic_v2 --mode A (CPU)

**Status:** PASS

**Description:** 7 self-hostable models, graded quality + CPU latency + sync/async
parity + storage. Corpus batched (verified batched == per-item, identical nDCG).

**Result (nDCG@10, 95% CI):** bge-large 0.856, gte-base 0.850, bge-base 0.842,
bge-small(onnx/st) 0.825, e5-base 0.823, minilm 0.804. **gte-base/bge-base (768)
significantly beat bge-small (384): p=0.037.** CPU p50: minilm 10ms, bge-small-onnx
11ms, gte-base 49ms, e5 52ms, bge-base 60ms, bge-large 187ms. Parity ≤ 3e-7.
Results: `results/results_v2_synthetic_v2.json`.

---

### harness.run --dataset synthetic_v2 --mode B (Azure)

**Status:** PASS

**Description:** text-embedding-3-large/-small (full + Matryoshka-truncated) via
`AzureOpenAIEmbedder`. Key from env only (never committed).

**Result (nDCG@10):** 3-large@3072 0.882, 3-large@1024 0.877, 3-small@1536 0.869,
3-small@512 0.849 — all beat the open models. 1024 is a statistical tie with the
full 3072. Latency is WAN round-trip (~1.3–1.7s), not compute. Parity ≤ 4e-3
(provider embeddings not bitwise-deterministic). Results:
`results/results_v2_synthetic_v2_modeB.json`.

---

### harness.run --dataset beir:scifact (external cross-check)

**Status:** PASS (capped slice due to host load)

**Description:** Established human-labeled IR set; ranking cross-check. Ran as a
capped 1007-doc slice, quality-only (host CPU-saturated by other work; full
reproduces with `--dataset beir:scifact` when the host is free).

**Result:** Independently confirms 768>384 — **bge-base 0.846 > bge-small 0.818,
p=0.0096**; gte-base tops at 0.865, gte-base > bge-base p=0.045. Rank agreement
with the memory set (Spearman, open models) = 0.54. Results:
`results/results_v2_beir_scifact.json`.

---

### Host-load adaptations (documented, not silently absorbed)

**Status:** PASS

1. **Batched corpus embedding** — per-item embedding of BEIR corpora projected
   ~10h; added a batched fast-path (single-query latency still per-item).
   Verified batched == per-item (identical nDCG). ~6× faster even under load.
2. **CPU pinned** — SentenceTransformer was auto-selecting Mac MPS (GPU), which
   OOM'd under memory pressure and gave non-representative latency. Pinned to CPU
   (the deployment target) at the harness level.
3. **Capped BEIR** — full ArguAna/NFCorpus were not tractable on the loaded host;
   used capped slices for the ranking cross-check. Full is one flag away.

---

### ./scripts/format.sh and ./scripts/validate.sh

**Status:** see the commit-time run recorded below; in-scope `benchmarks/` files
checked directly with the repo-pinned `ruff==0.15.20` + `mypy` (clean). Core
untouched (`git diff main -- libs/` empty). The `agno` mypy step reports 3
pre-existing errors in `knowledge/reranker/sentence_transformer.py` — a core file
not touched by this task (see prior log / findings); not introduced here.
