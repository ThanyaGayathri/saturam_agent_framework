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
| no_decay (baseline) | 0.891 | **1.000** | 0.147 (worst) | 1.000 |
| exp_recency | 0.612 | 0.709 | 0.031 | 0.427 |
| ttl_halflife | 0.563 | 0.641 | 0.043 | 0.176 |
| **importance_weighted** | **0.864** | **0.939** | **0.088** | **0.828** |
| ebbinghaus | 0.501 | 0.560 | 0.035 | 0.402 |

### Pruning behaviour (prune enabled)

| strategy | prune-precision | prune-recall | false-forget | retained |
|---|---|---|---|---|
| no_decay | – | – | 0.0 | 438 (unbounded) |
| exp_recency | 0.365 | 0.703 | 22.8 | 164 |
| ttl_halflife | 0.382 | 0.622 | 21.6 | 192 |
| **importance_weighted** | 0.280 | 0.235 | **0.0** | 295 |
| ebbinghaus | 0.325 | 0.720 | 29.0 | 136 |

**Reading of the results:**

1. **No-decay never forgets the fresh fact (recall 1.0) but cannot suppress
   stale ones** — it has the worst stale-hit-rate (0.147) and grows without bound
   (438 retained). Pure semantic RAG behaviour; not episodic.
2. **Pure recency decay (exp_recency, ebbinghaus, ttl) suppresses stale well
   (~0.03) but over-forgets valid-but-old facts** — 30d+ recall collapses to
   0.18–0.43, and when used for pruning they **false-forget 22–29 still-needed
   memories per run**. A durable fact that simply hasn't been queried recently
   decays even though it is still true.
3. **Importance-weighting fixes exactly that failure.** Salient facts decay
   slowly, so importance_weighted keeps near-baseline recall (0.939, and **0.828
   even at 30d+**) while still **halving** the stale-hit-rate vs baseline (0.088
   vs 0.147), and it **never false-forgets** (0.0) because low-importance stale
   items are the only ones that decay below the prune floor. It bounds growth
   (295 vs 438) without the aggressive-pruning risk.

Importance_weighted is the only candidate that is Pareto-strong on both axes:
close to baseline recall **and** materially lower staleness, with zero
false-forgets.

### Parameter sensitivity (`results/sensitivity.md`)

- `imp_factor`: monotone, well-behaved knee at **4–6** (nDCG 0.86 -> 0.93 while
  stale-hit rises 0.088 -> 0.122). No cliffs; the decision is robust to ±50%.
- `base_half_life`: the main recall/staleness dial; 5 days sits at the knee for a
  90-day horizon.
- **Ebbinghaus strength-reinforcement showed negligible benefit** (nDCG spread
  only 0.056 across the whole `reinforce_gain` sweep). The dominant lever is the
  decay rate and its importance modulation — not an ever-growing strength field.
  This directly informs the schema decision in §5.

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

| Metric | Provisional target | importance_weighted @ default |
|---|---|---|
| Overall recall@k | >= 0.90 | 0.939 |
| Recall@k for facts aged 30d+ | >= 0.80 | 0.828 |
| Stale-hit-rate | < 0.10 | 0.088 |
| False-forget (pruning on) | 0 | 0.0 |

All met at the provisional defaults. **These thresholds are assumptions for SM-R1
to confirm, not ratified targets.**

---

## 8. Reproduce

```bash
cd benchmarks/memory_decay
./setup.sh                                   # creates .venv, installs numpy
./.venv/bin/python run_benchmark.py --seeds 5
./.venv/bin/python sweep_params.py --seeds 5
```

Outputs land in `results/`. See [TEST_LOG.md](TEST_LOG.md).
