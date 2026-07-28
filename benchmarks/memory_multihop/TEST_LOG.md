# TEST_LOG — SM-R9 multi-hop / HippoRAG PPR spike

Environment: dedicated venv `.venvs/multihop` (py3.11), egpt (editable) + datasets +
networkx + numpy + scipy + openai. LLM = Azure OpenAI Responses (`gpt-5.4`). All LLM/embed
calls cached under `results/` (git-ignored).

**Embeddings:** the original runs below used Azure `text-embedding-3-small` — **neither of
SM-R4's ratified picks**, because this branch forked before SM-R4 merged. Fixed in the PR-review
pass: the harness now defaults to SM-R4's ratified spec (`bge-base-en-v1.5`@768 Mode A /
`text-embedding-3-large`@1024 Mode B). The dense leg was re-measured — see the final entry.
Entries dated before that fix should be read as `text-embedding-3-small` results.

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

**Result:** PPR beats the dense baseline on every metric. recall@5 0.750→0.892 hybrid / 0.908
pure PPR (CIs disjoint), all-hops-hit@5 0.467→0.700/0.767, chaining-only all-hops
0.333→0.625/0.708, QA F1 0.583→0.656/0.731. One-time index cost: 285 OpenIE calls, 107,637
tokens, $0.63; KG 3,393 entities / 7,199 edges. PPR compute <100ms (hybrid retrieval 0.056s
mean). Full table: `results/summary_full30.md`; raw:
`results/results_20260723T075917Z_full30.json` (+ `detail_*`).

**Caveats added in PR review (do not read this row as settled):** (a) embeddings here are
`text-embedding-3-small`, not SM-R4's ratified pick — dense leg re-measured below; (b) the
`all-hops-hit` CIs **overlap** at n=30, so thresholds keyed on that metric are not ratifiable
without n≥100 or a power analysis; (c) `hybrid` uses RRF, which SM-R8's evidence argues against
for a PPR leg — weighted fusion was never measured; (d) "0/285 OpenIE failures" is JSON-parse
success, **not** triple quality.

---

### Reproducibility check — offline replay against warm cache

**Status:** PASS

**Description:** Re-ran `run.py` with `HARNESS_LLM_MODE=replay HARNESS_EMBED_MODE=replay` (no
network) to confirm the committed run is deterministic and reproducible from the cache.

**Result:** Identical metrics to the live run (see below).

---

### run.py --retrieval-only --tag smR4ModeA — dense baseline on SM-R4's ratified embedder

**Status:** PASS

**Description:** Addresses the PR #15 review finding that the dense baseline ran on Azure
`text-embedding-3-small`, which is neither of SM-R4's ratified picks. Re-ran the `vector` leg
on **SM-R4 Mode A (`BAAI/bge-base-en-v1.5`@768, sentence-transformers)**, same eval set, same
k=5, same bootstrap procedure. Used the new `--retrieval-only` flag: retrieval metrics score
against the dataset's gold titles and need no LLM, so this leg runs credential-free.

**Result:** **The review's hypothesis did not hold for the dense leg.** all-hops-hit@5 is
**identical** (0.467 overall, 0.333 chaining-only); recall@5 moves 0.750→0.742 overall and
0.688→0.677 chaining-only, both well inside the CI. Consistent with SM-R4's own eval, where
bge-base (0.911) and `text-embedding-3-small` (0.898) are a statistical tie on the memory set.
Raw: `results/results_20260728T104020Z_smR4ModeA.json`; write-up: `results/summary_smR4ModeA.md`.

Two confounds checked before trusting this:
- **BGE query instruction** — adding the standard `"Represent this sentence for searching
  relevant passages: "` prefix makes results *worse* (recall@5 0.725, all-hops 0.433), so the
  reported numbers are the favourable config for dense, not a handicap.
- **macOS Accelerate BLAS RuntimeWarnings** (divide/overflow/invalid) from the cosine matmul —
  verified benign: all inputs/outputs finite, row norms exactly 1.0, BLAS matches an independent
  `np.einsum` path to **1.8e-15**. Now suppressed narrowly with an `isfinite` assert behind it.

**Not re-run (blocked, not skipped):** `hippo` / `hybrid` / EM / F1 and the OpenIE triple-quality
measurement all need live LLM calls — no credentials on this host, and `results/llm_cache/` is
git-ignored so the original extraction cannot be replayed. The dense-vs-PPR comparison is
therefore **asymmetric** and the PPR margin is not yet fully re-validated.

---

### kg_quality_run.py --self-test — KG-quality scorer

**Status:** PASS (scorer verified; real corpus numbers OUTSTANDING)

**Description:** New `harness/kg_quality.py` measures whether OpenIE actually recovers 2Wiki's
gold `evidences` chain (72 gold triples across the 30 questions) at three nested levels —
**entity** (both endpoints present), **edge** (endpoints directly connected — the level PPR
actually consumes, since `kg.py` discards relation labels), and **triple** (relation-strict) —
plus a **grounding rate** as a faithfulness proxy. Precision against `evidences` is deliberately
not reported: 2Wiki's gold covers only the chain, so scoring other extracted triples as false
positives would measure gold sparsity rather than extraction error.

The `--self-test` mode validates the scorer on a hand-built fixture with no LLM and no network:
a 2-hop gold chain where extraction recovers hop 1 under a longer surface form ("Queen
Ealhswith" for gold "Ealhswith") and recovers hop 2's entities but not the edge between them.

**Result:** SELF-TEST OK — entity_recall 1.0, edge_recall 0.5, triple_recall 0.5, 1 missing
edge, grounding 3/3, full_chain_recall 0.0 (chain correctly detected as broken). Real numbers
against the 285-passage corpus need an OpenIE pass (credentials); flagged as outstanding before
ratification rather than silently skipped.

---

### smoke.py — re-verified after the embedder change

**Status:** PASS

**Description:** Re-ran the Phase-0 smoke. Its credential check was wrong for the new default:
SM-R4 Mode A needs no credentials, so a key-presence test reported `embed=False` for a perfectly
working embedder. Now reports the resolved embedder spec and a real readiness flag.

**Result:** SMOKE OK — `embedder=local:BAAI/bge-base-en-v1.5@768 ready=True` with no credentials
present, and it now points at the credential-free `--retrieval-only` path.

---
