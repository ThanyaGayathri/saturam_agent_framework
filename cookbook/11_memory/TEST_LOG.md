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

**Re-verified after SM-V2 (#96)** with the embedder no longer named in the file: `mode` now selects
it, and the same query returns the same top score of **0.799**, confirming the Mode A default
resolves to the model this entry was originally recorded against.

**Requires:** a running Postgres with pgvector (`./cookbook/scripts/run_pgvector.sh`). No model API
key; the embedder is local and downloads its weights on first run.

---

### 14_hybrid_candidates_pgvector.py

**Status:** PASS

**Description:** Exercises the SM-V3 hybrid candidate legs on the SM-V1 pgvector memory store,
against a real Postgres 18 + pgvector with the ADR-ratified Mode A embedder
(`BAAI/bge-base-en-v1.5` @768). Writes three memories to its own table (`memory_hybrid_demo`, so it
does not read rows `13_` left in the default one) and runs three queries through
`search_candidates`, printing both ranked lists per query.

**Result:** Success. `isinstance(store, HybridSearchSubstrate)` is True, so the optional capability
is detectable, and every query returns two independently-ranked lists with scores in `[0.0, 1.0]`.

The three queries show the legs differing in a different way each time:

- `order ORD-99417-XZ status` -- lexical returns **only** `carrier(0.500)`; vector ranks the whole
  corpus, `carrier(0.876) decoy(0.666) commute(0.416)`. Precision against proximity.
- `tram commute to the berlin office` -- lexical returns **nothing**, because Postgres' web-search
  syntax is conjunctive and one unmatched term empties the leg; vector still answers
  `commute(0.698)`. This is the case a lexical-only store gets wrong, and it reads as an empty
  corpus rather than as a miss.
- `order status` -- both answer and **disagree about the top hit**: vector `carrier(0.764)` first,
  lexical `decoy(0.714)` first. This is precisely what a blended `similarity_score` destroys.

**Notable:** the ticket's premise -- "assert BM25 surfaces an exact ID while vector may not" --
**did not reproduce on the production embedder**, and the file says so rather than implying it.
`bge-base` ranks the identifier-carrying record first as well. The decoy-first behaviour appears
only under the deterministic bag-of-tokens embedder the integration tests use, and that test's
docstring now scopes the claim to it. What the legs genuinely differ on is result *shape*, which is
what the two-list return exists to preserve.

Run **twice in a row and the output is identical**: events carry explicit ids and the substrate
upserts by id.

**Requires:** a running Postgres with pgvector (`./cookbook/scripts/run_pgvector.sh`). No model API
key; the embedder is local and downloads its weights on first run.

---

### 15_scoped_and_windowed_recall.py

**Status:** PASS

**Description:** Exercises the SM-V4 valid-time window through the seam against a real Postgres 18
+ pgvector, on its own table (`cookbook_windowed`) so the reads show this file's five rows rather
than whatever a shared default table has accumulated. Writes three memories for `acme/ada` and one
each for `acme/bob` and `globex/ada`, at three different `occurred_at` quarters, then reads the same
question unwindowed, with an upper bound only, with a lower bound only, and with a full window
across all three scopes.

**Result:** Success. `valid_to=Q3` returns the two memories that became true before July and
excludes the Q3 relocation, which starts exactly at the bound -- the window is half-open.
`valid_from=Q3` returns **all three**, which is the case worth reading: neither earlier memory has
an end date, so both are open-ended intervals that still overlap the second half of the year. A
window selects records whose validity *overlaps* it, not records authored inside it, and nothing in
this file claims the earlier facts ended -- bounding one is the graph's job (SM-G5 #103). The first
draft of this cookbook framed the same reads as "before and after the move" and was corrected
rather than documented, because that framing implies a filter the data does not support.

Scope isolation holds under the window: `acme/bob` reads only `bob-q3` and `globex/ada` only
`other-q3`, though all three rows carry the same relocation content and the same valid time.
`dropped_outside_window` and `dropped_by_validity` both print 0 for the windowed read, which is the
design showing through -- the substrate filtered in-search, so the provider had nothing left to
drop. Setting `as_of` alongside a window is refused with a `ValueError`.

Run **twice in a row and the output is identical**: every event carries an explicit id and the
substrate upserts by id.

**Requires:** a running Postgres with pgvector (`./cookbook/scripts/run_pgvector.sh`). No model API
key; the embedder is local and downloads its weights on first run.

---

### 16_memory_embedding_pipeline.py

**Status:** PASS

**Description:** The memory embedding pipeline (SM-V2 #96). Prints what each deployment mode
selects, exercises the Mode A air-gap refusal against three remote embedders, shows the version tag
stamped on every stored vector, embeds a batch on the write path and one query on the read path
through the same resolved embedder, and finally resolves the real Mode A default.

**Result:** Success. Mode A selects `sentence_transformer` / `BAAI/bge-base-en-v1.5` at 768d and
Mode B `azure_openai` / `text-embedding-3-large` at 1024d, both from SM-R4's `DECISION.md`.
`huggingface`, `openai` and `cohere` are each **refused** under Mode A with `field_name="embedder"`
-- the carried-forward ADR 0003 A.3c item, and refused as a class rather than as the one name the
ADR happens to mention. The real default resolves to `BAAI/bge-base-en-v1.5|d768|cos|v1` and
returns a 768-dimension query vector.

**Found by running it:** the first version of the Mode A default recorded 768 but did not pass it to
the constructor, and `SentenceTransformerEmbedder.dimensions` is 384 whatever model it loads -- so
the pipeline refused its own default. No unit test could have caught it, because every test double
declares the width it actually returns, which is the one thing the real class does not do. Fixed by
passing `dimensions` as a constructor argument, and pinned by
`test_the_mode_default_tells_its_embedder_the_dimension`.

**Requires:** no database and no API key. Downloads `bge-base` (~440 MB) on first run.

---

### 17_entity_extraction_graph.py

**Status:** PASS

**Description:** Entity extraction from episodes into temporal-graph nodes (SM-G2 #100), against a
real Neo4j 5 Community and a real Postgres 18 + pgvector, with the ADR-ratified Mode A embedder
(`BAAI/bge-base-en-v1.5` @768). Writes three episodes across two users in one tenant, reads once to
show the read path costs no model call, then drives `provider.consolidate()` for each user and
prints the entity nodes with their type, normalised key, vector width and source episodes.

**Result:** Success. Extraction runs only on the consolidation lane -- the read before it returns
its two episodes without a model call, and `details["entities"]` reports the extractor and the
embedding version only after `consolidate`. Provenance unions across interactions: `NorthStar
Analytics` and `Sarah Chen` each come back with **both** of ada's episode ids, having been mentioned
in each. Every node carries `dims=768`, so the candidates are dedup-ready for SM-G8 (#106).

The SM-G8 boundary shows up in the output rather than only in a docstring: ada's `NorthStar
Analytics` and bob's `NorthStar` are **two nodes**, and they are also under two scopes, which is two
separate reasons this ticket does not merge them. That is the intended state -- entity resolution is
#106's.

**Model note:** the file uses `OpenAIResponses(id="gpt-5.5")` per CLAUDE.md. This machine carries
only Azure credentials, so the run above was performed with the model swapped for
`AzureOpenAI(id="gpt-5.4")` and everything else identical; the OpenAI path itself is unexercised
here and reaches a `ModelAuthenticationError` without a key, as expected.

**Requires:** `./cookbook/scripts/run_neo4j.sh`, `./cookbook/scripts/run_pgvector.sh`, and an
`OPENAI_API_KEY`. Extraction is **one model call per interaction**, so the cost scales with the
episode count rather than with the run.

**Note:** the file writes to its own `memory_vectors_sm_g2` table. `consolidate` reads every episode
in the scope, so on the shared default table it extracted entities from
`13_vector_substrate_pgvector.py`'s memories and printed a node list that matched none of its own
writes -- corrected here rather than documented.

**Renumbered from `14_` to `17_` at merge time** (review-toolkit skill guidance): SM-V3 (#86) and
SM-V4/SM-V2 claimed `14_`-`16_` on `main` after this file branched. Whoever merges second renumbers.

---

### 18_episodic_subgraph.py

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

**Renumbered from `14_episodic_subgraph.py` to `15_`, then to `18_`, across two merges**: `feat/SM-V3`
took `14_` first; then, while this branch was still catching up, `feat/SM-V4`, `feat/SM-V2` and
`feat/SM-G2` landed `15_`-`17_` between this branch's two merges of `main`. Per this file's own
convention, whoever merges second renumbers -- twice, this time.

---

### 19_relation_extraction_graph.py

**Status:** FAIL

**Description:** Relation/fact edge extraction from episodes into typed graph edges (SM-G3 #101),
against a real Neo4j 5 Community and a real Postgres 18 + pgvector, wired alongside `17_`'s entity
pipeline via a second `relation_pipeline` option.

**Result:** The file does not run to completion in this environment: the write and read-path legs
run clean against the real containers (two episodes written, the pre-consolidate read returns them
with no model call), but `provider.consolidate()` reaches the entity extraction pass and raises
`ModelAuthenticationError` -- **this environment carries no model credentials at all (no
`OPENAI_API_KEY`, no Azure key)**, unlike the machine `17_` was run from, so the LLM leg of this
file, and therefore this file's own pass/fail, is unexercised here.

To still validate the graph write path against the real backend rather than only the in-memory
double, `RelationExtractionPipeline` was run directly (not through this cookbook) against the same
Neo4j container with a stub extractor standing in for the model: one `run()` followed by one
`arun()` over the identical `(Ada, works_at, Acme Corp)` triple left **exactly one** edge in the
graph, with `r.predicate = "works_at"` and `r.provenance = {"source_episode_ids": ["e1"]}` -- proof
that `relation_edge_id_for`'s deterministic id upserts the same edge on re-extraction rather than
accumulating a duplicate one, which the unit suite's `FakeGraph` cannot prove on its own.

**Requires:** `./cookbook/scripts/run_neo4j.sh`, `./cookbook/scripts/run_pgvector.sh`, and an
`OPENAI_API_KEY`. Extraction is **one model call per interaction**, so the cost scales with the
episode count rather than with the run.

**Note:** the file writes to its own `memory_vectors_sm_g3` table, matching `17_`'s own reasoning:
`consolidate` reads every episode in the scope, so sharing a table mixes another cookbook's writes
into this run.

---

### 20_entity_resolution_graph.py

**Status:** PASS

**Description:** Exercises SM-G8's `EntityResolutionPipeline` against a real Neo4j 5 Community.
Writes two episodes each mentioning a different spelling of one company ("Acme" / "Acme
Corporation"), writes both as separate candidate entity nodes (as `17_entity_extraction_graph.py`
deliberately leaves them), embeds each with a real `sentence-transformers` model, then runs the
resolution pipeline over both candidates.

**Result:** Success, and all three acceptance criteria are visible in the output. Before
resolution, two nodes exist for one company. `pipeline.run` reports 1 candidate (the second,
"Acme Corporation", is folded away by the first's merge before it would be attempted, and round 3's
review fixed `report.candidates` to count only what was actually attempted, not `len(candidates)`),
1 merged into 1 survivor. After resolution, exactly one node remains, carrying
`aliases=['Acme Corporation']` and
the union of both episodes' ids in `source_episode_ids` -- duplicate entities merged, aliases
retained. Both episodes' `MENTIONS` edges now resolve to the one surviving node's name -- edges
updated on merge. The two candidates are given the **same** description on purpose: description is
folded into the embedded text (`entities.embedding_text`), and two real, differently-worded
descriptions of the same company scored a normalised cosine of ~0.84 against the default
`min_score=0.92` floor -- correctly refusing to merge on a genuinely weaker signal, but a duller
demo of the merge path than showing the acceptance criteria succeed.

**Requires:** `./cookbook/scripts/run_neo4j.sh`, and a local `sentence-transformers` embedder --
installed by `.venvs/demo`, no API key.

**Run twice in a row and the output is identical**: episodes and entities are both written by id,
so the second run's `upsert_entities` call recreates the "Acme Corporation" node the first run's
merge had just deleted -- both runs start from the same two-node state and print the same report.

**Renumbered from `19_entity_resolution_graph.py` to `20_`**: `feat/SM-G3` landed `19_` on `main`
while this branch was still in review. Per this file's own convention, whoever merges second
renumbers.

---

### 21_episode_importance.py

**Status:** PARTIAL

**Description:** Demonstrates `ImportanceScorer` (SM-E5): scores two contrasting episodes with
`ascore()`, applies the result to each with `dataclasses.replace(episode, importance=score)`, and
shows the scored value reaching a reader through the existing `EPISODE_KEYS` floor.

**Result:** Verified structurally with a mocked `Model.aresponse` (no network) -- the script runs
end-to-end, `dataclasses.replace` applies the scored value, `significant.importance >
mundane.importance` holds, and `episode_to_record(...).metadata["importance"]` carries it through.
**Not run against a real model**: no `OPENAI_API_KEY` was available in the environment that wrote
this cookbook. `ImportanceScorer` itself is covered against a mocked model by
`libs/agno/tests/unit/memory/test_episodic_importance.py`; this file is the only thing left
unverified against a live provider. Re-run with a real key before relying on this entry as a full
PASS.

**Requires:** `OPENAI_API_KEY` (or your configured provider's credentials). No database.

**Renumbered from `15_` to `19_` to `20_` to `21_` across three merges**: `feat/SM-V4`,
`feat/SM-V2`, `feat/SM-G2` and `feat/SM-G6` claimed `15_`-`18_` on `main` between this branch's
first two merges of `main`; then `feat/SM-G3` claimed `19_`; then `feat/SM-G8` claimed `20_`. Per
this file's own convention, whoever merges second renumbers -- four times, this time.

---

### 22_episodic_relevance_scoring.py

**Status:** PASS

**Description:** Exercises SM-E6's relevance signal: `cosine_relevance` on two precomputed
embeddings, `episode_relevance` embedding raw text through a deterministic stub embedder, and the
three boundary refusals (empty embedding, mismatched width, a `None` an embedder returned instead
of raising).

**Result:** Success. With `DIM = 128` on the bag-of-hashed-tokens embedder, the matching pair
("deploy the payments service to production" / "shipped the payments service to prod") scores
`0.577`, distinctly higher than the unrelated pair ("...to production" / "the office coffee
machine is broken again") at `0.309` -- the acceptance criterion, visibly. `cosine_relevance` and
`episode_relevance` agree exactly on the matching/unrelated pair, as they should since
`episode_relevance` is a thin wrapper that embeds then calls `cosine_relevance`. All three
refusals print the named `MemoryOperationError` rather than a silent fallthrough.

**Requires:** nothing -- no database, no API key. Run with `.venv` or `.venvs/demo`.

**Renumbered from `19_` to `21_` to `22_` across two merges**: SM-G3 (#95) landed `19_` on `main`
while this branch was still catching up; `episodic/__init__.py` was a genuine 3-way concurrent
collision with SM-E4 (#91) and SM-E5 (#93). SM-E4 merged first, so this file moved to `21_`
avoiding SM-E5's own then-open claim on `20_`. Then SM-E5 (#93) merged and its branch had already
renumbered to `20_entity_resolution_graph.py`/`21_episode_importance.py` following SM-G8 (#96)
landing `20_` first -- so this file moves to `22_` to stay clear of both. Per this file's own
convention, whoever merges second (now third) renumbers.

---

### 23_consolidation_worker.py

**Status:** PASS

**Description:** The async consolidation worker (SM-L1 #122) draining `MemoryProvider.consolidate`
off the hot path: 7 episodes against a pass cap of 3, one `ConsolidationWorker.run_once()` tick
running 3 passes (`[3, 3, 1]` episodes scanned) and fully draining the tier via the provider's
internal resumption cursor -- no page token passed by the caller. Then a simulated transient
`aconsolidate` failure is retried once and recovers, landing exactly one new semantic fact (no
duplicate, since the fact id is derived from its source episode). A third tick with nothing new
written confirms the cursor holds: zero episodes scanned, one pass.

**Result:** Ran clean, vector-only (no graph substrate needed -- SM-C5), no database, no API key:

```
Draining a tier larger than one bounded pass
=============================================
episodic rows written: 7, pass cap: 3
one worker tick ran 3 pass(es): [3, 3, 1]
fully drained: True
semantic facts now: 7

A transient failure is retried, and the retry is safe because
consolidate upserts by a content-derived id -- never a duplicate:
attempts made: 2, recovered: True
semantic facts now: 8

Re-running the same tick again finds nothing new -- the cursor
already sits past every episode written so far:
passes this tick: 1, episodes scanned: 0
```

**Note:** at the time this ran, `09_memory_provider_seam.py` and `11_memory_tiers.py` were both
failing independently of this ticket -- their local `InMemoryGraph` demo double no longer satisfies
`GraphSubstrate` (missing `upsert_entities`/`aupsert_entities`, added by an already-merged sibling
ticket). Confirmed pre-existing at this branch's base commit (`9d9268d2`), not introduced by SM-L1.
Flagged separately rather than fixed here to stay in scope. This file sidesteps it by wiring a
vector-only provider.
