# SM-R9 — full re-run on SM-R4's ratified embedder

Addresses the PR #15 review finding that the original run used Azure `text-embedding-3-small`,
which is **neither** of SM-R4's ratified picks. All three methods re-run end to end.

- **Embedder:** `local:BAAI/bge-base-en-v1.5@768` — SM-R4 **Mode A**
  (`benchmarks/memory_embedding/DECISION.md` §1).
- **LLM:** Azure OpenAI Responses, `gpt-5.4` (OpenIE + query NER + reader).
- Same eval set, same k=5, same 285-passage corpus, same bootstrap CI procedure.
- Sources: `results_20260728T112239Z_smR4ModeA_full30.json` (inherited thresholds),
  `results_20260728T112549Z_smR4ModeA_tuned.json` (re-tuned), `tau_sweep_*_smR4ModeA.json`,
  `kg_quality_*_full30.json`.

## Headline: pure PPR is embedder-fragile (the point estimates invert; the *difference* is n.s.)

| config / method | recall@5 | all-hops@5 | EM | F1 | chaining all-hops | chaining F1 |
| --- | --- | --- | --- | --- | --- | --- |
| **original** — 3-small, link=0.5 | | | | | | |
| vector | 0.750 | 0.467 | 0.567 | 0.583 | 0.333 | 0.479 |
| hippo | **0.908** | **0.767** | 0.700 | 0.731 | **0.708** | 0.663 |
| hybrid | 0.892 | 0.700 | 0.633 | 0.656 | 0.625 | 0.569 |
| **SM-R4 bge-base, link=0.5** (inherited) | | | | | | |
| vector | 0.742 | 0.467 | 0.467 | 0.489 | 0.333 | 0.361 |
| hippo | **0.633** | **0.467** | 0.500 | 0.555 | 0.417 | 0.486 |
| hybrid | 0.817 | 0.600 | 0.700 | 0.700 | 0.500 | 0.667 |
| **SM-R4 bge-base, link=0.65** (re-tuned) | | | | | | |
| vector | 0.742 | 0.467 | 0.467 | 0.489 | 0.333 | 0.361 |
| hippo | 0.817 | 0.633 | 0.500 | 0.574 | 0.625 | 0.509 |
| **hybrid** | **0.883** | **0.700** | 0.667 | 0.719 | 0.625 | 0.708 |

Three findings, in order of consequence:

### 1. Pure PPR is **fragile** to the embedder; hybrid is **robust**

Swapping to SM-R4's ratified embedder at the *inherited* thresholds collapses pure PPR:
recall@5 **0.908 → 0.633**, chaining all-hops **0.708 → 0.417**. Hybrid barely moves
(0.892 → 0.817, and 0.883 once re-tuned; all-hops 0.700 in both the original and tuned runs).

**The dense leg is not the cause** — it is flat (0.750 → 0.742, all-hops identical at 0.467).
So the review's predicted *direction* is borne out, but by the opposite mechanism to the one
proposed: dense did not get stronger, **PPR got weaker**. Note the *ordering* change is not
itself significant — hybrid-vs-hippo is n.s. at n=30 (p=0.59) and n=120 (p=0.51). **What is
significant is PPR's collapse** (p=0.0001). An earlier version of this doc called this a
"ranking flip"; that overstated it.

**Consequence for the decision:** ship `hybrid` **for robustness** — pure PPR's advantage did
not survive an embedder change, and that fragility *is* significant. This is **not** a claim
that hybrid retrieves better than pure PPR; that comparison is n.s. at both n=30 and n=120.

### 2. Root cause: `link_threshold` is embedder-specific and 0.5 is badly miscalibrated

The query-entity → KG-node cutoff is an **absolute cosine**. Measured entity–entity cosine
distribution under bge-base (3,413 real KG entities, 5.8M pairs):

| p50 | p90 | p99 | p99.9 | max |
| --- | --- | --- | --- | --- |
| 0.4115 | 0.4944 | 0.5878 | 0.6984 | 1.0 |

A 0.5 cutoff sits near the **median** of the *random-pair* distribution, so roughly 5% of all
5.8M pairs qualify as "links" — PPR gets seeded on spurious entities and diffuses its mass.
The sweep is monotone through that region:

| link_threshold | 0.30 | 0.40 | 0.50 | 0.60 | **0.65** | 0.70 | 0.75 | 0.80 | 0.85 | 0.90 | 0.95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recall@5 | .633 | .633 | .633 | .733 | **.817** | .808 | .783 | .792 | .800 | .783 | .750 |
| all-hops@5 | .467 | .467 | .467 | .533 | .633 | **.667** | .633 | .633 | .667 | .667 | .633 |

**`synonym_tau`, by contrast, does not matter here at all** — flat at .633–.650 recall across
0.70→0.95 even as the synonymy-edge count moves 5,516 → 26. Only `link_threshold` is
load-bearing, which is not what the original doc's flag design implied.

### 3. Honest out-of-sample: re-tuning recovers *some*, not all, of the loss

`link_threshold=0.65` was selected on the same 30 questions it is scored on, so the tuned row
is in-sample and optimistic. Leave-one-out selection (pick the threshold on the other 29,
score the held-out one):

| metric | inherited (0.5) | in-sample best | **LOOCV (honest)** |
| --- | --- | --- | --- |
| recall@5 | 0.633 | 0.817 (@0.65) | **0.750** |
| all-hops@5 | 0.467 | 0.667 (@0.70) | **0.500** |

So re-tuning genuinely recovers recall (+0.117 out-of-sample) but **all-hops barely moves
out-of-sample (0.467 → 0.500)**. Pure PPR does not return to its original 0.767 under any
honest procedure. LOOCV also selected several different thresholds across folds (0.65/0.70/
0.85/0.90), i.e. the optimum is not sharply identified at n=30.

## KG quality — measured, and weaker than `0/285` implied

`0/285 extraction failures` was JSON-parse success. Measured against 2Wiki's gold `evidences`
(72 gold triples over 30 questions), at the granularity PPR actually consumes:

| metric | value | reading |
| --- | --- | --- |
| entity recall | **0.958** | OpenIE almost always finds both endpoints of a gold triple |
| **edge recall** | **0.639** | but only 64% of gold chain edges exist in the KG |
| **full-chain recall** | **0.367** | **only 37% of questions have their complete gold chain walkable** |
| triple recall (relation-strict) | 0.097 | relation phrasing is free-form; near-useless as a metric |
| grounding rate | 0.936 | 6.4% of extracted triples have an endpoint not in the source text |

**This reframes what PPR is doing.** For 63% of questions the gold reasoning chain is not
traversable at all, yet hybrid still reaches 0.700 all-hops — so the measured benefit comes
largely from entity/passage **membership** structure and dense fusion, **not** from walking
correct relational edges. SM-H4 should not assume the relational layer is carrying the result.

This is clean Wikipedia prose. Episodic memory has since been **measured** (LongMemEval-S, see
`summary_measurements.md` §4) and the expectation was only half right: OpenIE does **not**
hallucinate more on dialogue (grounding 0.964 vs 0.956), but fact **density** collapses (7.56 vs
17.82 triples/100 words, ~2.4× the cost per useful triple) and **coreference** is ~3× worse.

## Caveats

- **EM/F1 are not comparable across the original and these runs.** The original LLM cache was
  git-ignored, so reader calls were re-issued; `vector`'s EM moved 0.567 → 0.467 while its
  retrieval barely changed, which is reader drift, not retrieval. **Retrieval metrics
  (recall@5, all-hops) are the sound comparison** — they are scored against dataset gold labels
  and are LLM-independent for the dense leg.
- **One reader call per run is refused by the Azure content filter** (`sexual: medium`, on a
  2Wiki passage retrieved for *"Where does the director of film Man At Bath work at?"*). It
  lands in `hybrid` in both runs, scores as no-answer, and is reported as
  `reader_content_filtered`. **Hybrid's EM/F1 are therefore a lower bound** by up to 1/30.
- n=30 throughout this document. **Superseded by the n=120 run** (`summary_measurements.md` §5),
  where the adopt criterion is met and significant for both configs. Note absolute levels fall on
  the larger corpus while the *gaps* replicate.
- `--link-threshold` now defaults to 0.5 for backwards-comparability, **not** because 0.5 is
  right. Anything shipping on bge-base must set it explicitly.

## Reproduce

```bash
cd benchmarks/memory_multihop
PYTHONPATH=. python run.py --methods vector hippo hybrid --k 5 --link-threshold 0.65 --tag tuned
PYTHONPATH=. python tau_sweep.py --tag smR4ModeA     # sweep + LOOCV, no LLM spend once cached
PYTHONPATH=. python kg_quality_run.py --tag full30   # gold-chain recall + grounding
```

One-time OpenIE index: 285 calls, 106,948 tokens, **$0.62**, 3,413 entities / 7,368 edges,
0 parse failures. Everything after that replays free from `results/llm_cache/`.
