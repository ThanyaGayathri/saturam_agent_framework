# Test Log: 11_memory

> Tests not yet run. Run each file and update this log.

### 01_agent_with_memory.py

**Status:** PENDING

**Description:** Tests persistent agent memory updates and retrieval for one user.

---

### 02_agentic_memory.py

**Status:** PENDING

**Description:** Tests agentic memory create, recall, delete, and update operations.

---

### 03_agents_share_memory.py

**Status:** PENDING

**Description:** Tests memory sharing across two agents using the same database.

---

### 04_custom_memory_manager.py

**Status:** PENDING

**Description:** Tests custom memory manager instructions and memory retrieval behavior.

---

### 05_multi_user_multi_session_chat.py

**Status:** PENDING

**Description:** Tests sequential multi-user, multi-session memory behavior.

---

### 06_multi_user_multi_session_chat_concurrent.py

**Status:** PENDING

**Description:** Tests concurrent multi-user, multi-session memory behavior.

---

### 07_share_memory_and_history_between_agents.py

**Status:** PENDING

**Description:** Tests shared session history and memory across two agents.

---

### 08_memory_tools.py

**Status:** PENDING

**Description:** Tests MemoryTools and WebSearchTools integration with user memory.

---

### 09_memory_provider_seam.py

**Status:** PASS

**Description:** Exercises the SM-C1 MemoryProvider seam end to end with two in-file substrate
stand-ins: write to both substrates, consolidate episodes into semantic facts, and read a merged
slice back as prompt context. Then runs the same consumer function against a second config
(`mode=FRONTIER`) to show the mode reaches the provider without any call-site edit, followed by a
bi-temporal point-in-time read across an invalidation and a cross-tenant isolation check.

**Result:** Success (re-run after the scope reset). The run is **byte-identical across 10
executions** -- verified by hashing, because an earlier version of this claim was false:
consolidation grouped episodes in UUID-driven scan order, so four distinct outputs appeared across
eight runs. Grouping now walks content order.

Both mode blocks produce identical output from the same consumer function. Consolidation merged
2 episodes into 2 facts (the statements are distinct, so nothing deduped), and the merge reports
`method=score_sum` -- there is no weighted or RRF fusion in SM-C1, which is SM-H2's (#132). The
point-in-time read returns the fact `as_of=200` and not `as_of=400`, confirming invalidation is
expire-and-reinsert rather than a delete. A read scoped to tenant `globex` returns 0 of tenant
`acme`'s records. Requires no database, no model, and no API key.

Two defects in this file's reference substrates were found by review and fixed: `upsert` stored the
caller's object rather than a copy, so the "two independent substrates" shared one row and
`graph.invalidate()` also stamped `expired_at` on the vector copy -- the bi-temporal demo worked
partly for the wrong reason. And `invalidate` overwrote `t_invalid` instead of taking the tightest
bound, which is the in-place clobber its own comment cites SM-R10 for avoiding. Verified after the
fix: `vector row IS graph row -> False`, and two invalidations leave one current belief with
`t_invalid=250`.

Note that `scan` here takes no cursor. The `since` / `after_id` pair it used to model was removed
along with the consolidation resumption protocol; a resumable walk is SM-L1's (#122).

Re-run under SM-C4 (#92): unchanged output, byte for byte. The fail-closed entry guard refuses a
call that carries no `Scope`, and every call here carries one, so a cookbook that had to change
would have meant the guard was refusing legitimate traffic.

The two stand-in substrates now call `require_scope` first, matching the obligation the
`VectorSubstrate` / `GraphSubstrate` docstrings place on an implementor. Reference code gets
copied, so a template that skipped the guard would teach the one shape those docstrings forbid.
Output is unchanged by it, which is the point: the guard fires on scopes a caller should never
have built, and this file builds none of them.

---

### 10_graph_substrate_neo4j.py

**Status:** PASS

**Description:** The temporal knowledge graph behind the seam, against a real Neo4j 5 Community
started with `cookbook/scripts/run_neo4j.sh`. Shows that choosing a graph backend is a config edit:
`config.graph = {"backend": "neo4j", ...}` resolves through `discovery.construct`, and no line below
the config names Neo4j, a Bolt URI, or the driver.

**Result:** Reports `vector: False / graph: True` -- a graph-only provider is a supported wiring.
Three scopes read the same query: `acme/ada` sees 1 record, `acme/bob` 0, `globex/ada` 1. The
`acme/bob` line is the one worth watching: isolation on Community is a property filter on every
statement, so two users of one tenant are separated by the same mechanism as two tenants, and a
tenant-only predicate would have returned 1 there. Invalidation reports `t_invalid: 1700000000` with
`still current belief: True`, which is expire-and-reinsert rather than a delete -- the bounded copy
is the new current row. The point-in-time reads then answer 1 record as of march and 0 as of june.

Two earlier drafts of this file were refused by the substrate, both times correctly. The first
invalidated at a `t_invalid` earlier than the row's `t_valid` ("the row would be valid at no
instant"); the timeline is now explicit so the output is deterministic. The second invalidated an
**episode**, which ADR 0003 section B.6 routes through its own lifecycle rather than through the
edge primitive -- the file now writes a semantic fact and invalidates that, which is what the
primitive is for.

The provider is released with `provider.close()` rather than `provider.graph.close()`: reaching past
the seam for a backend method is what the seam exists to prevent, and a caller building a provider
per request has no way to know which substrate holds a connection pool.

**Requires:** a running Neo4j (`./cookbook/scripts/run_neo4j.sh`). No model, no API key.

---

### 11_memory_tiers.py

**Status:** PASS

**Description:** Exercises the SM-C2 CoALA tier model against two in-file substrate stand-ins that
record what they were asked. Prints the `TIER_POLICY` table, writes one event per tier and shows
where each landed, shows a query naming no tiers resolving to the three persistent ones, shows a
procedural read skipping the graph leg while a semantic read consults it, contrasts `tiers()` on a
both-substrates provider against a graph-only one, and prints the six `learn` stores with the tier
each maps to and the scope slots its `recall` filters on.

**Result:** Success. Output is **byte-identical across 3 executions** (verified by hashing), which
matters here because the read paths iterate substrate contents. Write routing observed: an event
naming no tier lands in `episodic`; the graph holds the episode as an episode and the semantic
memory as a fact, via the two different ports; the procedural rule reaches the vector substrate
only and appears in neither graph list. The procedural read reports `consulted the graph: False`
and the semantic read `True`. The graph-only provider serves `episodic, semantic` and returns `[]`
for a procedural read rather than refusing it. Requires no database, no model, and no API key.

Re-run under SM-C4 (#92): output unchanged, byte for byte, and its two stand-in substrates gained
the same `require_scope` guard as `09_memory_provider_seam.py`'s. Both files were fixed together
rather than only the one a review named -- a reader copying either gets the same template, so
guarding one would have left the other teaching the shape the port docstrings forbid.

---

### 12_episode_representation.py

**Status:** PASS

**Description:** Exercises the SM-E1 episode representation with no substrates at all, because the
subject is the record rather than a store. Builds an episode from a write-back event and prints
what the row carries (identity, tenant, raw content, `occurred_at`, `t_valid`, `created_at`,
default `importance`), shows `graph_node_id` derived from the scope key and the episode id, reads
the episode back as a `MemoryRecord` and prints the provenance references, the `EPISODE_KEYS`
metadata floor and the caller's own metadata surviving beside it, shows a re-segmentation split
naming no single replacement while keeping both, and prints the four boundary refusals (missing
id, wrong tier, metadata that would shadow an episode field, a link addressing another episode).

**Result:** Success. Output is identical across 3 executions apart from the one `created_at` line,
which is this run's transaction time -- verified by hashing with that line masked (same digest for
all three) and by diffing raw (that line the only difference). Observed: valid time comes from
`occurred_at` (1700000000) while `created_at` is the run clock, so the two axes are visibly
independent; `graph_node_id` is `acme/assistant/ada/*/deploy-42/ep-1`, the scope key with the
episode id as a sixth slot; `importance` stays at the 0.5 default because scoring it is SM-E5's;
the split prints `superseded_by on record: None` with `['ep-2', 'ep-3']` in provenance. Each
refusal names the field and, where one exists, the ticket that owns the behaviour. Requires no
database, no model, and no API key.

---

### 13_vector_substrate_pgvector.py

**Status:** PASS

**Description:** Exercises the SM-V1 pgvector memory vector store through the seam against a real
Postgres 18 + pgvector, with the ADR-ratified Mode A embedder (`BAAI/bge-base-en-v1.5` @768).
Selects the backend by config alone (`config.vector = {"backend": "pgvector", ...}`), writes one
memory for each of three scopes -- two users inside one tenant, plus a second tenant -- and reads
them back with a query that **shares no words** with any stored memory.

**Result:** Success. Semantic recall works: "which city did ada relocate to" retrieves "ada moved to
berlin in march" at a similarity of **0.799**, so the ranking is embedding-driven and not lexical.
Isolation holds on the slot that matters: `acme/bob` reads bob's memory and never ada's, though the
two differ only in the user slot inside one tenant. The top score prints in `[0.0, 1.0]` with higher
better, which is the port's contract and not pgvector's native `<=>` distance. A point-in-time read
is **refused** rather than answered, because the graph leg is not wired and the vector projection
carries no authoritative bounds.

Run **twice in a row and the output is identical**: events carry explicit ids and the substrate
upserts by id, so a re-run replaces the same three rows instead of accumulating a fresh set. The
first version of this file left the ids unset, and each run added three more rows -- corrected here
rather than documented.

**Requires:** a running Postgres with pgvector (`./cookbook/scripts/run_pgvector.sh`). No model API
key; the embedder is local and downloads its weights on first run.

---

### 14_episodic_subgraph.py

**Status:** PASS

**Description:** Exercises SM-G6's episodic subgraph against a real Neo4j 5 Community. Writes four
episodes across two users of one tenant, each carrying the entity names it mentions, then reads them
back three ways: by mention link, by half-open valid-time window, and by both together.

**Result:** Success, and all three acceptance criteria are visible in the output. The link read
returns the two berlin episodes oldest-first and skips the scheduler one; the window read
`[MARCH, JUNE)` returns march and april and excludes june, which is the half-open bound; the two
filters together narrow to the single march episode, so they conjoin rather than union. "Store an
episode, traverse to its entities" prints the three entity node ids read off the `MENTIONS` edges --
`acme/*/ada/*/*/entity/{ada,berlin,platform team}` -- in the order the episode named them. Ada's
`berlin` and bob's `berlin` print as different node ids and bob's link read returns only his own
episode, so entities are keyed on the full scope rather than the tenant. The unfiltered recall is
**refused** naming SM-G9 (#107) rather than answered, because every episode in scope is an
enumeration and what that means over a temporal graph is not this ticket's to define.

Run **twice in a row and the output is identical**: the four events carry explicit ids and
`upsert_episodes` replaces by id, so a re-run replaces the same four episodes and their mention
edges rather than accumulating a fresh set.

**Requires:** a running Neo4j (`./cookbook/scripts/run_neo4j.sh`). No model API key.

**Environment note:** run with `.venv`, not `.venvs/demo` -- `demo_setup.sh` does not install the
`neo4j` driver, so `10_graph_substrate_neo4j.py` has the same constraint. Pre-existing, not
introduced here.
