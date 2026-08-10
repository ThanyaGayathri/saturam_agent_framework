# Finding 01 — Data quality & licensing (Phase 1)

This note records the data-quality bar (skill §5) for the memory eval harness and
the licensing stance behind the "fetch on demand, never vendor" design.

## Benchmarks chosen and what each measures

| Benchmark | What it measures | Role here | Turn-level evidence? |
|---|---|---|---|
| **LongMemEval** | 5 long-term abilities: info extraction, multi-session, temporal reasoning, knowledge update, abstention | **Headline** (most discriminative per research) | Session-level `answer_session_ids` + per-turn `has_answer` |
| **LOCOMO** | Multi-session dialogue QA: single-hop, multi-hop, temporal, open-domain, adversarial | **External validity** for QA + retrieval | Yes — `evidence` = `dia_id`s |
| **DMR** | Retrieve one fact from a prior session | **Sanity baseline only** — near-saturated, low value alone (issue #88) | Thin/none — degrades gracefully |
| **Custom temporal/decay** | Bi-temporal "true at T" + episodic decay | Covers behavior the public sets do **not** isolate | Yes — authored with evidence + decay targets |

## Data-quality bar (§5) — honest assessment

- **Representative:** LongMemEval + LOCOMO are established, independently-authored,
  multi-session conversational sets — the right workload for episodic memory. Good.
- **Not circular / not single-author:** the two headline sets are third-party. The
  **custom temporal/decay set is SINGLE-AUTHORED** — an explicit caveat. It is a
  *targeted probe* for `as_of_t`/`decay`, cross-checked against LongMemEval's
  `temporal-reasoning` + `knowledge-update` categories; it is never used as the sole
  signal for a decision. SM-R1 should ratify whether it needs an independent labeler.
- **Graded where it matters:** QA correctness uses a graded LLM judge (numeric 1-10),
  not only binary (Phase 2).
- **Externally cross-checked:** temporal accuracy is reported on both LongMemEval and
  LOCOMO temporal slices *and* the custom set, so no single set is decisive.
- **Sufficient sample:** the committed fixtures are tiny (smoke-test sized) and are
  **not** for ranking anything — full runs download the real corpora. Any ranking
  claim must state n and a confidence interval (Phase 2 `stats`).

## Licensing stance: fetch on demand, never vendor

The real corpora are **never committed**. Each loader has a `download()` that fetches
into a **gitignored** `data/<name>/raw/` dir, and `build()` parses either that file or a
tiny committed **raw sample**. Only the small, **synthetic, format-faithful** fixtures
under `fixtures/` are committed — they carry no third-party content, so there is no
redistribution question.

| Benchmark | Upstream | Redistribution here |
|---|---|---|
| LongMemEval | github.com/xiaowu0162/LongMemEval (data via HuggingFace/Drive) | None — fetched on demand |
| LOCOMO | github.com/snap-research/locomo | None — fetched on demand |
| DMR | MemGPT/Letta (Apache-2.0 upstream) | None — fetched on demand |
| Custom | Authored in-house | Committed (in-house, freely usable) |

**Action for full runs:** before a large run, confirm the current upstream license for
each set (they can change) and that the fetch URL still resolves. DMR has no single
canonical URL — set `DMR_URL`/`DMR_JSON` to the file from the MemGPT/Letta repo.

## Open items carried forward

- **DMR raw schema** varies across MemGPT/Letta releases; `dmr.build` targets a lenient
  documented shape and raises loudly if it does not match. Verify field names against the
  fetched file and adjust `dmr._CTX_KEYS` (tracked here, not silently absorbed).
- **Research doc** `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` was not accessible
  in this worktree (the `docs/` symlink target is absent). Grounding currently leans on the
  public benchmark sources + the sibling `benchmarks/memory_consolidation` harness. Fold in
  the doc's exact targets/citations once the symlink is restored.
