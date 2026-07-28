# SM-R9 — dense baseline re-run on SM-R4's ratified embedder

Source: `results_20260728T104020Z_smR4ModeA.json` (+ `detail_*`). Addresses the PR #15
review finding that the original dense baseline ran on Azure `text-embedding-3-small`,
which is **neither** of SM-R4's ratified picks.

- **Embedder:** `local:BAAI/bge-base-en-v1.5@768` — SM-R4 **Mode A**
  (`benchmarks/memory_embedding/DECISION.md` §1). Credential-free, so this leg is
  reproducible on any host.
- **Mode:** `--retrieval-only` (retrieval metrics are scored against the dataset's gold
  titles and need no LLM; the reader does).
- Same eval set, same k=5, same 285-passage pooled corpus, same bootstrap CI procedure
  (2000 resamples, seed 7).

## Result — the corrected dense baseline does *not* improve

| metric | old: `text-embedding-3-small` | new: `bge-base-en-v1.5`@768 (SM-R4 Mode A) | Δ |
| --- | --- | --- | --- |
| recall@5 (overall) | 0.750 [.667, .833] | **0.742** [.650, .833] | −0.008 |
| all-hops-hit@5 (overall) | 0.467 [.300, .667] | **0.467** [.300, .633] | **0.000** |
| recall@5 (chaining-only) | 0.688 [.604, .781] | **0.677** [.594, .771] | −0.011 |
| all-hops-hit@5 (chaining-only) | 0.333 [.167, .542] | **0.333** [.167, .542] | **0.000** |

The two metrics the GO decision rests on — overall and chaining-only `all-hops-hit@5` —
are **identical to three decimal places**. Overall recall@5 moves by −0.008, well inside
the CI. So the reviewer's hypothesis (a stronger embedder lifts dense most on the
non-chaining questions, compressing the PPR margin and possibly flipping
pure-PPR-vs-hybrid) is **not supported for the dense leg**.

This is consistent with SM-R4's own eval, where the two models are a statistical tie on
the memory-representative set: `bge-base` 0.911 [.888, .934] vs `text-embedding-3-small`
0.898 (and vs `text-embedding-3-large` 0.900, Holm p=0.21 — n.s.). SM-R4's ratified pick
is a *deployment/licensing* choice (offline, MIT, 768-dim), not a quality upgrade — it
says so explicitly: "Mode B is now OPTIONAL, not a quality upgrade."

### Confound checked: BGE query instruction

`bge-*-v1.5` retrieval normally prepends `"Represent this sentence for searching
relevant passages: "` to queries. Omitting it could have understated the dense baseline.
Measured both ways — it makes things **worse** here, so the reported (no-prefix) numbers
are the *favourable* configuration for dense, not a handicap:

| config | recall@5 | all-hops@5 | chaining recall@5 | chaining all-hops@5 |
| --- | --- | --- | --- | --- |
| no prefix (as run) | 0.742 | 0.467 | 0.677 | 0.333 |
| with BGE query prefix | 0.725 | 0.433 | 0.656 | 0.292 |

### Numerical trust: the BLAS warning

macOS Accelerate raises spurious `divide by zero` / `overflow` / `invalid value`
RuntimeWarnings from the cosine matmul. Verified benign before trusting any number:
embedding matrix and similarity outputs are all-finite, row norms are exactly 1.0, and
the BLAS result matches an independent `np.einsum` path to **1.8e-15**. Now suppressed
narrowly in `harness/retrievers.py` with a hard `isfinite` assert behind it, so a *real*
non-finite result still fails loudly. (Same issue and disposition as SM-R8 `e2cd21d`.)

## What was NOT re-run, and why

**No LLM credentials exist on this host, and the OpenIE/LLM caches are git-ignored**
(`results/llm_cache/`, `results/embed_cache/`), so they could not be replayed from the
committed artifacts. That blocks:

| not re-run | why | what it would take |
| --- | --- | --- |
| `hippo` (PPR) | KG requires 285 live OpenIE LLM calls | Azure creds; ~$0.63, ~15 min |
| `hybrid` (RRF) | needs the hippo leg | as above |
| EM / F1 (all methods) | reader is an LLM call per question | as above |
| OpenIE triple precision/recall vs 2Wiki `evidences` | needs extracted triples | as above |

So the comparison in the table above is **asymmetric**: the dense leg is on SM-R4's
embedder, the `hippo`/`hybrid` numbers it is being compared against are still the
original `text-embedding-3-small` run. PPR's entity vectors use the *same* embedder
(synonymy edges + query→node linking), so its arm would move too, in an undetermined
direction. **The PPR margin reported in SPIKE.md is therefore not yet fully re-validated
— only the reviewer's specific "weak dense baseline" concern is retired.**

Two reasons to expect the PPR arm to move more than the dense arm did, both of which
make the full re-run necessary rather than a formality:

1. `synonym_tau=0.85` and `link_threshold=0.5` are **absolute cosine thresholds tuned on
   `text-embedding-3-small`**. Measured on this corpus under `bge-base`@768 (285 passage
   titles as a proxy entity-name set, 40,470 pairs): median off-diagonal cosine 0.379,
   p95 0.483, p99 0.553, max 0.934. At tau=0.85 only **12 pairs (0.03%)** qualify. A
   threshold sitting that far into the tail is highly sensitive to the embedder's cosine
   scale, so the synonymy-edge set — and hence the graph topology PPR ranks over —
   is not portable across embedders. Both values need re-tuning, not inheriting.
2. Dimension changes 1536 → 768 (Mode A) or → 1024 (Mode B), which also shifts the
   entity-linking neighbourhood.

## Reproduce

```bash
cd benchmarks/memory_multihop
PYTHONPATH=. python run.py --methods vector --k 5 --retrieval-only --tag smR4ModeA
```

Needs no credentials: SM-R4 Mode A is a local sentence-transformers model. The full
three-method comparison additionally needs Azure creds in `.env` (see `.env.example`).
