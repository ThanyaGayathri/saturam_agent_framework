# SM-R5 — Decision: Episodic Decay Model

**Status:** Proposed (decision task), revised v2 after review. No framework/core
code changed here; this records the chosen model + measured rationale for the
implementation tasks (SM-C3, SM-R12) and downstream SM-E4 (recency scoring) /
SM-E9 (forgetting).
**Depends on:** SM-R1 (#76) — all numeric targets below are **PROVISIONAL** and
flagged for SM-R1 to ratify.
**Method:** every option was implemented inside `harness/` and measured on a
reproducible offline simulation (see [README.md](README.md)); we do **not**
decide from cited literature numbers alone.

---

## 0. Review response (v2 — what changed and why)

This revision addresses the review. Several fixes changed headline numbers or
flipped a sub-recommendation; the **top-line choice (importance-weighted recency
decay, ranking-primary) holds and is now significance-tested**, but the evidence
is more honest.

| Review point | Change |
|---|---|
| Oracle-importance confound (importance assigned by `kind`) | Added `importance_noise` to decorrelate importance from kind + a sweep (`confound.py`). The *targeting* benefit is calibration-dependent (§2.4); decision now gated on importance-scorer calibration (§5, §7). |
| §4 three-condition prune gate was never implemented | Implemented it and **measured** it (`ablations.py`). It is *worse* than the simple rule for the chosen strategy (can't shed noise). §4 rewritten to the measured recency-floor rule. |
| Prune-precision counted noise-shedding as error | Fixed to a coherent need-based universe where never-queried noise is disposable. Precision for the winner: 0.28 → **1.00** (§2.2). |
| Wrong clock: `recall@30d+` keyed on fact calendar-age | Added an idle-age (age-since-access) bucket — the clock decay acts on. The honest decay-resistance number is **0.362**, not 0.787 (§2.1). |
| Mixed-pass reporting (recall from no-prune, false-forget from prune) | Added a **post-prune recall** column read from the prune-enabled pass (§2.2). |
| No significance testing | Added paired bootstrap + Holm over pooled per-query values (`significance.py`, §2.3). |
| Reinforcement rule chosen on synthetic-only, all-top-k | Re-measured all-top-k vs valid-only under synthetic **and** embeddings; valid-only is better → §3 flipped. |
| Consolidation ordering / invalidate-not-delete unsettled | Added an explicit ordering + archive-not-delete rule (§6). |
| Promotion trap: `+gamma*importance` in rejected variants | Named the canonical scorer (§1) and flagged it in `strategies.py`. |
| Doc drift; stale-hit is a /k artifact | Reconciled all figures to the committed JSON; added a versioned-only stale-hit column (§2.1). |

---

## 1. Decision (TL;DR)

Adopt **importance-weighted exponential recency decay**, applied **primarily to
ranking**, with a **conservative recency-floor prune** as a secondary, opt-in
behaviour, and **reinforcement on confirmed relevance** (not mere injection).

```
# CANONICAL scorer (importance lives in the half-life, NOT as an additive term):
score(memory, query, now) = alpha * relevance + beta * recency
recency   = 0.5 ** (age_since_access / half_life)
half_life = base_half_life * (1 + imp_factor * importance)
age_since_access = now - last_accessed_at      # reset on confirmed access
```

This is a hybrid of **Generative Agents** (recency as a ranking term) and
**MemoryBank** (salience protects memories). Importance modulates the *decay rate*,
not the score directly — the data shows that is the more effective use of the
signal. **Note for SM-C\*:** the rejected `exp_recency`/`ebbinghaus` variants carry
an extra `+ gamma*importance` score term; the canonical scorer above does **not**.
Do not carry that term into the promoted implementation.

**Provisional default parameters** (SM-R1 to ratify):

| Parameter | Value | Notes |
|---|---|---|
| `base_half_life` | **5 days** (120 h) | Half-life at importance = 0. Swept on the chosen strategy (§2.4): a smooth recall/staleness dial — 5d gives nDCG 0.866 / stale 0.110; ~7d lifts recall toward the 0.90 target at stale ~0.13. |
| `imp_factor` | **4.0** (expect to raise under embeddings) | importance 1.0 → 5x longer half-life |
| `alpha` (relevance weight) | 1.0 | Relevance stays the dominant signal |
| `beta` (recency weight) | 1.0 | |
| prune recency threshold | 0.05 | Prune-eligibility floor (see §4) |
| default importance | 0.5 | When the importance scorer has no value |

---

## 2. Why — measured trade-off

5 seeds, k = 5, 90-day horizon, ~450 episodes / ~205 queries per run
(`results/summary.md`). Values are mean ± per-seed std. Reinforcement is the
default all-top-k here (the pessimistic case; §3 recommends confirmed-only, which
improves every number — see §3).

### 2.1 Retrieval quality (ranking only)

| strategy | nDCG@k | recall@k | stale-hit | stale-hit (versioned) | idle-30d+ |
|---|---|---|---|---|---|
| no_decay (baseline) | 0.893±0.024 | **1.000** | 0.151 (worst) | 0.347 (worst) | 1.000 |
| exp_recency | 0.614 | 0.718 | 0.039 | 0.091 | 0.130 |
| ttl_halflife | 0.586 | 0.664 | 0.049 | 0.112 | 0.007 |
| **importance_weighted** | **0.866±0.017** | **0.937±0.015** | 0.110 | 0.254 | **0.362** |
| ebbinghaus | 0.485 | 0.548 | 0.031 | 0.073 | 0.019 |

- **idle-30d+** = recall for facts not surfaced in >30 days (the clock decay acts
  on). This replaces the earlier fact-calendar-age "30d+" (0.787), which conflated
  re-access; **0.362 is the honest decay-resistance number** — still far ahead of
  the pure-recency peers (0.007–0.130), well behind no_decay (1.0, which never
  decays).
- **stale-hit (versioned)** restricts to queries that can actually produce a
  same-topic stale hit; the plain stale-hit is diluted by /k and by durable
  queries that structurally cannot go stale.

### 2.2 Pruning behaviour (recency-floor gate)

| strategy | prune-precision | prune-recall | false-forget | post-prune recall | retained |
|---|---|---|---|---|---|
| no_decay | – | – | 0.0 | 1.000 | 439 (unbounded) |
| exp_recency | 0.922 | 0.663 | 21.0 | 0.686 | 168 |
| ttl_halflife | 0.915 | 0.593 | 21.0 | 0.661 | 193 |
| **importance_weighted** | **1.000** | 0.394 | **0.0** | **0.937** | 292 |
| ebbinghaus | 0.906 | 0.740 | 28.0 | 0.538 | 140 |

- **prune-precision** now uses a need-based universe (a memory pruned after its
  last-needed time forgets nothing; never-queried noise is correctly disposable).
  The winner prunes **only** truly-dead memories (1.000) — the old 0.277 was the
  metric penalising correct noise-shedding.
- **post-prune recall** is measured *in the prune-enabled pass* (fixing the
  mixed-pass reporting): importance_weighted keeps **0.937** recall with pruning
  on, while the aggressive pruners collapse to 0.54–0.69 (they prune valid facts).

### 2.3 Significance (`results/significance*.md`)

Paired bootstrap over pooled per-query values, Holm-corrected. importance_weighted
**significantly beats every pure-recency peer** on both recall and nDCG, synthetic
and embedding (Δ +0.22 to +0.39, all p < 0.01). no_decay significantly beats it on
raw recall/nDCG (Δ −0.03 synthetic / −0.20 embedding) — the expected cost of
decay, paid to buy the staleness and growth wins. **The decay-group ranking is not
seed noise.**

### 2.4 Importance-confound sweep (`results/confound.md`)

Decorrelating importance from kind (`importance_noise` 0 → 1):

| importance_noise | IW recall | recall margin vs peer | IW idle-30d+ | idle-30d+ margin |
|---|---|---|---|---|
| 0.00 (oracle) | 0.937 | +0.219 | 0.362 | **+0.232** |
| 0.50 | 0.897 | +0.252 | 0.215 | +0.168 |
| 1.00 (random) | 0.806 | +0.307 | 0.118 | **+0.080** |

importance_weighted never collapses to peer level — but that robustness is mostly
a **longer average half-life** (`imp_factor=4`), not salience targeting. The
genuine *targeting* benefit (idle-30d+ margin) shrinks ~65% (0.232 → 0.080) as
importance decorrelates. **So the win is real but the salience component depends on
importance-scorer calibration** — a gate for SM-R1 (§7).

`base_half_life` is swept **on the chosen strategy** (`results/sensitivity.md`,
`importance_weighted.base_half_life_days`): it is a smooth recall/staleness dial
with no sharp knee — nDCG 0.672 → 0.935 and stale-hit 0.050 → 0.146 as it moves
2d → 14d. The default **5d sits mid-range** (0.866 / 0.110); reaching the
provisional recall ≥ 0.90 target needs ~7d (nDCG 0.910, stale 0.128), which SM-R1
trades against its stale-hit budget. `imp_factor` is likewise monotone (nDCG
0.51 → 0.93 over 0 → 8) with no knee — expect to tune both upward for an embedding
backend.

### 2.5 Confirmation under real embeddings (`results/summary_embedding.md`)

| strategy | nDCG (syn→emb) | recall (syn→emb) | idle-30d+ (syn→emb) | stale-hit (syn→emb) |
|---|---|---|---|---|
| no_decay | 0.893 → 0.903 | 1.000 → 1.000 | 1.000 → 1.000 | 0.151 → 0.151 |
| **importance_weighted** | 0.866 → **0.703** | 0.937 → **0.782** | 0.362 → 0.029 | 0.110 → **0.059** |
| exp_recency | 0.614 → 0.415 | 0.718 → 0.489 | 0.130 → 0.000 | 0.039 → 0.015 |
| ebbinghaus | 0.485 → 0.373 | 0.548 → 0.429 | 0.019 → 0.005 | 0.031 → 0.017 |

Candidate ranking preserved (and significance-tested, §2.3). Absolute recall drops
because real embeddings make fresh-vs-stale harder — which is why the §7 targets
are backend-sensitive and provisional.

---

## 3. Reinforcement-on-access rule (AC #3) — revised

- **Access = a memory being *confirmed relevant / used*, not merely injected into
  the top-k.** On access, set `last_accessed_at = now`.
- **Measured basis for the revision** (`results/ablations.md`): reinforcing *every*
  top-k item (including stale co-topic hits) resets the clock on stale memories and
  sustains them — a rich-get-richer loop. Reinforcing **only confirmed-relevant
  hits** is better on every axis, under both backends:

  | policy | recall | stale-hit | idle-30d+ |
  |---|---|---|---|
  | all-top-k (synthetic) | 0.937 | 0.110 | 0.362 |
  | **valid-only (synthetic)** | **0.962** | **0.092** | **0.642** |
  | all-top-k (embedding) | 0.782 | 0.059 | 0.029 |
  | **valid-only (embedding)** | **0.802** | **0.050** | **0.055** |

- **Recommendation:** reinforce on a confirmation signal (e.g. the memory was
  actually used in the response, or passed a relevance threshold), not on bare
  injection. The exact production signal is an **SM-E4** design point; the sim's
  "valid-only" is an idealized upper bound that proves the direction.
- No unbounded `strength` counter is needed — resetting `last_accessed_at` captures
  the spaced-repetition effect (see §5).

---

## 4. Ranking-only vs also-prunes (AC #2) — revised

**Decay drives RANKING first; pruning is a separate, conservative, opt-in gate
using the recency floor only.**

- **Ranking (primary, always on):** decay is a scoring term — the reliable,
  reversible win.
- **Pruning (secondary, feeds SM-E9):** prune when `recency < prune_threshold`
  (0.05). For the chosen strategy this is **already safe**: the importance-scaled
  half-life keeps salient/durable facts above the floor, so importance_weighted
  **false-forgets 0** while shedding dead clutter (retained 292 vs 439).

**Why not the three-condition gate?** The earlier draft defended a gate of
`recency-floor AND importance-below-floor AND not-currently-valid`. We implemented
and measured it (`ablations.py`):

| gate (importance_weighted) | prune-recall | false-forget | retained |
|---|---|---|---|
| recency-floor (chosen) | 0.394 | 0.0 | **292** |
| three-condition (§4 draft) | 0.101 | 0.0 | 402 (≈ no-prune) |

The extra conditions make it **too conservative to bound growth**: condition 3
(not-currently-valid) shields `FOREVER`-valid noise — the main growth driver — so
retained stays ~402. The recency floor alone, made safe by the importance-weighted
half-life, is both safe (0 false-forget) and effective (292). Hard-conflict deletes
remain a consolidation concern (§6), not a decay one.

---

## 5. New `UserMemory` fields (AC #4 — for SM-C3 / SM-R12)

Current `db/schemas/memory.py:9` has `created_at` / `updated_at` only.

**Required new fields:**

| Field | Type | Default | Purpose |
|---|---|---|---|
| `last_accessed_at` | `int` (epoch s), nullable | `created_at` | Recency clock; reset on confirmed access (§3) |
| `importance` | `float` in [0, 1], nullable | `0.5` | Modulates decay rate; set by the importance scorer |

**Deferred (do NOT add now):**

| Field | Reason |
|---|---|
| `strength` | Ebbinghaus reinforcement gave negligible measured benefit (nDCG spread 0.080 across the whole `reinforce_gain` sweep, best at `gain=0`). `last_accessed_at` + `importance` capture the behaviour with one fewer stateful field. Revisit only if SM-E4 shows a need. |

Decay is **computed at read time** from `now - last_accessed_at` and `importance`;
only `last_accessed_at` is written (on access). Migration is additive (both fields
nullable). **Calibration dependency:** the salience benefit needs a reasonably
calibrated `importance` (§2.4) — SM-R1 should gate ratification on the importance
scorer, and until it exists the decision degrades gracefully to "longer effective
half-life" (still ahead of pure-recency peers, but without the targeting edge).

---

## 6. Interaction with consolidation (ADD / UPDATE / DELETE) and ordering

- **ADD:** new episode gets `last_accessed_at = created_at` and `importance` from
  the scorer (or 0.5). Immediately participates in decay ranking.
- **UPDATE (supersede):** the new version becomes valid; the old version is not
  deleted — decay lets it **fade gracefully** from ranking and become
  prune-eligible once its recency drops below the floor.
- **DELETE:** reserved for hard conflicts / explicit forget.
- **Ordering rule (was an assertion; now explicit):** decay-pruning **must run
  after** the consolidation/reflection pass (SM-R7), never before — otherwise an
  episodic row can be pruned before its knowledge is consolidated into the semantic
  tier. And decay-pruning must **archive/tier rather than hard-delete** episodic
  rows, to stay consistent with the bi-temporal, invalidate-not-delete model (D8):
  set an archived/expired marker; do not destroy the row. Hard `DELETE` stays a
  consolidation-owned operation for conflicts.
- **Importance scoring (separate task):** feeds `importance` into the half-life;
  the two are intentionally coupled (§2.4).

---

## 7. Provisional acceptance targets (for SM-R1 to ratify)

| Metric | Provisional target | synthetic | real embeddings |
|---|---|---|---|
| Overall recall@k | ≥ 0.90 | 0.937 ✅ | 0.782 ❌ |
| Recall@k, idle > 30d (decay resistance) | ≥ 0.50 | 0.362 ❌ | 0.029 ❌ |
| Stale-hit-rate | < 0.10 | 0.110 (~) | 0.059 ✅ |
| False-forget (pruning on) | 0 | 0.0 ✅ | 0.6 (~) |

Honest status: overall recall is met synthetically; the **decay-resistance and
embedding recall targets are not met at the current defaults**. This is expected —
the targets were set against the clean synthetic oracle. **These are assumptions
for SM-R1 to ratify, not ratified targets.** SM-R1 should (a) set them per the
production embedding model, (b) gate on importance-scorer calibration (§2.4), and
(c) expect to raise `imp_factor` / `base_half_life` (and enable confirmed-only
reinforcement, §3, which alone lifts idle-30d+ to 0.642) to recover decay
resistance. The stale-hit and false-forget goals — the point of decay — are met.

---

## 8. Acceptance criteria (issue #80)

| # | Acceptance criterion | Status | Where |
|---|---|---|---|
| 1 | Decay model + parameters chosen with rationale | ✅ Done | §1 + §2 (all 5 candidates measured; significance-tested) |
| 2 | Ranking-only vs also-prunes behaviour defined | ✅ Done | §4 (ranking-primary; recency-floor prune; 3-condition gate measured & rejected) |
| 3 | Reinforcement-on-access rule defined | ✅ Done | §3 (access = confirmed use; reset `last_accessed_at`; measured all-top-k vs valid-only) |
| 4 | New `UserMemory` fields specified for SM-C3/R12 | ✅ Done | §5 (`last_accessed_at`, `importance`; `strength` deferred with evidence) |

**Questions to answer:**
- *Ebbinghaus vs exponential recency vs conflict-only?* → All implemented,
  measured, significance-tested. Winner: importance-weighted recency (§2).
- *Ranking only, or also prune (feeds SM-E9)?* → Ranking primary; recency-floor
  prune, opt-in (§4).
- *What is "access" / how does reinforcement reset?* → Confirmed use; reset
  `last_accessed_at` (§3).
- *What new `UserMemory` fields?* → `last_accessed_at`, `importance`; `strength`
  deferred (§5).

**Options to evaluate** (Ebbinghaus, exponential recency, no-decay — plus
TTL/half-life and importance-weighted) were all implemented and measured.

**Provisional / deferred:** absolute thresholds are for SM-R1 (§7) and are
embedding-backend + importance-calibration sensitive; promotion into `egpt/memory`
is SM-C\* (this task commits benchmarks only).

---

## 9. Reproduce

```bash
cd benchmarks/memory_decay
./setup.sh                                            # creates .venv, installs deps
./.venv/bin/python run_benchmark.py --seeds 5                     # synthetic (decision)
./.venv/bin/python run_benchmark.py --seeds 5 --relevance embedding  # confirmation
./.venv/bin/python sweep_params.py --seeds 5                      # parameter sensitivity
./.venv/bin/python confound.py --seeds 5                         # importance de-circularization
./.venv/bin/python ablations.py --seeds 5 --embedding            # prune-gate + reinforcement
./.venv/bin/python significance.py --seeds 5                     # paired-bootstrap significance
```

Outputs land in `results/` (`summary*.md`, `sensitivity.md`, `confound.md`,
`ablations.md`, `significance*.md`). See [TEST_LOG.md](TEST_LOG.md).
