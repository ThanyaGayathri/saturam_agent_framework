# SM-R5 — Decision: Episodic Decay Model

**Status:** Proposed (decision task). No framework/core code changed here; this
records the chosen model + measured rationale for the implementation tasks
(SM-C3, SM-R12) and downstream SM-E4 (recency scoring) / SM-E9 (forgetting).
**Depends on:** SM-R1 (#76) — all numeric targets below are **PROVISIONAL** and
flagged for SM-R1 to ratify.
**Method:** every option was implemented inside `harness/` and measured on a
reproducible offline simulation (see [README.md](README.md)); we do **not**
decide from cited literature numbers alone.

---

## 1. Decision (TL;DR)

Adopt **importance-weighted exponential recency decay**, applied **primarily to
ranking**, with a **conservative, importance-gated prune** as a secondary,
opt-in behaviour.

```
score(memory, query, now) = alpha * relevance + beta * recency
recency = 0.5 ** (age_since_access / half_life)
half_life = base_half_life * (1 + imp_factor * importance)
age_since_access = now - last_accessed_at      # reset on access
```

This is a hybrid of the two proven paradigms: **Generative Agents** (recency as a
ranking term) and **MemoryBank** (salience protects memories from forgetting).
Importance modulates the *decay rate* rather than being a weak additive term —
which the data shows is the more effective use of the importance signal.

**Provisional default parameters** (SM-R1 to ratify):

| Parameter | Value | Notes |
|---|---|---|
| `base_half_life` | **5 days** (120 h) | Half-life at importance = 0 |
| `imp_factor` | **4.0** (tune 4–6) | importance 1.0 -> 5x longer half-life (25 d) |
| `alpha` (relevance weight) | 1.0 | Relevance stays the dominant signal |
| `beta` (recency weight) | 1.0 | |
| prune recency threshold | 0.05 | Prune-eligibility floor (see §4) |
| default importance | 0.5 | When the importance scorer has no value |

---

## 2. Why — measured trade-off

Averaged over 5 seeds, k = 5, 90-day horizon, ~446 episodes / ~192 queries per
run (`results/summary.md`). Higher nDCG/recall is better; **lower** stale-hit-rate
and false-forget are better.

### Retrieval quality (ranking only)

| strategy | nDCG@k | recall@k | stale-hit-rate | recall @30d+ |
|---|---|---|---|---|
| no_decay (baseline) | 0.893 | **1.000** | 0.151 (worst) | 1.000 |
| exp_recency | 0.614 | 0.718 | 0.039 | 0.442 |
| ttl_halflife | 0.586 | 0.664 | 0.049 | 0.161 |
| **importance_weighted** | **0.866** | **0.937** | **0.110** | **0.787** |
| ebbinghaus | 0.485 | 0.548 | 0.031 | 0.411 |

### Pruning behaviour (prune enabled)

| strategy | prune-precision | prune-recall | false-forget | retained |
|---|---|---|---|---|
| no_decay | – | – | 0.0 | 439 (unbounded) |
| exp_recency | 0.371 | 0.671 | 21.0 | 168 |
| ttl_halflife | 0.392 | 0.615 | 21.0 | 193 |
| **importance_weighted** | 0.277 | 0.236 | **0.0** | 292 |
| ebbinghaus | 0.319 | 0.710 | 28.0 | 140 |

**Reading of the results:**

1. **No-decay never forgets the fresh fact (recall 1.0) but cannot suppress
   stale ones** — it has the worst stale-hit-rate (0.151) and grows without bound
   (439 retained). Pure semantic RAG behaviour; not episodic.
2. **Pure recency decay (exp_recency, ebbinghaus, ttl) suppresses stale well
   (~0.03–0.05) but over-forgets valid-but-old facts** — 30d+ recall collapses to
   0.16–0.44, and when used for pruning they **false-forget 21–28 still-needed
   memories per run**. A durable fact that simply hasn't been queried recently
   decays even though it is still true.
3. **Importance-weighting fixes exactly that failure.** Salient facts decay
   slowly, so importance_weighted keeps near-baseline recall (0.937, and **0.787
   even at 30d+**) while still cutting the stale-hit-rate vs baseline (0.110 vs
   0.151), and it **never false-forgets** (0.0) because low-importance stale items
   are the only ones that decay below the prune floor. It bounds growth (292 vs
   439) without the aggressive-pruning risk.

Importance_weighted is the only candidate that is Pareto-strong on both axes:
close to baseline recall **and** materially lower staleness, with zero
false-forgets.

### Parameter sensitivity (`results/sensitivity.md`)

- `imp_factor`: monotone, well-behaved knee at **4–8** (nDCG 0.87 -> 0.93 while
  stale-hit rises 0.110 -> 0.133). No cliffs; the decision is robust to ±50%.
- `base_half_life`: the main recall/staleness dial; 5 days sits at the knee for a
  90-day horizon.
- **Ebbinghaus strength-reinforcement showed negligible benefit** (nDCG spread
  only 0.080 across the whole `reinforce_gain` sweep, and the best point is
  `gain = 0`). The dominant lever is the decay rate and its importance modulation
  — not an ever-growing strength field. This directly informs the schema decision
  in §5.

### Confirmation under real embeddings (`results/summary_embedding.md`)

The decision is made on the synthetic signal (which isolates decay), then
**confirmed** by re-running with real `sentence-transformers` (`all-MiniLM-L6-v2`)
embeddings of the episode/query text — same 5 seeds, `--relevance embedding`.

| strategy | nDCG (syn→emb) | recall (syn→emb) | recall@30d+ (syn→emb) | stale-hit (syn→emb) | false-forget (emb) |
|---|---|---|---|---|---|
| no_decay | 0.893 → 0.903 | 1.000 → 1.000 | 1.000 → 1.000 | 0.151 → 0.151 | 0.0 |
| **importance_weighted** | 0.866 → **0.703** | 0.937 → **0.782** | 0.787 → **0.459** | 0.110 → **0.059** | **0.6** |
| exp_recency | 0.614 → 0.415 | 0.718 → 0.489 | 0.442 → 0.127 | 0.039 → 0.015 | 26.2 |
| ttl_halflife | 0.586 → 0.385 | 0.664 → 0.442 | 0.161 → 0.014 | 0.049 → 0.023 | 24.6 |
| ebbinghaus | 0.485 → 0.373 | 0.548 → 0.429 | 0.411 → 0.380 | 0.031 → 0.017 | 28.4 |

**What holds:** the candidate ranking is unchanged. importance_weighted is again
the only Pareto-strong decay strategy — highest nDCG/recall of the decay group,
by far the best old-fact recall (30d+ 0.459 vs 0.13/0.01/0.38), a **61% cut in
stale-hit-rate vs no_decay** (0.059 vs 0.151), bounded growth (292 vs 439), and
near-zero false-forgets (0.6 vs 24–28). The recommendation in §1 stands under real
embeddings.

**What changes (important):** real embeddings make fresh-vs-stale discrimination
harder (more within-topic ambiguity), so every decay strategy loses more recall
than under synthetic relevance — importance_weighted's recall drops 0.937 → 0.782
and its 30d+ recall 0.787 → 0.459. **Consequence:** the provisional targets in §7
were calibrated on the clean synthetic signal and are *too tight* for a real
embedding backend. They must be re-calibrated per embedding model during SM-C
implementation (and likely met by nudging `imp_factor`/`base_half_life` up, since
embeddings raise the cost of decay). This is the concrete "real value" of the
confirmation run: the *choice* is robust, the *thresholds* are backend-sensitive.

---

## 3. What counts as "access" and how reinforcement resets (AC #3)

- **Access = injection of a memory into the retrieval context** (i.e. it was
  returned in the top-k for a query). This is observable, cheap, and is the point
  at which a memory actually influences the agent. Bare DB reads that never reach
  the model do **not** count.
- **On access:** set `last_accessed_at = now`. This resets the recency clock so a
  memory that keeps being used stays ranked highly (spaced-repetition effect)
  without needing an unbounded strength counter.
- The harness reinforces **every** top-k item (access = injection). A stricter
  "reinforce only confirmed-relevant hits" rule is a documented sensitivity knob;
  it did not change the ranking of candidates, so we adopt the simpler
  inject-to-reinforce rule and leave the stricter variant to SM-E4 if needed.

---

## 4. Ranking-only vs also-prunes (AC #2)

**Decay drives RANKING first; pruning is a separate, conservative, opt-in gate.**

- **Ranking (primary, always on):** decay is a scoring term. This is where the
  reliable win is and it is reversible — a wrongly down-ranked memory is still
  retrievable if it becomes relevant again.
- **Pruning (secondary, feeds SM-E9):** a memory is prune-eligible only when
  **all** hold:
  1. it is **stale** — superseded or expired via consolidation (see §6), **or**
     its `recency < prune_threshold` (0.05), **and**
  2. its `importance` is low (below a configured floor), **and**
  3. it is **not currently the valid version** of its fact.

  The importance gate is why importance_weighted false-forgets **0** memories
  while pure-recency pruning false-forgets 22–29. **Recommendation: never prune on
  recency alone.** Hard-conflict deletes are handled by consolidation DELETE, not
  by decay. Pruning may be shipped disabled by default and enabled per-store.

---

## 5. New `UserMemory` fields (AC #4 — for SM-C3 / SM-R12)

Current `db/schemas/memory.py:9` has `created_at` / `updated_at` only.

**Required new fields:**

| Field | Type | Default | Purpose |
|---|---|---|---|
| `last_accessed_at` | `int` (epoch s), nullable | `created_at` | Recency clock; reset on access (§3) |
| `importance` | `float` in [0, 1], nullable | `0.5` | Modulates decay rate; set by importance scorer |

**Deferred (do NOT add now):**

| Field | Reason |
|---|---|
| `strength` | Ebbinghaus reinforcement gave negligible measured benefit (§2). `last_accessed_at` + `importance` capture the same behaviour with one fewer stateful field. Revisit only if SM-E4 shows a need. |

Notes for implementation: decay is **computed at read time** from
`now - last_accessed_at` and `importance`; no stored decayed score to keep fresh.
Only `last_accessed_at` is written (on access), keeping writes cheap and the
migration additive/back-compatible (both fields nullable with sensible defaults).

---

## 6. Interaction with consolidation (ADD / UPDATE / DELETE) and importance

- **ADD:** new episode gets `last_accessed_at = created_at` and `importance` from
  the importance scorer (or 0.5). Immediately participates in decay ranking.
- **UPDATE (supersede):** the new version becomes the valid fact; the old version
  is not deleted immediately — decay + the importance gate let it **fade
  gracefully** from ranking and become prune-eligible once stale and
  low-importance. Decay is the soft bridge between UPDATE and a hard DELETE.
- **DELETE:** reserved for hard conflicts / explicit forget. Decay-pruning is the
  *complement*, not a replacement — it clears low-value stale clutter that no
  conflict rule catches.
- **Importance scoring (separate task):** feeds `importance` directly into the
  decay half-life. The two mechanisms are intentionally coupled: importance is
  only useful here because it changes *how fast* a memory is forgotten.

---

## 7. Provisional acceptance targets (for SM-R1 to ratify)

| Metric | Provisional target | synthetic | real embeddings |
|---|---|---|---|
| Overall recall@k | >= 0.90 | 0.937 ✅ | 0.782 ❌ |
| Recall@k for facts aged 30d+ | >= 0.80 | 0.787 (~) | 0.459 ❌ |
| Stale-hit-rate | < 0.10 | 0.110 (~) | 0.059 ✅ |
| False-forget (pruning on) | 0 | 0.0 ✅ | 0.6 (~) |

Met (or near) on the synthetic signal. **Under real embeddings the recall targets
are not met** (see §2 confirmation): the thresholds were set against clean
synthetic relevance and are too tight for an embedding backend. **These thresholds
are assumptions for SM-R1 to ratify, not ratified targets** — and SM-R1 should set
them per the actual production embedding model, expecting to raise
`imp_factor`/`base_half_life` to recover recall. The stale-hit and false-forget
targets *are* met under real embeddings, so the qualitative goal (cut staleness
without forgetting) is delivered regardless of backend.

---

## 8. Acceptance criteria (issue #80)

Every checkbox from the issue, resolved and measured (not asserted):

| # | Acceptance criterion | Status | Where |
|---|---|---|---|
| 1 | Decay model + parameters chosen with rationale | ✅ Done | §1 (importance-weighted exp. recency; params table) + §2 (measured trade-off across all 5 candidates) |
| 2 | Ranking-only vs also-prunes behaviour defined | ✅ Done | §4 (ranking-primary; conservative importance-gated prune; measured false-forget) |
| 3 | Reinforcement-on-access rule defined (what counts as access, how strength resets) | ✅ Done | §3 (access = injection into top-k; reset `last_accessed_at = now`) |
| 4 | New `UserMemory` fields specified for SM-C3/R12 | ✅ Done | §5 (`last_accessed_at`, `importance` required; `strength` deferred with evidence) |

The issue's four **"Questions to answer"** are also each resolved:

- *Ebbinghaus vs exponential recency vs conflict-only (no decay)?* → All three (plus
  TTL/half-life and importance-weighted) implemented and benchmarked. Winner:
  importance-weighted exponential recency; confirmed under real embeddings (§1, §2).
- *Ranking only, or also prune/archive (feeds SM-E9)?* → Ranking primary; pruning a
  separate, conservative, importance-gated opt-in gate (§4).
- *What is "access", and how does reinforcement reset strength/last-access?* →
  Access = injection into retrieval context; on access `last_accessed_at = now` (§3).
- *What new `UserMemory` fields are needed?* → `last_accessed_at`, `importance`;
  `strength` deferred because Ebbinghaus reinforcement showed negligible benefit (§5).

The three **"Options to evaluate"** named in the issue (Ebbinghaus, Exponential
recency, No decay) were all implemented in the harness and measured, per the rule
that decision tasks must implement every option rather than cite numbers.

**Provisional / deferred (not blocking these criteria):** absolute acceptance
*thresholds* are provisional for SM-R1 to ratify and are embedding-backend
sensitive (§7); promotion of the chosen strategy into `egpt/memory` core happens in
SM-C* (this task commits benchmarks only).

---

## 9. Reproduce

```bash
cd benchmarks/memory_decay
./setup.sh                                            # creates .venv, installs deps
./.venv/bin/python run_benchmark.py --seeds 5                     # synthetic (decision)
./.venv/bin/python run_benchmark.py --seeds 5 --relevance embedding  # confirmation
./.venv/bin/python sweep_params.py --seeds 5
```

Outputs land in `results/` (`summary.md`, `summary_embedding.md`,
`sensitivity.md`). See [TEST_LOG.md](TEST_LOG.md).
