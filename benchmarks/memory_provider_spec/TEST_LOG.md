# TEST_LOG — memory_provider_spec (SM-R11)

Environment: system `python3` (the artifact is pure stdlib — no `egpt`, no external services,
no network, no API keys). Deterministic.

### interface.py

**Status:** PASS

**Description:** `python -m benchmarks.memory_provider_spec.interface` prints the seam summary
(4 tiers, 2 substrates, 2 modes, 5 core method pairs). Verified: `MemoryProvider` is a genuine
ABC (not instantiable); `Scope.key()`, `MemorySlice.as_context()` work; substrate ports are
`runtime_checkable` Protocols; empty/unknown registry lookups raise clear errors.

**Result:** Contract imports and runs; all semantic spot-checks pass.

---

### swap_test.py

**Status:** PASS (7/7 checks, exit 0)

**Description:** One consumer function (`consumer_workload`) driven verbatim against two
providers built purely from config. Checks:
1. `co_located` — write+read+consolidate via one seam.
2. `external_stub` — same, via the same seam.
3. Swap is config-only (no call-site change).
4. `co_located` — RRF folds a cross-substrate match to one record (vector_hits=1, graph_hits=1).
5. Tenant isolation — a second tenant sees nothing.
6. Bi-temporal (SM-R10) — invalidate-not-delete + `as_of` point-in-time read (before=1,
   as_of_after=0, as_of_before=1).
7. Sync + async variants both satisfy the contract.

**Result:** All pass. Output is byte-identical across repeated runs
(`results/swap_results.json`), confirming determinism.

---

### Bugs caught & fixed during prototyping

Three seam bugs surfaced and were fixed (see `findings/README.md`): F1 shared episode id across
substrates, F2 point-in-time validity owned at the fused read path, F3 consistent `tiers`
filtering across substrates. Each is recorded as a contract requirement for SM-C1.

---

### Lint / type / core-isolation gate

**Status:** PASS

- `ruff format --check` (repo config, line-length 120, py39): **clean** on all `.py` files.
- `ruff check` at the repo's effective default select `E4,E7,E9,F` (the repo pins ruff 0.15.20
  and sets no explicit `select`): **clean**. Verified parity — core `learn/stores/protocol.py`
  passes the identical command. (My tooling is ruff 0.16.0, whose newer default adds `UP` rules
  that core itself does not satisfy; those are a version artifact, not repo policy, so the seam
  keeps `typing.Dict`/`List` to match core idiom.)
- `mypy --check-untyped-defs --no-implicit-optional --python-version 3.9`: **Success, no issues**
  in `interface.py`, `adapters.py`, `swap_test.py`.
- **Core untouched:** `git diff origin/main -- libs/` is **empty** (0 lines) — the definitive
  guarantee. `./scripts/format.sh` / `validate.sh` scan only `libs/*` + `cookbook` (not
  `benchmarks/`) and require the pinned `.venv`; the in-scope files here are therefore linted
  directly (above), and core is proven unchanged rather than re-linted with a mismatched tool.
- Determinism cross-checked: `results/swap_results.json` byte-identical across repeated runs.
