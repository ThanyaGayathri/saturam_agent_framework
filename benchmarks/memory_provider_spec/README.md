# memory_provider_spec (SM-R11)

Design/Spec deliverable for **SM-R11 — Design: MemoryProvider interface spec**
([issue #86](https://github.com/Saturam-Inc/aicoe-tracker/issues/86)). Specifies the
`MemoryProvider` seam that **SM-C1** implements, and proves it is plug-and-play with two
runnable adapters and a config-only swap test.

This is a decision artifact, **not** framework core. Nothing here imports or mutates `egpt`
(`libs/agno/egpt/**`); seed surfaces are referenced by name only.

## Contents

| File | What it is |
|------|-----------|
| [`SPEC.md`](SPEC.md) | The specification: signatures, types, semantics, reuse dispositions, couplings. Read this first. |
| [`interface.py`](interface.py) | The runnable contract — `MemoryProvider` ABC, `Scope`, tiers/substrates, data model, config seam + registry. Dependency-free. |
| [`adapters.py`](adapters.py) | Two adapters: `co_located` (Mode A, functional, composes vector+graph substrate ports) and `external_stub` (Mode B). Both register via config. |
| [`swap_test.py`](swap_test.py) | Proves the seam: one consumer function drives both providers; backend swap is a one-line config change. Deterministic/offline. |
| [`findings/`](findings/) | Design findings and bugs caught while prototyping the seam. |
| [`results/`](results/) | Committed deterministic output of the swap test. |
| [`TEST_LOG.md`](TEST_LOG.md) | Test run log. |

## Run it

No external services, no API keys, no network — pure stdlib, deterministic.

```bash
# Seam summary (tiers, substrates, modes, methods)
python -m benchmarks.memory_provider_spec.interface

# Swap test (exit 0 = pass); writes results/swap_results.json
python -m benchmarks.memory_provider_spec.swap_test
```

## The seam in one look

```
consumers ── depend on ──> MemoryProvider (single seam)
                              read / write / consolidate (+ tier-aware), sync + async
                              owns hybrid fusion (RRF, k=60)
                                   │ composes
                        ┌──────────┴──────────┐
                  VectorSubstrate        GraphSubstrate
                  (SM-R3 backend)        (SM-R2 / SM-G1 backend, bi-temporal SM-R10)
```

Swapping **Mode A ↔ Mode B**, or a vector/graph backend, is editing
`MemoryProviderConfig` (or its source file) — call sites never change. See `SPEC.md` §5–§7.

## Status

Provisional pending **SM-R1 #76** (ADR) ratification; data-model types (`MemorySlice` etc.) are
referenced from **SM-R12 #87** and shaped provisionally here (non-authoritative).
