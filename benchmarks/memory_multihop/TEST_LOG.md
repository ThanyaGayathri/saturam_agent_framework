# TEST_LOG — SM-R9 multi-hop / HippoRAG PPR spike

Environment: dedicated venv `.venvs/multihop` (py3.11), egpt (editable) + datasets +
networkx + numpy + scipy + openai. LLM = Azure OpenAI Responses (`gpt-5.4`). All LLM/embed
calls cached under `results/` (git-ignored).

**Embeddings:** the original runs below used Azure `text-embedding-3-small` — **neither of
SM-R4's ratified picks**, because this branch forked before SM-R4 merged. Fixed in the PR-review
pass: the harness now defaults to SM-R4's ratified spec (`bge-base-en-v1.5`@768 Mode A /
`text-embedding-3-large`@1024 Mode B), and **all three methods were re-run** — see the final
entries, which **change the headline** (the pure-PPR-vs-hybrid ranking flips). Entries dated
before that fix should be read as `text-embedding-3-small` results.

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

**Superseded:** the `hippo` / `hybrid` legs were blocked at the time (no credentials on this
host). They have since been run — see the full re-run entry below, which changes the headline.

---

### kg_quality_run.py --self-test — KG-quality scorer

**Status:** PASS (scorer verified; real corpus numbers now measured — see below)

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
edge, grounding 3/3, full_chain_recall 0.0 (chain correctly detected as broken).

---

### smoke.py — re-verified after the embedder change

**Status:** PASS

**Description:** Re-ran the Phase-0 smoke. Its credential check was wrong for the new default:
SM-R4 Mode A needs no credentials, so a key-presence test reported `embed=False` for a perfectly
working embedder. Now reports the resolved embedder spec and a real readiness flag.

**Result:** SMOKE OK — `embedder=local:BAAI/bge-base-en-v1.5@768 ready=True` with no credentials
present, and it now points at the credential-free `--retrieval-only` path.

---

### run.py --tag smR4ModeA_full30 / _tuned — FULL re-run on SM-R4's embedder

**Status:** PASS — **changes the headline**

**Description:** All three methods re-run end to end on SM-R4 Mode A (`bge-base-en-v1.5`@768)
with live Azure `gpt-5.4` for OpenIE + query NER + reader. Run twice: once at the inherited
`link_threshold=0.5`, once at a re-tuned 0.65. One-time OpenIE index: 285 calls, 106,948
tokens, **$0.62**, 3,413 entities / 7,368 edges, 0 parse failures.

**Result:** **the pure-PPR-vs-hybrid ranking flipped.**

| config | vector | hippo | hybrid |
| --- | --- | --- | --- |
| original (3-small, link 0.5) — recall@5 | 0.750 | **0.908** | 0.892 |
| SM-R4 bge-base, link 0.5 — recall@5 | 0.742 | **0.633** | 0.817 |
| SM-R4 bge-base, link 0.65 — recall@5 | 0.742 | 0.817 | **0.883** |

The dense leg is flat (0.750→0.742, all-hops identical), so the review's "weak dense baseline"
concern is retired — but its predicted *outcome* (a flip) is confirmed via the opposite
mechanism: **pure PPR is embedder-fragile, hybrid is robust**. Recommended config is now hybrid
on measurement, not caution. Details: `results/summary_smR4ModeA.md`.

**Caveats:** EM/F1 are not comparable to the original run (LLM cache was git-ignored, so reader
calls were re-issued; `vector` EM drifted 0.567→0.467 on near-identical retrieval). One reader
call per run is refused by the Azure content filter (a 2Wiki passage, `sexual: medium`); it
lands in `hybrid`, scores as no-answer, and is reported as `reader_content_filtered`, so
hybrid's EM/F1 are a lower bound by up to 1/30.

---

### tau_sweep.py — threshold portability + LOOCV

**Status:** PASS

**Description:** Sweeps `synonym_tau` (6 values) and `link_threshold` (11 values) over the real
KG, plus leave-one-out threshold selection. Costs no LLM spend once the cache is warm.

**Result:** **`link_threshold` is the load-bearing knob and 0.5 is badly miscalibrated for
bge-base**; `synonym_tau` does nothing. Entity-entity cosine distribution over 3,413 real
entities (5.8M pairs): p50 **0.4115**, p90 0.4944, p99 0.5878 — so a 0.5 cutoff sits near the
*median of the random-pair distribution* and ~5% of all pairs falsely qualify as links,
mis-seeding PPR. Sweep is monotone through that region (recall@5 .633 at 0.3–0.5 → **.817** at
0.65). `synonym_tau` is flat at .633–.650 across 0.70→0.95 while synonymy edges move 5,516→26.

Honest out-of-sample (LOOCV, since 0.65 was picked on the same 30 questions it is scored on):
recall@5 **0.750** (in-sample best 0.817), all-hops **0.500** (in-sample best 0.667). So
re-tuning recovers recall but all-hops barely moves, and pure PPR does not return to its
original 0.767 under any honest procedure.

---

### kg_quality_run.py --tag full30 — real KG-quality numbers

**Status:** PASS — **KG is weaker than `0/285` implied**

**Description:** Gold-chain recall against 2Wiki's `evidences` (72 gold triples / 30 questions)
plus grounding rate, replayed free from the warm OpenIE cache.

**Result:** entity recall **0.958**, **edge recall 0.639**, **full-chain recall 0.367**,
relation-strict triple recall 0.097, grounding rate 0.936. So for **63% of questions the gold
reasoning chain is not walkable at all**, yet hybrid still reaches 0.700 all-hops — meaning
PPR's benefit here comes largely from entity/passage membership structure and dense fusion
rather than from traversing correct relational edges. On clean Wikipedia prose; episodic memory
should be expected to score worse. Raw: `results/kg_quality_20260728T112728Z_full30.json`.

---
### fusion_sweep.py + run.py --fusion weighted — weighted vs RRF for dense+PPR

**Status:** PASS — closes the review's "weighted fusion never measured" gap

**Description:** SM-R8 ratified weighted / relative-score fusion and declined to adopt RRF
automatically once a graph leg joins; SM-R9 had only ever run RRF. `harness/fusion.py` is now
**ported from SM-R8's own `fusion.py`** (not reimplemented) so both are measured with identical
code. Full run at SM-R8's ratified `w=0.5`, plus a sweep of `vector_weight` 0.0→1.0 with a
paired bootstrap (10k resamples, SM-R8's test) and LOOCV weight selection. Retrieval-only sweep
replays from cache, so no LLM spend.

**Result:** **SM-R8's result does not transfer to this leg pair.** RRF's point estimate is above
every weighted setting (RRF .883/.700 vs best weighted .817/.633, both at **w=0.0** — i.e.
"discard the dense leg"). At SM-R8's ratified w=0.5, weighted hybrid (.800/.567) is worse than
RRF **and worse than not fusing at all** (pure PPR .817/.633).

**But it is a tie, not an RRF win:** paired bootstrap gives +0.067 recall@5 (CI [−0.033, +0.175],
p=0.26) and +0.067 all-hops (CI [−0.100, +0.267], p=0.58). n=30 cannot resolve it. Our CI does
**exclude** SM-R8's −0.076 effect, so the two results are not one underlying effect — SM-R8's
own §3.5 caveat (one dataset, one *synthetic* graph leg) is vindicated.

**A hypothesis discarded en route:** the intuitive explanation — min-max normalization mangling
PPR's skewed stationary probabilities — was measured and is **false** (mean normalized score over
ranks 2–10: dense .193 vs PPR .163, near-identical). The real difference is rank-decay steepness:
RRF at k=60 is nearly flat (rank1/rank10 ratio 1.15, so cross-leg agreement dominates) while
weighted min-max is steep (ratio ~6.7, so one leg's top hit dominates). Consistent with RRF's
margin being largest on all-hops (+0.133) rather than recall (+0.083).

Raw: `results/fusion_sweep_20260728T120251Z_smR4ModeA.json`,
`results/results_20260728T120316Z_smR4ModeA_weighted.json`; write-up: `results/summary_fusion.md`.

---
### run.py --eval eval_set_n120.json — n=120 scale-up (949 passages)

**Status:** PASS — **changes one n=30 verdict**

**Description:** 120-question stratified set, a strict superset of the committed n=30 set so
results stay nested. 949 pooled passages, full OpenIE + reader, SM-R4 Mode A,
`link_threshold=0.65`, RRF. One-time index: 10,742 entities / 25,628 edges / **$2.18**.

**Result:**

| method | recall@5 | all-hops@5 | chaining all-hops |
| --- | --- | --- | --- |
| vector | 0.723 | 0.425 | 0.281 |
| hippo | 0.779 | 0.558 | 0.531 |
| **hybrid** | **0.815** | **0.592** | 0.490 |

Paired tests vs n=30: hybrid-vs-dense all-hops **+0.167, p<0.0001** (discordant **20:0**) —
confirmed and stronger. **hippo-vs-dense all-hops flips to significant** (p=0.226 at n=30 →
**p=0.017** at n=120): a real verdict change from sample size, vindicating the review's
"more data" instinct even though the paired test was the right fix for the headline comparison.
hybrid-vs-hippo remains **not significant** (p=0.51), consistent with the n≈500 power estimate —
so the "ranking flip" claimed earlier is still unestablished after 4× the data.

**Adopt criterion (chaining all-hops gain ≥ 0.15): MET by both configs** — hippo +0.250,
hybrid +0.208, both significant.

**Caution the larger set exposes:** absolute numbers fall across the board (hybrid recall@5
0.883 → 0.815) because the shared pool is 3.3× bigger. The *gaps* replicate; the *levels* do
not. Thresholds must be expressed as gains over dense, never as absolute scores.

**At scale:** 1 OpenIE parse failure and **3 content-filter refusals out of 949 passages**
(0.3%) — silent KG holes the 285-passage run never surfaced, and which crashed the first attempt
before `openie.py` was hardened. Now reported as `openie_content_filtered`.

---
