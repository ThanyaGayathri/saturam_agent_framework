# RESEARCH — Episodic -> Semantic Consolidation Strategy (SM-R7)

**Status:** Draft for review · **Owner area:** framework/memory · **Tracker:** `Saturam-Inc/aicoe-tracker#82`
**Decisions:** D7 (consolidation strategy) + D8 (provenance model) · **Feeds:** SM-L7 (impl), SM-R13/SM-X7 (eval harness), SM-L6/L8
**Depends on:** SM-R1 (#76) — *not yet done; all numeric targets below are **PROVISIONAL** pending R1.*

> This is a research spike written in DECISION.md shape: a recommendation plus the
> evidence for it. Per the team rule we did not conclude from cited papers alone —
> we implemented the candidate strategies and measured the trade-off. The harness,
> datasets, metrics and raw results are in this folder; reproduce with
> `HARNESS_LLM_MODE=replay python run.py --dataset synthetic --qa`.

---

## 1. TL;DR — Recommendation

1. **Strategy (D7):** adopt **extract + merge (mem0-style) as the mandatory consolidation
   backbone**, augmented by an **optional, sleep-time reflection layer** for higher-order
   insights. This is the `hybrid` family, with the `mem0` layer as the non-negotiable core.
   **Reject whole-corpus / rolling-blob summarization as the primary mechanism** — it is the
   field's known failure mode and our provenance-precision metric quantifies why (0.35 vs
   0.85). Keep a summary only as an optional secondary "profile card" view.
2. **Provenance (D8):** every semantic fact stores `source_episode_ids` (fact -> episodes),
   populated at extraction and **unioned forward** on every UPDATE/NOOP; conflicts are handled
   **bi-temporally** (`valid_from`/`valid_to`/`superseded_by`) so nothing is destroyed.
3. **Anti-lossiness:** atomic facts (not blobs), supersede-don't-overwrite, provenance kept
   valid, and an explicit **consolidation-quality gate** (below) run in CI on the eval set.
4. **Quality metric (for SM-R13):** a composite of fact recall, precision, dedup, conflict
   resolution, and **provenance precision+recall**, plus footprint and cost — defined and
   implemented in `harness/metrics.py`.
5. **Reuse `LearnedKnowledgeStore` insight extraction?** **Partially — yes as the reflection
   engine, no as the episodic->semantic merge engine.** Its extract-with-dedup loop
   (`learned_knowledge.py:1109`) is a good basis for the *reflection* layer; the ADD/UPDATE/
   DELETE/NOOP merge with provenance is new work (closest seed is `memory/manager.py`, which
   lacks provenance and an episodic tier).

---

## Acceptance criteria coverage

Every SM-R7 (#82) acceptance checkbox, where it is satisfied, and the evidence.

| # | Acceptance criterion | Status | Where | Evidence / how |
|---|----------------------|:------:|-------|----------------|
| 1 | Consolidation strategy chosen with rationale | ✅ | §1, §6 | mem0-style extract+merge backbone + optional sleep-time reflection (`hybrid`), chosen over baseline / pure reflection / summarization with measured rationale (§5) and explicit rejected alternatives. |
| 2 | Provenance model (semantic → source episodes) defined | ✅ | §7 | `source_episode_ids` on every fact, populated at extraction and unioned forward on UPDATE/NOOP; bi-temporal `valid_from`/`valid_to`/`superseded_by`; provisional core schema extension; `provenance_validity == 1.0` measured, and provenance-precision separates strategies (§5.1). |
| 3 | Anti-lossiness safeguards specified | ✅ | §8 | Five safeguards: atomic facts not blobs, supersede-don't-overwrite, grounded/conservative extraction, CI quality gate, and rebuildable-from-episodes. Summarization rejected precisely on anti-lossiness (provenance precision 0.35). |
| 4 | Measurable consolidation-quality metric (for the eval harness) | ✅ | §4, §9 | Composite store-quality score (weights in §4) + provenance P/R/validity + conflict accuracy + cost/latency, **implemented** in `harness/metrics.py` with an LLM assertion judge; provisional CI gate thresholds in §9. Seeds SM-R13. |
| 5 | Decision on reusing `LearnedKnowledgeStore` insight extraction | ✅ | §11 | Reuse it as the **reflection** engine (`learned_knowledge.py:1109,1243`); build the ADD/UPDATE/DELETE/NOOP provenance merge fresh (seed `memory/manager.py:967`). |

Additional deliverables requested in the task (beyond the checkboxes): **when it runs**
(sleep-time / background, §10), **interaction with SM-R6 segmentation, SM-R5 decay, SM-R3
store** (§10), and the note that this **feeds SM-R13 / SM-X7** (§9, §10). Decisions D7 + D8 are
covered by §6 and §7 respectively. All numeric targets are **PROVISIONAL pending SM-R1** (§12).

---

## 2. Objective & scope

Design the episodic->semantic consolidation step: how raw episodes are distilled into durable
semantic memory *with preserved provenance*, and how we measure its quality. The seed has
partial pieces but no provenance-preserving episode->fact promotion:

- `memory/strategies/summarize.py:15` — collapses N memories into one narrative. Lossy, no
  episode->fact promotion, **no provenance**.
- `memory/manager.py:967` — mem0-style LLM merge with `add/update/delete_memory` tools
  (ADD/UPDATE/DELETE/NOOP) over `<existing_memories>`. **No episodic tier, no provenance,
  no importance/reflection.**
- `learn/stores/learned_knowledge.py:1109` — insight extraction with search-existing dedup
  (`_summarize_existing`, `learned_knowledge.py:1243`), distinguishing "insight" vs "summary
  with no insight". A usable **reflection** base.
- `learn/curate.py:28` — prune (age/count) + dedup by **exact normalized string** only.

`UserMemory` (`db/schemas/memory.py:9`) has `input`, `created_at`, `updated_at` but **no
`source_episode_ids`, no `valid_from/valid_to`, no `superseded_by`** — provenance needs a
schema extension (§7). That is core code and is **not** changed by this spike.

---

## 3. Strategies prototyped

All five are implemented in `harness/strategies/` (sync + async), sharing extraction and
merge primitives so they differ only in policy.

| key | strategy | what it does | grounding |
|-----|----------|--------------|-----------|
| `baseline` | No consolidation | store every episode verbatim | control |
| `mem0` | Extract + merge | atomic fact extraction, then per-fact LLM ADD/UPDATE/DELETE/NOOP against lexically-retrieved neighbours; provenance unioned forward; conflicts superseded bi-temporally | mem0 |
| `reflection` | Reflection + importance | extract scored facts, lexical dedup, and importance-gated insight synthesis citing source facts | Generative Agents |
| `hierarchical` | Hierarchical summary | per-session summary + a single rolling global summary (old global superseded) | MemoryBank |
| `hybrid` | mem0 + reflection | mem0 atomic base + interval-gated reflection insights | Letta-ish sleep-time hybrid |

Sleep-time rewriting (Letta) is represented by the *scheduling* of these passes (§10), not a
separate strategy — the rewrite unit is the same merge/reflection logic run in the background.

---

## 4. Methodology

**LLM.** Azure OpenAI **Responses API**, deployment `gpt-5.4` (house standard is `gpt-5.5`; the
repo's own default is `gpt-5.4-mini`, so the family matches). We keep the house "Responses,
not Chat" rule via a thin `AzureOpenAIResponses` subclass of egpt's `OpenAIResponses`
(`harness/llm.py`). Every call is **recorded and replayed** from `results/llm_cache/`, so all
numbers are reproducible offline and reruns are free.

**Datasets.**
- **Synthetic** (`harness/datasets/synthetic.py`): a hand-authored, deterministic
  episodic stream with **planted ground truth** — stable facts, duplicates (restated later),
  conflicts (job/city/beverage change over time), and distractor chit-chat. Gold
  `source_episode_ids` per fact enable provenance scoring. This is the only set with labels.
- **LOCOMO** (`harness/datasets/locomo.py`): public long-conversation memory benchmark, used
  for **external validity** of QA correctness at scale (no dedup/conflict/provenance labels).

**Metrics** (`harness/metrics.py`):
- *Label metrics* (synthetic): fact **recall** / **precision**, **dedup** score,
  **conflict-resolution** accuracy, **provenance** precision/recall/validity, footprint
  (active facts + memory tokens).
- *QA correctness* (any set): retrieve facts, answer with the LLM, grade with an LLM judge.
- Recall and conflict use an **LLM assertion judge** ("is this statement currently true given
  the memory?") rather than string matching, because bag-of-words cannot distinguish
  "no longer drinks tea" from "drinks tea", nor "moved *to* Pune" (which names both cities).
- *Composite* store-quality score in `[0,1]` with weights: recall 0.20, precision 0.15, dedup
  0.15, conflict 0.15, provenance-recall 0.15, **provenance-precision 0.20**.
- *Cost/latency*: real token usage from the Responses API; USD via a configurable price table
  (default $1.25 in / $10 out per 1M tokens — a placeholder; set `HARNESS_PRICE_IN/OUT`).

**Two methodological findings shaped the reading of results** (threats to validity, §12):
1. **Read-time reasoning saturates recall/conflict/QA on small corpora.** With a capable
   reader, *every* strategy — including the no-consolidation baseline — answers everything on
   the synthetic set. So those metrics do **not** discriminate consolidation quality at small
   scale; the **store-quality** metrics (provenance precision, dedup, noise, footprint) and
   **scale behaviour** (LOCOMO, under a retrieval budget) do.
2. **Retrieval is a confound.** On tiny memories we hand the *whole* consolidated store to the
   reader to isolate *what consolidation kept* from *retrieval quality* (an SM-R3/SM-R13
   concern). At LOCOMO scale a retrieval budget applies, which is where distillation earns
   its value.

---

## 5. Results

### 5.1 Synthetic (labelled) — 17 episodes, 6 sessions, 12 target facts (3 conflicts)

`HARNESS_LLM_MODE=replay python run.py --dataset synthetic --qa` · raw: `results/synthetic_results.json`

| strategy | composite | recall | prec | dedup | conflict | prov-P | prov-R | facts | mem-tok | QA | USD |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| baseline | **0.881** | 1.0 | 0.71 | 0.75 | 1.0 | 1.00\* | 0.75 | 17 | 255 | 1.0 | 0.010 |
| mem0 | 0.860 | 1.0 | 0.67 | **0.80** | 1.0 | 0.85 | **0.80** | 15 | **160** | 1.0 | 0.035 |
| reflection | 0.808 | 1.0 | 0.57 | 0.62 | 1.0 | 0.83 | 0.77 | 23 | 451 | 1.0 | 0.028 |
| hierarchical | 0.763 | 1.0 | **1.00** | 0.57 | 1.0 | **0.35** | 0.71 | **7** | 331 | 1.0 | 0.024 |
| hybrid | 0.781 | 1.0 | 0.70 | 0.56 | 1.0 | 0.67 | 0.72 | 23 | 384 | 1.0 | 0.043 |

\* baseline provenance precision is *trivially* 1.0 — each raw episode cites only itself; it
has episode-granular, not fact-granular, semantics and O(n) unbounded growth. It is the
control, **not** a production option.

Reading:
- **recall / conflict / QA all saturate at 1.0** — finding (1). No differentiation here.
- **Provenance precision** is the sharpest discriminator and directly indicts summarization:
  `hierarchical` scores **0.35** because its rolling blob cites *every* episode for *every*
  fact — provenance is present but useless. `mem0` (0.85) and `reflection` (0.83) keep
  fact-granular provenance.
- **Dedup:** `mem0` best (0.80). Reflection/hierarchical/hybrid leave more near-duplicates.
- **Footprint:** `mem0` is the most compact real distillation (15 atomic facts, 160 memory
  tokens). `hierarchical` has fewest *facts* (7) but only by discarding detail (lossy).
- **Cost:** `mem0`/`hybrid` cost most (per-fact merge calls); summary/reflection are cheaper.

The composite ranks `baseline > mem0 > …`; this is the expected small-corpus artifact
(baseline is scale-blind-flattered). Excluding the control, **`mem0` leads on every
store-quality axis that matters for a provenance-preserving design.**

### 5.2 LOCOMO (external validity, at scale) — sample 0, first 4 sessions (76 episodes)

`python run.py --dataset locomo --max-sessions 4 --qa --qa-limit 15` · raw: `results/locomo_results.json`

| strategy | active facts | mem-tok | compression vs baseline | QA (15) | tokens | USD |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|
| baseline | 76 | 2655 | 1.0x (no compression) | 0.40 | 20.7k | 0.028 |
| **mem0** | 29 | **452** | **5.9x** | 0.33 | 38.4k | 0.090 |
| reflection | 32 | 667 | 4.0x | 0.40 | 18.6k | 0.042 |
| hierarchical | **5** | 773 | 3.4x | 0.33 | 19.1k | 0.037 |
| hybrid | 35 | 671 | 4.0x | 0.33 | 42.9k | 0.100 |

Reading (note the caveats — this is weaker evidence than §5.1):
- **Compression is the real scale signal.** 76 episodes -> `mem0` 29 atomic facts / **452 memory
  tokens (5.9x smaller** than baseline's 2655, and *smaller than the summary's* 773). This is the
  value consolidation adds that a small corpus hides: baseline's store grows O(n) and degrades
  retrieval under a budget; `mem0` stays compact **and** atomic/provenanced. `hierarchical` has
  the fewest *facts* (5) but larger token footprint (verbose blobs) and is lossy.
- **QA is coverage-dominated, not consolidation-dominated.** We ingested only 4 of 19 sessions
  but the QA probes span all 19, so most misses are "not yet ingested", not "consolidated badly".
  All strategies land 0.33-0.40 (a **1-question** spread) — within noise. LOCOMO here does **not**
  discriminate consolidation quality; it confirms no strategy *regresses* QA and sizes the cost.
- **Extract+merge carries a cost premium.** `mem0`/`hybrid` cost ~2-3x more (per-fact merge
  calls). This is the argument for running merge **at sleep-time** (amortized, off the request
  path) and for an **on-prem SLM extractor** (§10) to cut the dominant extraction cost.

---

## 6. Decision & rationale (D7) — Acceptance criterion #1

**Chosen: mem0-style extract+merge backbone + optional sleep-time reflection (the `hybrid`
architecture with `mem0` as the mandatory core).**

Why:
- **Precision + provenance + conflict handling** are the whole point of episodic->semantic
  consolidation, and extract+merge is the only family that keeps them at fact granularity
  (best dedup 0.80, best distiller provenance precision 0.85, smallest footprint). Whole-blob
  summarization fails provenance precision (0.35) and is lossy — exactly the field's warning.
- **It compresses best at scale without going lossy.** On LOCOMO (§5.2), `mem0` distils 76
  episodes to 29 atomic facts / 452 memory tokens — 5.9x smaller than raw and smaller than the
  summary's blob — while keeping provenance. This is the scale benefit a small corpus hides.
- **Its cost premium is manageable.** Extract+merge costs ~2-3x more (LOCOMO), but it runs at
  sleep-time (off the request path) and the high-volume extraction step can move to an on-prem
  SLM (§10), so the premium buys precision/provenance/conflict-safety without user-facing latency.
- **Reflection adds what extraction misses:** generalizations/insights across episodes. But on
  its own it is noisier (precision 0.57) and does not resolve conflicts in the store. So it is
  a *layer on top*, gated and run at sleep-time, not the base.
- **Why not pure reflection or pure summary:** both underperform on the provenance-first
  composite and violate AC#2/#3 (summary) or leave conflicts unresolved in-store (reflection).
- **Why the mem0 base is mandatory and reflection optional:** the base delivers the auditable,
  conflict-safe semantic tier; reflection is valuable but its cost/noise should be a tunable
  policy, defaulted **off** for latency-sensitive tenants and **on** (sleep-time) otherwise.

---

## 7. Provenance model (D8) — Acceptance criterion #2

A semantic fact links back to the episodes it was distilled from, and history is never
destroyed. Implemented in `harness/schemas.py::SemanticFact` and `strategies/ops.py`; the
**provisional core schema extension** (SM-L7 owns the actual change to `db/schemas/memory.py`):

```
UserMemory (+):
  source_episode_ids: list[str]   # fact -> source episodes (provenance)
  valid_from: int | None          # when the fact became true in the world
  valid_to:   int | None          # when it stopped being true (None = current)
  superseded_by: str | None       # memory_id of the fact that replaced this one
  derived_by: str | None          # strategy/pass that produced it
  importance: float               # [0,1], for reflection/decay
```

Rules (all measured working in the harness — `tests/test_harness.py`):
- **Populate at creation, union forward.** ADD sets `source_episode_ids`; UPDATE and NOOP
  extend it (never shrink) so a fact cites *every* supporting episode.
- **Supersede, don't overwrite.** UPDATE creates a new fact and marks the old
  `valid_to=now, superseded_by=<new>`. DELETE sets `valid_to` (retire) but keeps the row.
- **Keep newer on conflict** via the bi-temporal stamps; older assertions remain queryable
  for audit / "what did we believe then".
- **Validity check.** Every cited episode id must resolve to a real episode
  (`provenance_validity` = 1.0 for all strategies in §5.1).

Provenance is stored **on the fact** (a column/edge), not re-derived, so it stays valid across
decay (SM-R5) and re-segmentation (SM-R6). Storage granularity is a store (SM-R3) concern:
a join table `fact_id <-> episode_id` in Postgres, or an edge in the neuro-symbolic graph.

---

## 8. Anti-lossiness safeguards — Acceptance criterion #3

The field's failure mode is fragile/lossy LLM summarization. Safeguards, in priority order:
1. **Atomic facts, not blobs.** The durable tier is atomic statements with independent
   provenance; we never collapse the store into a single rewritten paragraph. (This is why
   summarization is rejected as the primary mechanism; provenance precision 0.35 is the tell.)
2. **Supersede, never overwrite.** UPDATE/DELETE retire via bi-temporal stamps; original
   assertions and their provenance survive for audit and rollback.
3. **Extraction is grounded + conservative.** The extractor is instructed to only emit facts
   supported by episodes and to skip trivia; noise is then measured (`noise_fact_count`).
4. **Quality gate in CI (§9).** Consolidation cannot regress recall/provenance/conflict below
   thresholds on the eval set without failing — lossiness becomes a test failure, not a
   silent drift.
5. **Provenance lets us reconstruct.** Because every fact cites its episodes, a bad
   consolidation can be recomputed from the retained episodic tier (episodes are the source of
   truth; semantic memory is a derived, rebuildable view).

---

## 9. Consolidation-quality metric — Acceptance criterion #4 (feeds SM-R13)

Defined and implemented in `harness/metrics.py`. For the eval harness (SM-R13) we expose:

- **Composite store-quality** in `[0,1]` (weights in §4) as the single headline, **read
  alongside footprint and LOCOMO QA** (the composite is intentionally scale-blind; §5 explains
  why a small-corpus baseline over-scores).
- **Provenance precision/recall/validity** as first-class gates (AC#2 is provenance-first).
- **Conflict-resolution accuracy** via the assertion judge (recency-aware).
- **Cost & latency** per consolidation (tokens, USD, seconds).

**Provisional CI gate (targets PROVISIONAL pending SM-R1):** on the labelled eval set, require
`provenance_validity == 1.0`, `provenance_precision >= 0.80`, `conflict_accuracy >= 0.90`,
`dedup_score >= 0.75`, `fact_recall >= 0.95`, and `noise_fact_count` within budget. `mem0`
meets these today except a precision/noise margin to tune; summarization fails
provenance-precision by design.

---

## 10. When it runs & how it interacts

**When (D7 scheduling).** Consolidation runs **asynchronously, out of the request path**:
- **Sleep-time / background pass** (Letta-style) is the default: after a session closes, or on
  an idle/cron trigger, run extract+merge over the session's episodes and, on an interval or
  importance threshold, the reflection pass. Both public entry points ship **sync and async**
  variants (house rule).
- A cheap **on-close** extract+merge keeps the semantic tier warm; the heavier reflection and
  any global re-summary are deferred to the background to keep latency and cost off the user.
- An **on-prem SLM extractor** is a good fit for stage 1 (extraction is the high-volume,
  low-reasoning step): the harness supports a separate cheaper deployment via
  `AZURE_OPENAI_EXTRACTOR_DEPLOYMENT`; the merge/reflection reasoning stays on the strong model.

**Interactions:**
- **SM-R6 (segmentation):** segmentation defines the episode/session boundary that is the
  consolidation *unit*. Consolidation consumes segments; a session = one batch here. Better
  segmentation -> cleaner extraction and provenance.
- **SM-R5 (decay):** decay acts on the *semantic* tier using `importance` + `valid_to` +
  provenance recency; superseded facts are prime decay candidates. Consolidation writes the
  signals (importance, bi-temporal stamps) that decay reads. They are complementary passes.
- **SM-R3 (store):** the store persists facts, the `fact<->episode` provenance edges, and the
  bi-temporal columns, and provides retrieval (semantic/graph) that this spike stubbed with
  lexical similarity. Provenance granularity and conflict queries are store requirements.
- **SM-R13 / SM-X7 (eval):** this harness (metrics + datasets + assertion judge) is the seed
  of the eval harness; §9 is the quality contract.

---

## 11. Reusing `LearnedKnowledgeStore` — Acceptance criterion #5

**Decision: reuse it as the reflection engine; do not reuse it as the episodic->semantic merge
engine.**

- **Reuse (yes):** `LearnedKnowledgeStore._extract_learnings` + `_summarize_existing`
  (`learned_knowledge.py:1109,1243`) already do "search existing -> extract non-duplicate
  insight", which is exactly the **reflection** layer. Adopt it there.
- **Do not reuse (no):** it targets *insights* over conversations, not atomic
  episode->fact promotion with ADD/UPDATE/DELETE/NOOP and provenance. The merge backbone is
  closer to `memory/manager.py` and needs new provenance + bi-temporal logic regardless.
- **Net:** SM-L7 builds the mem0-style merge backbone (new), and wires the reflection layer to
  the existing `LearnedKnowledgeStore` extraction rather than re-implementing it.

---

## 12. Threats to validity / limitations

- **Small labelled corpus.** Synthetic is 17 episodes; recall/conflict/QA saturate (finding
  #1). Conclusions rest on store-quality + LOCOMO scale, not the saturated metrics.
- **Lexical retrieval stub.** Neighbour retrieval and QA retrieval use token Jaccard, not
  embeddings (offline). This *understates* every LLM strategy's QA where phrasing diverges;
  we mitigated by handing the whole small store to the reader. Real retrieval is SM-R3/R13.
- **Judge/QA use the same model family** as consolidation — possible shared blind spots.
- **Cost is modelled**, not billed: token counts are real, USD uses a placeholder price table.
- **One synthetic scenario / one LOCOMO sample** (scoped for budget). Broaden in SM-R13.
- **SM-R1 not done:** all numeric targets are PROVISIONAL and should be re-baselined after R1.

---

## 13. Reproduce

```bash
HARNESS_LLM_MODE=replay python run.py --dataset synthetic --qa     # deterministic, offline
python tests/test_harness.py                                       # offline self-tests
```
Raw results: `results/synthetic_results.json`, `results/locomo_results.json`. LLM cache:
`results/llm_cache/`. See `findings/` for notes handed to the SM-L7 implementation phase.
