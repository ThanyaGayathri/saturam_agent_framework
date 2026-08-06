# SM-R6 — Decision: Event-Segmentation Approach

**Status:** Proposed (decision task). No framework/core code changed here; this
records the chosen approach + measured rationale for the implementation tasks
(SM-E3 segmentation, SM-E2 write-back) and the downstream consolidation lane
(SM-R7).
**Depends on:** SM-R1 (#76) — every numeric target/threshold below is
**PROVISIONAL** and flagged for SM-R1 to ratify.
**Method:** every option was implemented inside `harness/` and measured on a
reproducible benchmark (see [README.md](README.md)) across **four conditions**:
synthetic vectors, real text embeddings, a **hardened** synthetic stream
(gradual/overlapping boundaries + mid-topic pauses), and a **real, independent,
human-annotated dialogue dataset (TIAGE)** — plus a **real gpt-5.4 (Azure
Responses)** run and a **real on-prem SLM (llama3 8B)** run. All means are
reported with standard deviation. We do **not** decide from cited literature.

> **Revision note (post-review, PR #9):** an earlier draft recommended
> `hybrid_gap_emllm` for v2 and leaned on `time_gap` precision = 1.0. Reviewer
> `paranthaman-commits` correctly flagged both as artifacts of how the synthetic
> stream was built. After adding boundary/pause hardening, seed-variance, and the
> real TIAGE dataset, **the hybrid is withdrawn and the `time_gap` precision claim
> is corrected** (see §3 and §8). The tiered *shape* survives; the v2 pick changed.

---

## 1. Decision (TL;DR)

Adopt a **tiered segmentation** design. What changed after hardening: the cheap
methods are much weaker on realistic data than the idealised synthetic run
suggested, so the LLM is elevated from "premium only" to the **quality default on
the async lane**, and the deterministic rule is reframed as a cheap *prior*, not a
precise oracle.

- **v1 — `time_gap` + turn-count cap, inline on write-back (deterministic,
  zero-cost).** A cheap first cut: start a new episode after a long idle gap, and
  hard-cap episode length. **Not** relied on for precision — its precision is a
  property of the gap distribution (1.0 when long gaps only occur at real
  boundaries, but **0.31 once users pause mid-topic**; §3). Its job is a cheap,
  deterministic pre-segmentation that the async lane refines.

- **v2 — a semantic re-segmenter on the async consolidation lane (no API cost).**
  Default **`embedding_drift`** (TextTiling), which is the best no-API method on
  the **real** dataset; **`surprise_em_llm`** (EM-LLM: proxy surprise + modularity
  graph refinement) is the close alternative that is more robust on structured
  synthetic data and should be re-tested with a real token-NLL surprise signal
  before it is preferred. Pick between them by tuning on the production embedding
  model with this harness. **The hybrid is withdrawn** — it depended on `time_gap`
  precision and collapses under realistic gaps (§3).

- **Quality default / premium — `llm_boundary` with `gpt-5.5` via
  `OpenAIResponses`, async.** The **only** method that stays strong on both
  hardened-synthetic (F1 0.88) and real (F1 0.62) data; ~1.6–2.5 s and
  ~$0.002–0.009 per session, non-deterministic. Recommended wherever segmentation
  quality matters; the cheap v2 methods are the cost-saving fallback.

**Provisional default parameters** (SM-R1 to ratify):

| Parameter | Value | Notes |
|---|---|---|
| `time_gap_seconds` | **900 s** (15 min) | Cheap prior; precision is gap-model-dependent (see §3/§8), not guaranteed. |
| `max_turns_per_episode` | **40** | Hard cap so a long unbroken session still segments. |
| `min_turns_per_episode` | **3** | Suppress micro-episodes / backchannel splits. |
| `embedding_drift` `threshold_c` | **0.75** | Depth-score threshold (σ units); best F1/Pk in the sweep. |
| `embedding_drift` `block` | **3** turns | TextTiling block window. |
| EM-LLM `gamma` | **1.0** (σ) | Surprise threshold; flat over 0.5–1.5 (robust). |
| EM-LLM `objective` / `refine_radius` | **modularity** / **3** | Refinement objective + search window. |
| LLM model | **`gpt-5.5`** (`OpenAIResponses`) | Measured on a gpt-5.4 Azure deployment; see §6. |

---

## Acceptance criteria (issue #81)

- [x] **Segmentation method chosen for v1** (with a v2 path if surprise-based is deferred)

      v1 = `time_gap` + turn-count cap (deterministic, inline). v2 = async semantic
      re-segmenter (`embedding_drift`, EM-LLM alternative). Surprise-based (EM-LLM)
      is implemented and measured, not deferred. See §1, §3.

- [x] **Cost/quality trade-off documented**

      F1/precision/recall/Pk/WindowDiff with variance AND latency + $/session for
      every option, across 4 data conditions incl. a real dataset, a real gpt-5.4
      run and a real SLM run; sensitivity + robustness sweeps. See §3, `results/`.

- [x] **Integration point with write-back (SM-E2) defined** (inline vs async)

      Inline deterministic prior; async semantic/LLM re-segmentation, with an
      explicit **episode-identity contract** for async re-cuts. See §4.

- [x] **Boundary metadata to store on each episode specified**

      Field table incl. method/trigger/confidence + the supersede/revision fields
      the async identity contract needs. See §5.

Open questions in the issue body:

- [x] **Turn/session vs LLM-decided vs surprise-based (EM-LLM)?** — all implemented and
  measured across 4 conditions; tiered choice (deterministic prior v1, semantic v2, LLM quality default). §1, §3.
- [x] **Cost vs quality per method?** — quantified with variance: EM-LLM/drift ~1–45 ms/$0;
  LLM ~1.6–2.5 s/$0.002–0.009; SLM ~338 s/$0. §3.
- [x] **Sub-session events, or are turn/session boundaries enough for v1?** — turn/time-gap is
  a cheap v1 prior but **not sufficient alone** (precision degrades on real pauses); sub-session
  semantic/LLM re-segmentation is needed for quality. §1, §3, §4.
- [x] **Inline on write-back or async consolidation?** — both, split by tier, with the identity contract in §4.

All numeric thresholds/targets are **provisional** pending SM-R1 (#76) — see §6.

---

## 2. Options evaluated (all implemented)

| # | Approach | Signal | Cost | Disposition |
|---|---|---|---|---|
| 1 | `single_session` | none (1 episode) | ~0 | baseline (under-segment) |
| 2 | `per_turn` | none (every turn) | ~0 | baseline (over-segment) |
| 3 | `fixed_window` | turn count | ~0 | rejected (weakest real option) |
| 4 | `time_gap` | message timestamps | ~0 | **v1 prior** (not a precise oracle) |
| 5 | `embedding_drift` | TextTiling on embeddings | embedding | **v2 default** |
| 6 | `surprise_em_llm` | EM-LLM: proxy surprise + modularity/conductance graph refine | embedding | **v2 alternative** |
| 7 | `llm_boundary` | LLM topic-shift call (`gpt-5.5` / on-prem SLM) | API / GPU | **quality default** (LLM); SLM not viable on CPU |
| 8 | `hybrid_gap_*` | (4) ∪ (5 or 6) | embedding | **withdrawn** (collapses under realistic gaps) |

---

## 3. Why — measured trade-off

Boundary tolerance ±1 turn. **Higher F1/precision/recall is better; lower
Pk/WindowDiff is better.** F1 shown as mean±sd. Synthetic runs average 5 seeds
(~100 events / ~11 episodes each); TIAGE averages 100 real dialogues (~16 turns /
~3 boundaries each). `results/` holds the full tables.

### F1 across conditions (the headline)

| approach | syn-vec | syn-vec **hard** | real-emb | real-emb **hard** | **TIAGE (real)** |
|---|---|---|---|---|---|
| single_session | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| per_turn | 0.161 | 0.161 | 0.161 | 0.161 | 0.343±0.14 |
| fixed_window | 0.271 | 0.271 | 0.271 | 0.271 | 0.252±0.25 |
| time_gap | 0.691±0.20 | **0.425±0.06** | 0.691±0.20 | **0.425±0.06** | n/a (no timestamps) |
| embedding_drift | 0.970±0.04 | 0.950±0.06 | 0.682±0.09 | 0.682±0.09 | **0.537±0.26** |
| surprise_em_llm | 0.846±0.03 | 0.799±0.06 | 0.724±0.02 | 0.724±0.02 | 0.368±0.30 |
| hybrid_gap_emllm | 0.846±0.03 | 0.487±0.04 | 0.799±0.05 | **0.503±0.03** | n/a |
| hybrid_gap_drift | 0.970±0.04 | 0.529±0.04 | 0.711±0.09 | 0.470±0.06 | n/a |
| **llm_boundary (gpt-5.4)** | – | – | **0.877±0.07** | **0.876±0.05** | **0.622±0.28** |
| slm_boundary (llama3 8B, CPU) | – | – | 0.335±0.08 | – | – |

_"hard" = gradual/overlapping boundaries + mid-topic long pauses. Under real
embeddings the gradual-boundary vector blend is overwritten by real text
embeddings, so `embedding_drift`/`em_llm` are unchanged there and the boundary
hardening for semantic methods is carried by **TIAGE's real elliptical text**; the
pause hardening still hits the timestamp methods (`time_gap`, hybrids)._

### Cost / latency (real model calls)

| approach | latency / session | cost / session | note |
|---|---|---|---|
| time_gap / fixed_window | ~0.03 ms | $0 | deterministic |
| embedding_drift | ~2–11 ms | $0 | local embedding |
| surprise_em_llm | ~1–45 ms | $0 | local embedding + graph |
| **llm_boundary (gpt-5.4)** | ~1.6–2.5 s | ~$0.002–0.009 | Azure Responses |
| slm_boundary (llama3 8B, CPU) | ~340–440 s | $0 (on-prem) | worst-case config; F1 0.335 (§6) |

### Reading of the results

1. **Naive baselines bracket the problem** and **`fixed_window` is the weakest real
   option** (F1 0.25–0.27 everywhere) — turn-count windows cut through the middle
   of episodes.
2. **`time_gap` precision is a property of the gap model, not the method.** On the
   base stream (long gaps only at real boundaries) precision is 1.0; once we inject
   **mid-topic long pauses** (the "hard" config — users pausing for meetings /
   overnight), precision falls to **0.308** and F1 to 0.425. On the real TIAGE
   dialogues it cannot even run (no timestamps). So `time_gap` is a cheap *prior*,
   not the precise backbone the first draft claimed. *(This corrects review #2.)*
3. **Pure semantic drift is fragile but wins on real data.** `embedding_drift` is
   near-perfect on clean synthetic vectors (0.970) yet drops to 0.682 on real
   synthetic-text embeddings and **0.537 on real TIAGE** — but that is still the
   **best no-API method on the real dataset** (vs EM-LLM 0.368; the 0.17 gap is
   ~5σ over n=100, not noise). The synthetic-vector 0.970 is a trap; trust the real
   numbers.
4. **EM-LLM is the most robust on structured data, weaker on real elliptical
   turns.** `surprise_em_llm` holds 0.724 under synthetic hardening (best no-API
   there) but drops to **0.368 on TIAGE**, below `embedding_drift`. Its surprise
   signal is an **embedding-cosine proxy, not token NLL** (§6) — the production
   signal may rank differently, so EM-LLM is the *alternative*, to re-test with a
   real surprise signal, not the default. *(Addresses review #6.)*
5. **The hybrid is withdrawn.** `hybrid_gap_emllm` looked best under real
   embeddings on the base stream (0.799) **only because it inherited `time_gap`'s
   perfect-precision anchors**. Under realistic gaps those anchors become noisy and
   the hybrid **collapses to 0.503** (Pk 0.130 → 0.377), worse than standalone
   EM-LLM. A method whose advantage evaporates the moment the gap model is
   realistic cannot be the v2 recommendation. *(Directly from reviews #1/#2.)*
6. **The LLM is the only robustly strong method.** `gpt-5.4` scores 0.877 / 0.876
   on synthetic default/hard (essentially **immune to the hardening** — it reads
   text, not timestamps or blended vectors) and **0.622 on real TIAGE — the best on
   every realistic condition**. Its lead over the cheap methods *widens* exactly
   where they are weakest (real data). Cost ~$0.002–0.009 and ~1.6–2.5 s per
   session on the async lane. Given how much the cheap methods degrade on real
   data, the LLM is the **quality default**, not a luxury.
7. **The on-prem SLM was not viable in this config, but that is a worst-case, not a
   verdict on Mode A** — see §6 and review #5.

### Statistical honesty

Margins on the real dataset are wide (sd 0.14–0.30, n=100 → SE ≈ 0.014–0.030).
The `embedding_drift` > `em_llm` gap on TIAGE (0.537 vs 0.368) is ~5 SE and real;
the LLM > `embedding_drift` gap (0.622 vs 0.537) is ~2.5 SE. On synthetic the sd
is small (0.02–0.10 over 5 seeds). Where two options sit within ~1 SE we call it a
tie rather than rank them.

### Sensitivity + robustness (`results/sensitivity.md`, `results/robustness.md`)

- **EM-LLM graph refinement measurably helps** (Pk 0.136 → 0.096 refine off→on);
  `gamma` is flat over 0.5–1.5; `modularity` beats `conductance` on Pk. So the full
  EM-LLM (surprise + graph refine) is worth implementing over a bare threshold —
  with the token-NLL caveat in §6.
- **`embedding_drift` `threshold_c`** best at 0.75 (F1 0.978 / Pk 0.015 on synthetic).
- **Robustness sweep:** as backchannel/drift rise, `embedding_drift` and `em_llm`
  over-segment; the previously-touted hybrid stability does **not** hold once the
  time-gap anchors are themselves noisy (the hard config).

---

## 4. Integration with write-back (SM-E2) — inline vs async

Segmentation runs in **two lanes**:

- **Inline, on write-back (v1 prior).** On run completion, `time_gap` +
  `max_turns_per_episode` is evaluated on the just-appended events — O(1), no model
  call, no request-path latency. It assigns a **provisional** episode boundary.
- **Async, on the consolidation lane (v2 + LLM).** The event queue feeds the SM-R7
  worker, which re-segments each provisional episode with the semantic
  re-segmenter (`embedding_drift`/EM-LLM over the episode's embeddings) and, where
  quality matters, the **LLM**. This lane may split or merge the provisional
  inline episodes.

Rationale: the inline path must be cheap and deterministic; the accurate methods
(embedding/graph/LLM) are too heavy for the request path and belong where
consolidation already pays for compute. The measured gap between the inline prior
(time_gap 0.43 F1 hard) and the async LLM (0.88) is the whole reason the async
lane is not optional for quality.

### Episode-identity & stability contract (async re-segmentation)

Because async re-cutting changes episodes that write-back already committed, the
identity rule is **stable id + supersede link** (invalidate-not-delete, consistent
with the bi-temporal design in the research doc and SM-R10):

1. Inline write-back assigns each provisional episode a stable `episode_id` and
   `revision = 0`.
2. When the async lane re-segments, it **does not mutate** the old episode in
   place. It writes the new episode(s) with **new `episode_id`s**, `revision = n+1`,
   and `supersedes = [old_ids]`; the old episode is marked `superseded_by =
   [new_ids]` and retained (not deleted) for point-in-time reconstruction.
3. Downstream references (consolidated semantic memories, retrieval pointers) are
   resolved **through the supersede chain**; SM-R7 re-points or re-consolidates any
   content derived from a superseded episode. `refined = true` records that a re-cut
   happened; `supersedes`/`superseded_by` record the identity graph.

This gives SM-R7 and retrieval a deterministic rule for "what happened to
`episode_id` X when its boundary was re-cut." *(Resolves review #7.)*

---

## 5. Boundary metadata to store on each episode

| Field | Type | Meaning |
|---|---|---|
| `episode_id` | uuid | Episode identity (stable). |
| `revision` | int | 0 inline; incremented on async re-cut. |
| `supersedes` / `superseded_by` | uuid[] | Async re-segmentation identity graph (§4). |
| `start_event_id` / `end_event_id` | id | Inclusive event span. |
| `start_ts` / `end_ts` | epoch s | Episode time span. |
| `boundary_method` | enum | `time_gap` \| `turn_cap` \| `embedding_drift` \| `em_llm` \| `llm` \| `manual`. Which rule opened this episode. |
| `boundary_trigger` | float | Idle-gap seconds (time_gap), turn count (turn_cap), depth/surprise σ-units (semantic), or model confidence (llm). |
| `boundary_confidence` | float [0,1] | 1.0 for deterministic rules; model/graph confidence otherwise. Lets retrieval down-weight low-confidence cuts. |
| `refined` | bool | True if the async lane re-segmented the inline provisional boundary. |
| `segmenter_version` | str | Version/param-hash of the segmenter, for reproducibility and re-segmentation. |

---

## 6. Honesty notes / provisional flags (for SM-R1)

- **Model id.** Code/doc default to **`gpt-5.5`** via `OpenAIResponses` per repo
  policy; measured numbers come from the available **gpt-5.4** Azure Responses
  deployment. Re-measure on production `gpt-5.5` (unit prices $5/$15 per 1M in/out
  are provisional).
- **EM-LLM surprise is a proxy.** `harness/surprise.py` uses embedding-cosine
  Bayesian surprise, **not** the paper's token-level NLL (we have no offline token
  NLL). So the benchmark validates "a surprise proxy + graph refinement," and the
  production surprise signal may rank EM-LLM differently — hence EM-LLM is the v2
  *alternative*, gated on a token-NLL re-test. *(Review #6.)*
- **On-prem SLM verdict is worst-case, not a Mode-A veto.** The measured F1
  **0.335±0.08** at ~340–440 s/session (5 seeds) is one setup: **llama3-8B, CPU, a
  single ~1.6k-token whole-conversation prompt** — the least favourable
  configuration. Segmentation is a cheap structured task a small model can do well
  with a **windowed prompt and a GPU**. Do **not** foreclose the Mode-A on-prem
  path on this; SM-R1 should commission a GPU + per-window (and/or lightly
  fine-tuned) SLM re-test before concluding SLMs are unfit. *(Review #5.)*
- **External validity.** TIAGE (real, human-annotated, elliptical PersonaChat
  turns) is an independent cross-check, but it is still not *our* multi-tenant
  production chat logs. Absolute numbers will move on production data + the
  production embedding model; re-calibrate thresholds in SM-C. The synthetic
  "hard" config stresses timestamp methods but only mildly stresses semantic
  methods (real text embeddings overwrite the vector blend), so TIAGE carries the
  semantic external-validity weight.
- **Provisional targets.** Suggested acceptance on a held-out **real** labelled
  set: boundary **F1 ≥ 0.70** and **Pk ≤ 0.35**. On TIAGE **only the LLM meets
  this** (F1 0.62 is close; cheap methods 0.25–0.54 do not) — evidence that quality
  segmentation needs the async LLM (or a fine-tuned SLM), with the cheap methods as
  a fallback. SM-R1 ratifies the targets.

---

## 7. Hand-off

- **SM-E3 (segmentation):** implement the tiered design — `time_gap` +
  `max_turns_per_episode` inline; `embedding_drift` (default) / EM-LLM (alt) + LLM
  async. Promote the harness `segmenters.py` / `surprise.py` logic to
  `egpt/memory/` with **sync + async** public methods. Do **not** ship the hybrid.
- **SM-E2 (write-back):** call the inline prior when appending events; persist the
  §5 boundary metadata incl. the identity fields.
- **SM-R7 (consolidation):** consume provisional episodes, re-segment with the
  async semantic/LLM pass, apply the §4 supersede/revision identity contract, then
  consolidate.
- **SM-R1 (#76):** ratify §1 parameters, §6 model/pricing/targets, and commission
  the SLM GPU re-test (§6) + a validation on production chat logs.

---

## 8. Response to review (PR #9, `paranthaman-commits`)

| # | Sev | Reviewer point | Resolution |
|---|---|---|---|
| 1 | Major | Noise only on interior events → boundaries maximally detectable | Added gradual/overlapping-boundary blending (`gradual_shift_turns`) **and** the real TIAGE dataset. Semantic-method F1 on real elliptical text drops to 0.37–0.54; ranking re-reported (§3). |
| 2 | Major | `time_gap` precision 1.0 is a gap-model tautology | Added mid-topic long pauses (`mid_topic_pause_rate`). Precision falls **1.0 → 0.31**; `time_gap` reframed as a cheap prior, not a precise backbone (§1/§3). |
| 3 | Major | Plain 5-seed means, no variance; LLM 5 vs SLM 3 seeds; llm/slm files score offline peers on synthetic vectors | All tables now **mean±sd**; SLM re-run at **5 seeds**; llm/slm runs regenerated with `--relevance embedding` (no synthetic-vector peers in the results files); added a statistical-honesty note (§3). |
| 4 | Major | "Real embeddings" still idealised self-contained sentences | Added **TIAGE** (real, elliptical, human-annotated turns) as the trusted external cross-check; §6 states the synthetic-hard config only mildly stresses semantic methods and TIAGE carries the weight. |
| 5 | Minor | "SLM not viable" over-generalised; could foreclose Mode A | Reframed as a worst-case (CPU / 8B / whole-conversation) result; **do not foreclose Mode A**; SM-R1 to commission a GPU/windowed SLM re-test (§6). |
| 6 | Minor | "Full EM-LLM" overstates — surprise is an embedding proxy | Proxy caveat carried into the prose; EM-LLM demoted to v2 *alternative* pending a token-NLL re-test (§3/§6). |
| 7 | Minor | Async re-segmentation identity/stability contract unspecified | Added the **stable-id + supersede/revision** contract and metadata fields (§4/§5). |

**Net effect on the decision:** the tiered shape stands, but the hybrid is
withdrawn, `time_gap` is demoted to a prior, EM-LLM becomes an alternative to
`embedding_drift`, and the **LLM is elevated to the quality default** — all driven
by the new measurements, not prose.
