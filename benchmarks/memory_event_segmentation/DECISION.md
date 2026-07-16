# SM-R6 — Decision: Event-Segmentation Approach

**Status:** Proposed (decision task). No framework/core code changed here; this
records the chosen approach + measured rationale for the implementation tasks
(SM-E3 segmentation, SM-E2 write-back) and the downstream consolidation lane
(SM-R7).
**Depends on:** SM-R1 (#76) — every numeric target/threshold below is
**PROVISIONAL** and flagged for SM-R1 to ratify.
**Method:** every option was implemented inside `harness/` and measured on a
reproducible, labelled synthetic benchmark (see [README.md](README.md)) — under
both synthetic and **real text embeddings**, plus a **real gpt-5.4 (Azure
Responses)** run and a **real on-prem SLM (llama3 8B / ollama)** run. We do
**not** decide from cited literature numbers alone.

---

## 1. Decision (TL;DR)

Adopt a **tiered segmentation** design:

- **v1 — `time_gap` boundaries with a turn-count safety cap (deterministic,
  inline on write-back).** A new episode starts when the wall-clock gap since the
  previous message exceeds a threshold, with a hard cap so no episode exceeds N
  turns. Zero model cost, deterministic, runs inline. This is the "turn/session"
  option the issue expected for v1 — the data confirms it, and shows the
  *time-gap* variant strictly dominates fixed turn windows.

- **v2 — `hybrid_gap_emllm` on the async consolidation lane (no API cost).** The
  same time-gap anchors **unioned** with **EM-LLM** boundaries (Bayesian surprise
  + modularity graph refinement) over episode embeddings. This is the richest
  no-API-cost option and, under **real embeddings**, it beats every other
  local method. This is the EM-LLM path the issue flagged as a v2 candidate; our
  data says deploy it *as a hybrid* with the v1 anchors, not standalone.

- **Premium (optional, async, high-value sessions) — `llm_boundary` with
  `gpt-5.5` via `OpenAIResponses`.** Highest measured quality, but ~2.5 s and
  ~$0.009 per session and non-deterministic. Reserve for sessions explicitly
  flagged as high-value; never on the inline path.

**Provisional default parameters** (SM-R1 to ratify):

| Parameter | Value | Notes |
|---|---|---|
| `time_gap_seconds` | **900 s** (15 min) | New episode if idle gap exceeds this. Safely above the within-session turn cadence. |
| `max_turns_per_episode` | **40** | Hard cap so a long unbroken session still segments. |
| `min_turns_per_episode` | **3** | Suppress micro-episodes / backchannel splits. |
| EM-LLM `gamma` (surprise threshold) | **1.0** (σ units) | `boundary if surprise > mu + gamma*sigma`. Flat over 0.5–1.5 (robust). |
| EM-LLM `objective` | **modularity** | Refinement objective; better Pk than conductance. |
| EM-LLM `refine_radius` | **3** turns | Graph-refinement search window. |
| LLM model (premium) | **`gpt-5.5`** (`OpenAIResponses`) | Measured on a gpt-5.4 Azure deployment; see §6. |

---

## Acceptance criteria (issue #81)

- [x] **Segmentation method chosen for v1** (with a v2 path if surprise-based is deferred)

      v1 = `time_gap` + turn-count cap (deterministic, inline). v2 = `hybrid_gap_emllm`
      (EM-LLM surprise + graph refine) on the async lane. Surprise-based is NOT
      deferred — it is implemented and promoted to v2. See §1.

- [x] **Cost/quality trade-off documented**

      Measured F1 / precision / recall / Pk / WindowDiff AND latency + $/session for
      every option, under synthetic + real embeddings + a real gpt-5.4 run + a real
      on-prem SLM run; sensitivity + robustness sweeps. See §3 and `results/`.

- [x] **Integration point with write-back (SM-E2) defined** (inline vs async)

      Inline deterministic `time_gap` + turn-cap on write-back; async EM-LLM hybrid
      (+ optional LLM) on the SM-R7 consolidation lane, with rationale. See §4.

- [x] **Boundary metadata to store on each episode specified**

      Full field table: `boundary_method`, `boundary_trigger`, `boundary_confidence`,
      `refined`, `segmenter_version`, plus event spans / timestamps. See §5.

Also answers the open questions in the issue body:

- [x] **Turn/session vs LLM-decided vs surprise-based (EM-LLM)?** — all implemented and
  measured; tiered choice (turn/time-gap v1, EM-LLM hybrid v2, LLM optional premium). §1, §3.
- [x] **Cost vs quality per method?** — quantified: EM-LLM ~20 ms/$0; LLM ~2.5 s/$0.009;
  SLM ~338 s/$0 (poor). §3.
- [x] **Sub-session events, or are turn/session boundaries enough for v1?** — turn/time-gap
  enough for v1 (precision 1.0, deterministic, inline); sub-session semantic events are the v2 hybrid. §1, §4.
- [x] **Inline on write-back or async consolidation?** — both, split by tier. §4.

All numeric thresholds/targets are **provisional** pending SM-R1 (#76) — see §6.

---

## 2. Options evaluated (all implemented)

| # | Approach | Signal | Cost |
|---|---|---|---|
| 1 | `single_session` | none (1 episode) | baseline (under-segment) |
| 2 | `per_turn` | none (every turn) | baseline (over-segment) |
| 3 | `fixed_window` | turn count | deterministic, ~0 |
| 4 | `time_gap` | message timestamps | deterministic, ~0 |
| 5 | `embedding_drift` | TextTiling on embeddings | ~embedding only |
| 6 | `surprise_em_llm` | Bayesian surprise + modularity/conductance graph refine (EM-LLM) | ~embedding only |
| 7 | `llm_boundary` | LLM topic-shift call (`gpt-5.5` / on-prem SLM) | API/GPU |
| 8 | `hybrid_gap_*` | (4) ∪ (5 or 6) | ~embedding only |

---

## 3. Why — measured trade-off

Averaged over 5 seeds, boundary tolerance ±1 turn, ~100 events / ~11 true
episodes per stream. **Higher F1/precision/recall is better; lower Pk / WindowDiff
is better.** Two embedding backends are reported: the deterministic **synthetic**
signal (isolates topic structure — `results/summary.md`) and **real
`all-MiniLM-L6-v2` embeddings** of the actual turn text (`results/summary_embedding.md`).

### Quality (no-API-cost methods)

| approach | F1 (syn) | F1 (real-emb) | Pk (real-emb) | precision (real-emb) | recall (real-emb) |
|---|---|---|---|---|---|
| single_session | 0.000 | 0.000 | 0.465 | 0.000 | 0.000 |
| per_turn | 0.161 | 0.161 | 0.535 | 0.088 | 1.000 |
| fixed_window | 0.271 | 0.271 | 0.423 | 0.256 | 0.290 |
| **time_gap** | 0.691 | **0.691** | **0.201** | **1.000** | 0.568 |
| embedding_drift | 0.970 | 0.682 | 0.306 | 0.550 | 0.917 |
| surprise_em_llm | 0.846 | 0.724 | 0.194 | 0.646 | 0.834 |
| **hybrid_gap_emllm** | 0.846 | **0.799** | **0.130** | 0.699 | **0.939** |
| hybrid_gap_drift | 0.970 | 0.711 | 0.279 | 0.571 | 0.961 |

### Quality + cost (real model calls)

| approach | F1 | precision | recall | Pk | latency / session | cost / session |
|---|---|---|---|---|---|---|
| **llm_boundary (gpt-5.4, Azure)** | **0.871** | 0.822 | 0.933 | **0.080** | ~2.5 s | ~$0.009 |
| slm_boundary (llama3 8B, ollama, CPU) | 0.319 | 0.225 | 0.705 | 0.482 | ~338 s | $0 (on-prem) |
| time_gap | 0.691 | 1.000 | 0.568 | 0.201 | ~0.03 ms | $0 |
| hybrid_gap_emllm | 0.799 | 0.699 | 0.939 | 0.130 | ~20 ms | $0 |

(SLM row is 3 seeds; all others 5. Both LLM/SLM read the raw turn *text*, so their
numbers are directly comparable to the real-embedding semantic methods.)

### Reading of the results

1. **The two naive baselines bracket the problem.** `single_session` never
   segments (F1 0, Pk 0.465); `per_turn` over-segments to death (precision 0.088,
   WindowDiff 1.0). Any real method must beat both — most do.
2. **`fixed_window` is the weakest real option** (F1 0.27). Turn-count windows cut
   blindly through the middle of episodes; the issue's assumption that a fixed
   turn window is a fine v1 is **not** supported — the time-gap variant is far
   better for the same (zero) cost.
3. **`time_gap` is a deterministic, zero-cost workhorse with precision 1.0.** Every
   boundary it emits is real (a genuine idle gap never occurs mid-topic), but it
   **misses topic switches with no pause** (recall 0.57). Crucially, it is the
   **only method whose quality is identical under synthetic and real embeddings**
   — because it does not use embeddings at all. That determinism is why it is the
   v1 backbone.
4. **Pure semantic drift is fragile.** `embedding_drift` is near-perfect on clean
   synthetic vectors (F1 0.970) but **collapses to 0.682 on real embeddings** —
   real short conversational turns are not tightly clustered, so TextTiling fires
   spurious boundaries (precision 0.55, over-segments to 17 vs ~11). *A method that
   only looks strong on synthetic data is a trap;* the real-embedding run is what
   we trust.
5. **EM-LLM is the most robust semantic method.** `surprise_em_llm` degrades least
   under real embeddings (F1 0.846 → 0.724) and has the best Pk of the standalone
   semantic methods (0.194). The **graph-refinement pass measurably helps**: Pk
   improves 0.136 → 0.096 when refinement is on (`results/sensitivity.md`,
   `em_llm.refine`). This validates implementing the full EM-LLM (surprise +
   modularity/conductance), not just a surprise threshold.
6. **The hybrid is the best no-cost option.** `hybrid_gap_emllm` fuses time-gap's
   perfect-precision anchors with EM-LLM's recall: under real embeddings F1 0.799
   and **Pk 0.130 — the best of every local method**, recall 0.939. It inherits
   time-gap's precise cuts and recovers the no-pause switches EM-LLM finds.
7. **The LLM is the quality ceiling, at a price.** `gpt-5.4` (labelled `gpt-5.5`)
   reads the raw text and scores F1 0.871 / Pk 0.080 — higher than any local
   method on realistic signal — but at ~2.5 s and ~$0.009 per session, and it is
   non-deterministic. Worth it only for high-value sessions on an async lane.
8. **The on-prem SLM is not viable inline.** llama3 8B on CPU took **~338 s per
   session** (a single ~1.6k-token prompt) and scored **poorly (F1 0.319, below
   even `time_gap`)** — it over-segments and drifts off the true boundaries. Only
   workable if batched on a GPU with a stronger model. Documented so SM-R1 can
   weigh an on-prem GPU deployment vs the cloud LLM.

### Sensitivity + robustness (`results/sensitivity.md`, `results/robustness.md`)

- **`time_gap_seconds`:** flat for ≥300 s (the "idle-gap-only" regime). The
  within-session message cadence tops out ~180 s, so a 600–900 s threshold sits
  safely above the noise floor and guarantees precision 1.0. Dropping toward 180 s
  raises recall but starts to clip normal pauses — hence the conservative default.
- **EM-LLM `gamma`:** F1/Pk are flat across 0.5–1.5 — the surprise distribution
  has a clean gap, so the decision is robust to the threshold. The active lever is
  the **graph refinement**, not gamma.
- **EM-LLM objective:** modularity gives better Pk (0.096 vs 0.125) than
  conductance; conductance edges F1 slightly. We pick **modularity** (Pk/WindowDiff
  are the segmentation-standard metrics we optimise).
- **Robustness:** as noise rises (more backchannel/drift, fewer pause-marked
  boundaries), `embedding_drift` degrades gracefully in F1 but its Pk balloons;
  `surprise_em_llm` over-segments (13.8 → 20 episodes); `time_gap` degrades
  smoothly and never loses precision. The hybrid is the most stable useful method.

---

## 4. Integration with write-back (SM-E2) — inline vs async

Per the acceptance criterion, segmentation runs in **two lanes**:

- **Inline, on write-back (v1).** When a run completes, `time_gap` +
  `max_turns_per_episode` is evaluated on the just-appended events. It is O(1) per
  event (a timestamp comparison and a turn counter), fully deterministic, and adds
  no model latency to the request path. It decides the *provisional* episode
  boundary that closes the current episode and opens the next. This is the only
  segmentation the synchronous write-back path performs.
- **Async, on the consolidation lane (v2 + premium).** The event queue feeds the
  consolidation worker (SM-R7). There, over each provisional episode's embeddings,
  the **EM-LLM hybrid** re-segments (it needs the whole episode's embeddings and a
  graph pass — too heavy for inline), and, for flagged sessions, the **LLM** pass
  runs. Async re-segmentation may split or merge the provisional inline episodes
  before they are consolidated/committed.

Rationale: the inline path must be cheap and deterministic (it is on the user's
request path and must never depend on a model call); the richer, embedding- and
graph-based methods belong on the lane that already pays for consolidation. This
matches finding #3/#7 (deterministic zero-cost inline; semantic/LLM async).

---

## 5. Boundary metadata to store on each episode

Per the acceptance criterion, every episode records how it was cut (so
consolidation, retrieval, and later re-segmentation can reason about provenance
and confidence):

| Field | Type | Meaning |
|---|---|---|
| `episode_id` | uuid | Episode identity. |
| `start_event_id` / `end_event_id` | id | Inclusive event span. |
| `start_ts` / `end_ts` | epoch s | Episode time span. |
| `boundary_method` | enum | `time_gap` \| `turn_cap` \| `em_llm` \| `hybrid` \| `llm` \| `manual`. Which rule *opened* this episode. |
| `boundary_trigger` | float | The triggering value: idle-gap seconds (time_gap), turn count (turn_cap), surprise σ-units (em_llm), or model confidence (llm). |
| `boundary_confidence` | float [0,1] | 1.0 for deterministic rules; model/graph confidence otherwise. Lets retrieval down-weight low-confidence cuts. |
| `refined` | bool | True if the async lane re-segmented the inline provisional boundary. |
| `segmenter_version` | str | Version/param-hash of the segmenter, for reproducibility and re-segmentation. |

`boundary_method` + `boundary_confidence` are the minimum SM-R7 needs to decide
whether to trust or re-cut a boundary before consolidation.

---

## 6. Honesty notes / provisional flags (for SM-R1)

- **Model id.** Code and this doc default to **`gpt-5.5`** via `OpenAIResponses`
  per repo policy; the *measured* numbers come from the available **gpt-5.4**
  Azure Responses deployment. Re-run against the production `gpt-5.5` deployment to
  ratify §1 costs/latency (unit prices here — $5/$15 per 1M in/out tokens — are
  provisional).
- **Absolute vs relative numbers.** The **candidate ranking** is what we trust; the
  synthetic benchmark isolates topic structure and the real-embedding run
  confirms the ranking survives real semantic noise. Absolute F1/Pk will shift on
  production data and the production embedding model — re-calibrate thresholds
  (SM-C).
- **Targets are provisional.** Suggested acceptance targets for the chosen v1+v2:
  boundary **precision ≥ 0.85**, **Pk ≤ 0.20** on a held-out labelled set. SM-R1
  ratifies.

---

## 7. Hand-off

- **SM-E3 (segmentation):** implement the tiered design — `time_gap` +
  `max_turns_per_episode` inline; `hybrid_gap_emllm` async. Promote the harness
  `segmenters.py` / `surprise.py` logic to `egpt/memory/` with **sync + async**
  public methods.
- **SM-E2 (write-back):** call the inline segmenter when appending events; persist
  the §5 boundary metadata.
- **SM-R7 (consolidation):** consume provisional episodes off the event queue,
  optionally re-segment with the async hybrid / LLM, then consolidate.
- **SM-R1 (#76):** ratify §1 parameters, §6 model/pricing, and §6 targets.
