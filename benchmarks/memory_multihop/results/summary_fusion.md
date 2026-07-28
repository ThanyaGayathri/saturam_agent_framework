# SM-R9 — weighted vs RRF fusion for the dense+PPR leg pair

Closes the gap PR #15's review flagged: SM-R8 ratified **weighted / relative-score** fusion and
declined to adopt RRF automatically once a graph leg joins, but SM-R9 only ever ran RRF. Both
are now measured with the *same* fusion code — `harness/fusion.py` is ported from SM-R8's
`benchmarks/memory_retrieval/fusion.py` rather than reimplemented, so the comparison is against
SM-R8's actual method.

Config: SM-R4 Mode A (`bge-base-en-v1.5`@768), `link_threshold=0.65`, k=5, n=30.
Sources: `fusion_sweep_20260728T120251Z_smR4ModeA.json`,
`results_20260728T120316Z_smR4ModeA_weighted.json`.

## Full comparison — weighted at SM-R8's ratified w=0.5

| method | recall@5 | all-hops@5 | EM | F1 | chaining all-hops / F1 |
| --- | --- | --- | --- | --- | --- |
| vector | 0.742 | 0.467 | 0.467 | 0.489 | 0.333 / 0.361 |
| hippo (pure PPR) | 0.817 | 0.633 | 0.500 | 0.574 | 0.625 / 0.509 |
| hybrid — **weighted (w=0.5)** | 0.800 | 0.567 | 0.467 | 0.475 | 0.458 / 0.385 |
| hybrid — **RRF (k=60)** | **0.883** | **0.700** | **0.667** | **0.719** | **0.625 / 0.708** |

At SM-R8's ratified weight, weighted fusion is worse than RRF on every metric **and worse than
not fusing at all** (pure PPR beats it on all-hops, 0.633 vs 0.567).

Note the EM/F1 comparison actually *favours* weighted and it still loses: the RRF run has one
reader call refused by the Azure content filter (scored as no-answer), the weighted run has
none, because the two configs retrieve different passages for that question.

## Weight sweep — RRF beats every weighted setting

| vector_weight | 0.0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1.0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recall@5 | **.817** | .808 | .800 | .767 | .767 | .800 | .783 | .767 | .767 | .733 | .742 |
| all-hops@5 | **.633** | .600 | .600 | .533 | .533 | .567 | .533 | .500 | .500 | .433 | .467 |

*RRF: recall@5 **.883**, all-hops **.700** — above every row.*

**Weighted's in-sample optimum is degenerate: w=0.0**, i.e. "ignore the dense leg entirely",
which is just pure PPR. Within SM-R8's ratified 0.3–0.7 band it is worse still. So on this leg
pair, weighted fusion does not find a useful blend — its best answer is not to blend.

## But the difference is **not statistically significant** at n=30

Paired bootstrap (10k resamples, same test SM-R8 used), RRF minus best-weighted:

| metric | best weighted | mean diff | 95% CI | p | significant |
| --- | --- | --- | --- | --- | --- |
| recall@5 | w=0.0 | **+0.067** | [−0.033, +0.175] | 0.255 | **no** |
| all-hops@5 | w=0.0 | **+0.067** | [−0.100, +0.267] | 0.583 | **no** |

**So the honest claim is a tie, not an RRF win.** What can be said:

1. RRF's point estimate is higher at *every* weight, and weighted never exceeds it — consistent
   but underpowered.
2. **SM-R9 does not reproduce SM-R8's result.** SM-R8 measured weighted beating RRF by
   **+0.076 (p=0.0001)** in its 3-leg experiment with a PPR leg. In this sign convention that
   is −0.076, which falls **outside** our 95% CI of [−0.033, +0.175]. So this leg pair is
   inconsistent with an effect of SM-R8's magnitude in weighted's favour — even though we
   cannot resolve the direction ourselves.
3. Out-of-sample (LOOCV over the weight): weighted recall@5 **0.783**, all-hops **0.633** —
   still below RRF's 0.883/0.700, and the LOOCV folds nearly all select w=0.0.

## Why — the mechanism, after discarding a wrong hypothesis

The obvious explanation is that min-max normalization mangles the PPR leg, because PPR emits
extremely skewed stationary probabilities while dense emits flat cosines. **Measured, and that
is false.** Mean min-max-normalized score by rank (30 questions, pool of 10):

| rank | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dense | 1.000 | .571 | .365 | .263 | .196 | .142 | .096 | .065 | .036 | .000 |
| PPR | 1.000 | .570 | .333 | .217 | .101 | .071 | .057 | .051 | .036 | .033 |

The two are nearly identical after normalization (mean over ranks 2–10: dense .193, PPR .163).
Min-max is doing its job; leg-shape mismatch is **not** the cause.

The actual difference is **how steeply each method discounts rank**:

- **RRF is nearly flat.** With k=60, rank 1 scores 1/61 = .0164 and rank 10 scores 1/70 = .0143
  — a ratio of just **1.15**. An item's rank barely matters; what dominates is *whether it
  appears in both legs' pools at all*, since presence in both roughly doubles its score.
- **Weighted min-max is steep.** Rank 1 = 1.00, rank 5 ≈ .15 — a ratio of about **6.7**. One
  leg's top hit (0.5 after weighting) outscores an item sitting at rank 4–5 in *both* legs
  (≈0.24).

So **RRF rewards cross-leg agreement; weighted rewards single-leg confidence.** For
`all-hops-hit` — which requires *both* gold passages in the top-5, and where the second bridge
passage is typically mid-ranked in both legs rather than top in either — rewarding agreement is
exactly the right bias. That matches where RRF's margin is largest (all-hops +0.133 at w=0.5,
vs recall +0.083).

This is a hypothesis consistent with the measurements, not a proven mechanism; it would need an
ablation on `rrf_k` (which controls RRF's flatness) to confirm.

## What this means for the decision

- **Keep RRF as SM-R9's recommended fusion**, but on the basis that weighted showed no
  advantage here — not on a demonstrated RRF win, which the data does not support.
- **SM-R8's "weighted beats RRF" does not generalise to this leg pair.** SM-R8's own §3.5
  caveat — one dataset, one *synthetic* graph leg (k-NN over dense embeddings) — is vindicated:
  its graph leg is not this graph leg, and its result did not transfer. SM-R8 §1.1's
  instruction to "measure weighted-vs-RRF against the real graph retriever first" was the right
  call, and this is that measurement for PPR-over-an-OpenIE-KG.
- **Neither method is ratifiable from n=30.** Both this result and SM-R8's should be re-tested
  on SM-G9's production graph retriever before SM-H4 commits.
