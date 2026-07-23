# TEST_LOG — SM-R9 multi-hop / HippoRAG PPR spike

Environment: dedicated venv `.venvs/multihop` (py3.11), egpt (editable) + datasets +
networkx + numpy + scipy + openai. LLM = Azure OpenAI Responses (`gpt-5.4`), embeddings =
Azure `text-embedding-3-small`. All LLM/embed calls cached under `results/` (git-ignored).

### harness/dataset.py — eval-set builder

**Status:** PASS

**Description:** Builds a seeded, stratified subset of `voidful/2WikiMultihopQA`
(validation) into a pooled corpus. Caught and fixed a double-JSON-encoding bug (passage
text arrived char-joined, titles carried literal `\uXXXX`); after the fix, decoded text is
clean and gold-title integrity is 0/30 missing.

**Result:** `data/eval_set.json` — 30 questions (12 compositional / 6 inference / 6
bridge_comparison / 6 comparison), 285 pooled passages, gold-title integrity 0 missing.

---

### smoke.py — Phase-0 skeleton check

**Status:** PASS

**Description:** No-cost checks — eval set loads + is consistent, metrics compute on a toy
example, LLM/embedder construct from config. Also verified the real end-to-end path with a
1-call OpenIE + embedding smoke (valid triples returned, 1536-dim vectors, cost accounted).

**Result:** SMOKE OK.

---

### run.py — dev smoke (2 questions, 20-passage corpus, all 3 methods)

**Status:** PASS

**Description:** Full mechanism end-to-end (index → retrieve → answer → score) for vector,
hippo, and hybrid. Caught and fixed a HippoRAG-index crash from malformed OpenIE JSON
(findings/F1) and an entity-embedding perf problem (findings/F2, per-call → batched).

**Result:** All three methods run to completion, 0 OpenIE failures after the fix; the tiny
corpus is too easy to discriminate (all 1.0) — its purpose was mechanism validation only.

---

### run.py --tag full30 — full run (30 questions, 285-passage corpus, k=5)

**Status:** PASS

**Description:** vector / hippo / hybrid over the full pooled corpus. Measures recall@5,
all-hops-hit@5, EM, F1 (overall + chaining-only), with index cost + query latency. Exit code 0,
0/285 OpenIE failures.

**Result:** HippoRAG PPR wins decisively. recall@5 0.750→0.908 (CIs disjoint), all-hops-hit@5
0.467→0.767, chaining-only all-hops 0.333→0.708 (more than doubled), QA F1 0.583→0.731. Hybrid
(RRF) between the two. One-time index cost: 285 OpenIE calls, 107,637 tokens, $0.63; KG 3,393
entities / 7,199 edges. PPR compute <100ms (hybrid retrieval 0.056s mean). Full table:
`results/summary_full30.md`; raw: `results/results_20260723T075917Z_full30.json` (+ `detail_*`).

---

### Reproducibility check — offline replay against warm cache

**Status:** PASS

**Description:** Re-ran `run.py` with `HARNESS_LLM_MODE=replay HARNESS_EMBED_MODE=replay` (no
network) to confirm the committed run is deterministic and reproducible from the cache.

**Result:** Identical metrics to the live run (see below).

---
