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

**SM-E10 (#117) addition:** `resolve_episode_link` reads the march episode's derived
`graph_node_id` / `entity_ids` back against the graph this cookbook just wrote to, printing
`exists=True  t_valid=1600000000  drift=[]` -- the vector-side projection and the graph node agree.

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

### 19_relation_extraction_graph.py (re-verified, SM-G7 #105)

**Status:** FAIL (same pre-existing gap; the changed part is verified separately)

**Description:** SM-G7 replaced this file's `relation_edges` helper -- which used to reach
`graph._driver` directly because the port had no read verb for the semantic subgraph -- with
`graph.recall_facts(ACME_ADA, since=0)`, the read this ticket adds.

**Result:** Still gated on the same `OPENAI_API_KEY` this environment does not have, so the file
itself does not run end to end here -- unchanged from the entry above. What changed is verified
directly: `RelationExtractionPipeline.run()` then `.arun()` were run against the real
`smg1-neo4j` container (the same technique the entry above used) with a stub extractor over
`(Ada, works_at, Acme Corp)`, then read back with `graph.recall_facts(scope, entities=["Ada"])`
and `graph.recall_facts(scope, relation="works_at")` -- both returned exactly the one edge written,
and `graph.recall_facts(scope, relation="lives_in")` returned none. `recall_facts` reads what the
consolidation lane writes, against the real engine.

**Requires:** `./cookbook/scripts/run_neo4j.sh`, `./cookbook/scripts/run_pgvector.sh`, and an
`OPENAI_API_KEY`.

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

---

### 24_episodic_segmentation.py

**Status:** PASS

**Description:** Exercises SM-E3's inline segmentation prior end to end through
`episodes_from_events`: a two-topic session (launch planning, then an unrelated backup check after
a 2-hour idle gap) segmented in one call; the same session split across two write-back calls with
`SegmentState` threaded between them; a hard `max_turns_per_episode` cap firing with no idle gap at
all; and the full boundary-metadata field set (`boundary_method`/`boundary_trigger`/
`boundary_confidence`/`segmenter_version`/`revision`/`refined`) on one opening episode.

**Result:** Success. The two-topic session segments into exactly 2 episodes (the acceptance
criterion). `SegmentState` carried across the two-call run reproduces the same boundary the
single-call run found, with `boundary_trigger` reporting the measured idle gap in seconds
(7140.0s = 2 hours - 60s, the interval between the last event of call 1 and the first of call 2).
The turn cap (`max_turns=3`) opens a new episode at the 4th message despite each message being 1
second apart -- well inside the default 900s gap window -- confirming the cap fires independently
of the gap rule. `segmenter_version` on the stamped row matches `segmenter_version()` called with
the same parameters used to build it.

**Requires:** nothing -- no database, no API key. Run with `.venv` or `.venvs/demo`.

**Renumbered from `23_` to `24_`: SM-L1 (#122) merged to `main` first and took `23_` for
`23_consolidation_worker.py`.** Per this file's own convention, whoever merges second renumbers --
now the fourth time this file's lineage has moved (see `22_`'s own note above for the earlier two).

---

### 25_semantic_subgraph.py

**Status:** PASS

**Description:** The semantic subgraph's read surface (SM-G7 #105) -- resolved entities and fact
edges written directly (no model call), then recalled by entity (either end of the edge), by
relation label, and by valid-time window, against a real Neo4j 5 Community
(`smg1-neo4j`, the container already running on this host on the ports `run_neo4j.sh` names).

**Result:** Ran to completion against the real engine. Entity recall found both edges naming
"Ada" (`works_at`, `relocated_to`) and both edges naming "NorthStar Analytics" (`works_at`,
`located_in`) at either end. Relation recall found exactly the one `located_in` edge. The
valid-time window (`until=JUNE`) excluded the one edge dated at `JUNE` and kept the two dated at
`MARCH`. The episodic-vs-semantic count at the end reported 3 episodic rows (left in this scope by
an earlier `18_` run in this same session) against 3 semantic fact edges -- `recall_facts` returned
none of the episodic ones and `recall_episodes` returned none of these fact edges. The final
unfiltered call raised the expected refusal naming SM-G9 (#107).

**Requires:** `./cookbook/scripts/run_neo4j.sh`; no API key.

**Renumbered from `20_semantic_subgraph.py` to `23_` to `24_` to `25_` across three merges**:
`feat/SM-G8` landed `20_` on `main` first, `feat/SM-E5`/`feat/SM-E6` claimed `21_`/`22_` while this
branch was still catching up, then `feat/SM-L1` claimed `23_consolidation_worker.py`, then
`feat/SM-E3` claimed `24_episodic_segmentation.py`. Per this file's own convention, whoever merges
second renumbers -- three times, this time.

---

### 26_episodic_composite_scoring.py

**Status:** PASS

**Description:** SM-E7's composite scorer -- `composite_episodic_score` combining relevance
(SM-E6), recency (SM-E4) and importance (SM-E5) into one weighted-sum score. Shows the default
weights (recency=0.0, per ADR 0003 B.1: no additive per-query hot-path recency rescore) leaving a
stale and a fresh episode tied, an explicit write-time weight set that then separates them,
a four-episode fixture ranked in the expected order, and the three boundary refusals (a signal
outside `[0.0, 1.0]`, a NaN weight, a negative weight -- added in review round 1, PR #109).

**Result:** Ran to completion with no database and no API key. Stale/fresh scored identically
(1.8/1.8) under the default weights, then diverged (1.82/2.75) once recency was given a nonzero
weight. The fixture ranked `shipped_v2_to_prod` (2.70) > `quick_status_ping` (1.90) >
`old_incident_postmortem` (1.75) > `unrelated_small_talk` (0.40), matching the acceptance
criterion's expected ordering. All three refusals raised `MemoryOperationError` naming the
offending field (`relevance`, `weights.relevance` for both the NaN and the negative-weight case).

**Requires:** Nothing -- no database, no API key.

---

### 27_episode_forgetting.py

**Status:** PASS

**Description:** The SM-E9 forgetting policy (`select_for_forgetting`) applied to a
three-episode batch: a low-importance episode idle 40 days, a high-importance episode idle the
same 40 days, and a recent episode idle 1 day. The default recency-floor threshold (0.05) and the
default decay parameters (`base_half_life=5d`, `imp_factor=4.0`).

**Result:** Ran to completion. Exactly the low-importance stale episode was archived (`expired_at`
set to the run's `now`); the high-importance episode at the same age survived (its longer,
importance-scaled half-life keeps its decay score above the floor at 40 days), and the recent
episode was untouched. The two originals passed in that were *not* selected, and the one that
*was*, all still report `expired_at=None` on the original objects -- `select_for_forgetting`
returns detached archived copies, never mutates its input.

**Requires:** nothing -- no database, no API key. Run with `.venv` or `.venvs/demo`.

**Renumbered from `26_` to `27_`: SM-E7 (#109) merged to `main` first and took `26_` for
`26_episodic_composite_scoring.py`.** Per this file's own convention, whoever merges second
renumbers.

---

### 28_multihop_graph_traversal.py

**Status:** PASS

**Description:** Anchor-seeded multi-hop graph traversal (SM-G9 #107) -- a three-hop entity chain
(Ada -> NorthStar Analytics -> Berlin -> Germany) written directly (no model call), then read back
through `traverse` with the query text naming only "Ada", against a real Neo4j 5 Community
(`smg1-neo4j`, the container already running on this host on the ports `run_neo4j.sh` names).

**Result:** Ran to completion against the real engine. The default hop bound (2) surfaced both the
hop-1 edge ("Ada works_at NorthStar Analytics") and the hop-2 edge ("NorthStar Analytics
located_in Berlin") -- the second shares no word with the query text "tell me about Ada" at all,
and is surfaced purely by graph adjacency -- while the hop-3 edge ("Berlin part_of Germany") was
correctly excluded, and the hop-1 edge outranked the hop-2 one. Sweeping `max_traverse_hops` over
1/2/3 on freshly constructed substrates showed exactly the expected edge set grow at each bound
(`['ada-works-at-northstar']` -> `+ 'northstar-located-in-berlin'` -> `+ 'berlin-part-of-germany'`).

**Requires:** `./cookbook/scripts/run_neo4j.sh`; no API key. Run with `.venvs/demo`.

**Renumbered from `26_` to `28_`: `feat/SM-E7` (#109) and `feat/SM-E9` (#112) both merged to
`main` first and took `26_`/`27_` for `26_episodic_composite_scoring.py`/`27_episode_forgetting.py`
while this branch was still catching up.** Per this file's own convention, whoever merges second
renumbers.

---

### 29_near_duplicate_fact_dedup.py

**Status:** PASS

**Description:** The SM-L4 dedup stage (`consolidation.dedup.merge_near_duplicate_facts`) folding
two episodes -- "the deploy failed!" and "the deploy failed" -- that are the same fact once
normalized but that `_distil`'s raw token-overlap grouping (jaccard 0.5, below the 0.8 threshold)
treats as distinct, followed by a third, genuinely distinct episode that does not fold.

**Result:** Ran to completion. First pass: 2 episodes scanned, 1 semantic fact written,
`duplicates_merged=0` (`_distil`'s own grouping caught nothing), `near_duplicate_facts_folded=1`
(this stage caught the punctuation-only difference). The surviving fact carries both source
episode ids, `t_valid=50` (the earlier of the two episodes' 100 and 50, not just the survivor's
own), and topics unioned from both episodes. Second pass: the third, distinct episode ("customer
asked about invoicing") produced its own fact with `near_duplicate_facts_folded=0`, and the total
semantic fact count is 2, confirming the dedup stage does not over-merge.

**Requires:** No database, no API key. Run with `.venvs/demo`.

---

### 30_episodic_contiguity_retrieval.py

**Status:** PASS

**Description:** Temporal-contiguity retrieval (SM-E8 #115) -- `temporal_neighbors` on a
six-episode session names the radius-2 neighbors of a hit, `ContiguityBuffer(capacity=4)`
accumulates them across the pass and deduplicates a second hit's overlapping neighbors, and
`blend_contiguity`/`contiguity_bonus` blend a neighbor's distance from its hit into its own
`composite_episodic_score`.

**Result:** Ran to completion. `temporal_neighbors` returned the expected four neighbors, nearest
first (`ep-1`/`ep-3` at distance +/-1, `ep-0`/`ep-4` at distance +/-2). The buffer filled to its
4-item capacity with zero evictions on the first hit's lookup. The second hit's neighbor lookup
(`ep-2` at distance -1, `ep-4` at distance +1) deduplicated `ep-4` (already buffered from the
first hit) but genuinely added `ep-2` (the *first* hit itself, new from the second hit's
perspective) -- pushing the buffer past capacity and evicting `ep-1`, the oldest entry. The
buffer's size stays at 4/4 either way, so this is not visible from the length alone; the cookbook
now prints the newly-buffered and evicted ids explicitly to make both true.
The fixture showed an unrelated later-session episode (base score 0.650) outranking the hit's own
temporal continuation (base score 0.500) under relevance/importance alone, and the ranking
reversing once the continuation's contiguity bonus (0.200 at distance +1) was blended in (0.700 >
0.650) -- the improved-temporal-recall behavior the acceptance criteria ask for. The refusal
section confirmed a negative radius, a hit absent from the candidate pool, a zero-capacity buffer,
and a distance-0 contiguity bonus are all refused with a named field.

**Requires:** nothing -- no database, no API key. Run with `.venv` or `.venvs/demo`.

**Renumbered from `29_` to `30_`: `feat/SM-L4` took `29_near_duplicate_fact_dedup.py` first.**
Per this file's own convention, whoever merges second renumbers.

---

### 31_semantic_fact_store.py

**Status:** PASS

**Description:** The semantic fact store (SM-S1 #118) -- `SemanticFactStore.write` dual-writing a
triple fact ("Ada works_at NorthStar Analytics", confidence 0.95, provenance to two source
episodes) and an untripled fact ("Ada prefers async standups") to a real pgvector
(`smv1-pgvector-5532`) and a real Neo4j 5 Community (`smg1-neo4j`), then reading the triple fact
back through both `VectorSubstrate.search` and `GraphSubstrate.recall_facts` directly (not through
the fused `provider.read`, which would blur which leg answered).

**Result:** Ran to completion against both real engines. Both legs returned
`provenance["subject"] == "Ada"`, `metadata["confidence"] == 0.95` and
`metadata["derived_by"] == "sm_l3.extract"` for the triple fact -- the acceptance criterion
("representable in both vector and graph", "retrievable via both paths") holds end to end, not only
against the in-memory doubles the unit suite uses. The untripled fact stored and searched fine on
the vector leg with no subject/predicate/object keys in its provenance. The graph leg's
`recall_facts(entities=["Ada"])` also surfaced an unrelated pre-existing edge from an earlier
cookbook run sharing this scope (`Ada --[works_at]--> Acme Corp`, from `25_semantic_subgraph.py`'s
fixture data on the shared dev database) -- expected cross-run residue, not a defect.

**Requires:** `./cookbook/scripts/run_pgvector.sh` and `./cookbook/scripts/run_neo4j.sh`; no API
key. Run with `.venvs/demo`.

**Renumbered from `29_` to `30_` to `31_`: `feat/SM-L4` took `29_near_duplicate_fact_dedup.py`
first, then `feat/SM-E8` took `30_episodic_contiguity_retrieval.py` while this branch was still
catching up.** Per this file's own convention, whoever merges second renumbers -- twice, this
time.

---

### 32_fact_extraction_merge.py

**Status:** NOT RUN -- no model credentials in this environment

**Description:** `FactExtractionPipeline`'s mem0-style extract-and-merge (SM-L3 #124): a first
`consolidate()` pass with nothing to compare against (ADD), then a second pass after a follow-up
episode changes ada's city, which should retire the Berlin fact (`expired_at` + `superseded_by`)
and add a Lisbon one rather than leave both as current belief.

**Result:** Not exercised end to end. `smg1-neo4j` (`bolt://localhost:7691`) and a pgvector
container on `localhost:5532` were both already running on this host and reachable, but neither an
`OPENAI_API_KEY` nor Azure credentials (`AZURE_OPENAI_*`) were present in this environment, and
extraction and the merge decision are each a model call this file cannot fake -- unlike `17_`/`19_`,
there was no Azure fallback available to swap in either. Verified instead: the file parses
(`ast.parse`), imports cleanly, and passes `ruff format`/`ruff check`. The pipeline itself is
covered end to end against doubles in `libs/agno/tests/unit/memory/test_consolidation_extract.py`
(33 tests, including the ADD/UPDATE/DELETE/NOOP ops and the bi-temporal retirement fields this file
prints). Whoever next has a key should run it and update this entry -- the same caveat `17_`'s
"Model note" pins for its own `OPENAI_API_KEY` path.

**Requires:** `./cookbook/scripts/run_neo4j.sh`, `./cookbook/scripts/run_pgvector.sh`, and an
`OPENAI_API_KEY`. Extraction is one model call per pass, the merge decision one more per candidate.

**Note:** the file writes to its own `memory_vectors_sm_l3` table, for the same reason `17_`'s
note gives for `memory_vectors_sm_g2`.

**Renumbered from `29_` to `32_`: `feat/SM-L4` took `29_near_duplicate_fact_dedup.py`, then
`feat/SM-E8` took `30_episodic_contiguity_retrieval.py`, then `feat/SM-S1` took
`31_semantic_fact_store.py`, all while this branch was still catching up.** Per this file's own
convention, whoever merges second renumbers -- three times, this time.

---

### 33_reflection.py

**Status:** PARTIAL

**Description:** Importance-gated reflection (SM-L6): wires a `ReflectionPipeline` (threshold 1.2)
into `CoLocatedMemoryProvider`, writes three episodes about the same incident (importances
0.5/0.6/0.4, summing past the threshold), consolidates, and reads the synthesized insight back
through the ordinary SEMANTIC-tier read path, showing it still carries `source_episode_ids` for
all three episodes.

**Result:** Verified structurally with a mocked `Model.response` (no network) -- the script runs
end to end: `report.details["reflection"]["triggered"]` is `True`, the insight is retrievable via
`memory.read`, and its provenance names all three episode ids. **Not run against a real model**: no
`OPENAI_API_KEY` was available in the environment that wrote this cookbook, the same gap
`21_episode_importance.py` logs. `ReflectionPipeline`'s own decisions (accumulation, threshold
crossing, provenance, sync/async parity) are covered against a stub synthesizer by
`libs/agno/tests/unit/memory/test_consolidation_reflect.py`, and `LLMInsightSynthesizer._extract`'s
response-format handling is now also covered against a mocked `Model` in that same file -- what
remains unverified is specifically the real-model response shape this mock cannot guarantee to
match. Follow-up: [aicoe-tracker#192](https://github.com/Saturam-Inc/aicoe-tracker/issues/192).
Re-run with a real key and flip this entry to a full PASS/FAIL when it lands.

**Requires:** `OPENAI_API_KEY`; no database. Run with `.venvs/demo`.

**Renumbered from `29_` to `33_`: `feat/SM-L4` took `29_near_duplicate_fact_dedup.py`, then
`feat/SM-E8` took `30_episodic_contiguity_retrieval.py`, then `feat/SM-S1` took
`31_semantic_fact_store.py`, then `feat/SM-L3` took `32_fact_extraction_merge.py`, all while this
branch was still catching up.** Per this file's own convention, whoever merges second renumbers --
four times, this time.

---

### 34_sleep_time_scheduling.py

**Status:** PASS

**Description:** Sleep-time scheduling (SM-L9): builds a `SleepTimeScheduleConfig` (01:00-06:00
`America/New_York` window, 300s idle threshold), wraps it into a trigger via
`build_sleep_time_trigger`, and wires it into `ConsolidationWorker`. A fake clock drives two
`run_once` ticks against the same recorded event: one at 14:10 (idle threshold met, outside the
window) and one at Jan 16 02:00 (idle threshold met, inside the window).

**Result:** Verified end to end with no database, no API key, no network. The 14:10 tick drains 0
scopes; the 02:00 tick drains the scope and produces 1 semantic fact, confirming the window gates
firing while `record_event`'s accumulation is unaffected by the hour it was called at.

**Requires:** Nothing. Run with `.venvs/demo`.

---

### 35_hierarchical_summarization.py

**Status:** PARTIAL

**Description:** Hierarchical (session/day/global) summarization (SM-L8): wires a
`SummaryPipeline` (`day_seconds=1`) into `CoLocatedMemoryProvider`, writes two sessions'
episodes with a 1.1s gap between them, consolidates each, and reads the resulting SEMANTIC-tier
session and day summary rows back through the ordinary read path.

**Result:** Verified structurally with a mocked `Model.response` (no network) -- the script runs
end to end: `second.details["summary"]["day"]` is `True` after the second consolidate, and both a
`"session"`-level and a `"day"`-level summary come back from `memory.read`, each retrievable and
tagged with the right `metadata["level"]`. The fake `InMemoryVector` needed real scope filtering
(matching each set field, treating an unset one as a wildcard) rather than `33_reflection.py`'s
tenant-only bucketing -- that cookbook only ever consolidates and reads under one scope, but this
one consolidates two different sessions and then reads through a broader rollup scope that spans
both, which `assert_scope_isolation` correctly rejects against the looser fake. **Not run against
a real model**: no `OPENAI_API_KEY` was available in the environment that wrote this cookbook, the
same gap `33_reflection.py`'s own entry logs. `SummaryPipeline`'s own decisions (rolling vs.
periodic ids, rollup-key session stripping, retry-safety on a failed fold, sync/async parity) are
covered against a stub summarizer by `libs/agno/tests/unit/memory/test_consolidation_summarize.py`
-- what remains unverified is specifically the real-model response shape the mock cannot guarantee
to match (the mocked response above returns raw JSON `SummarizeStrategy.optimize` does not parse,
since the real, unmocked call there requests no structured output -- an artifact of this
verification's single fixed mock answering both the structured session-summary call and the plain
rollup-fold call, not a defect in the pipeline itself). Follow-up: re-run with a real key and flip
this entry to a full PASS/FAIL when one is available (same tracking as `33_reflection.py`'s
[aicoe-tracker#192](https://github.com/Saturam-Inc/aicoe-tracker/issues/192)).

**Requires:** `OPENAI_API_KEY`; no database. Run with `.venvs/demo`.

**Renumbered from `34_` to `35_`: `feat/SM-L9` took `34_sleep_time_scheduling.py` while this
branch was still catching up.** Per this file's own convention, whoever merges second renumbers.

---

### 36_hybrid_retrieval_orchestration.py

**Status:** PASS

**Description:** Feed-forward retriever orchestration (SM-H1 #131): `provider.read` fans out
across the vector+BM25 leg and the graph leg via `egpt.memory.retrieval.orchestrate`, and BM25
participates whenever the wired vector store implements `HybridSearchSubstrate` -- no code at the
`read` call site knows or cares. Writes two episodes where word overlap (vector + graph) and exact
substring containment (lexical) disagree about which one the query favours, then shows the lexical
leg's `MemorySlice.fusion["lexical_hits"]` count without it moving the ranking -- ADR 0003 section
A.6 holds the three-leg fusion strategy open, pending SM-H2 (#132), so the interim score-sum still
runs over vector+graph only.

**Result:** Runs end to end with no database, no model, no network -- both substrates are
self-contained in-memory stand-ins (the same pattern `09_memory_provider_seam.py` uses, extended so
the vector one also satisfies `HybridSearchSubstrate`). Output: `lexical_hits=1` (only
`has_phrase` contains the literal query phrase), and `higher_overlap` ranks first in
`slice_.records` regardless (word-overlap score 1.33 vs 0.67) -- confirming BM25 ran and did not
influence the merge.

**Note:** `09_memory_provider_seam.py`'s `InMemoryGraph` currently fails `checked_graph`'s
structural check (missing `upsert_entities`/`match_entities`/`merge_entities`/`recall_facts` +
async twins, added to `GraphSubstrate` after that file was last updated) -- confirmed on
unmodified `origin/main`, unrelated to this PR. This file's own `InMemoryGraph` implements the
full current protocol. Filing the `09_` fix is out of scope for SM-H1; flagged here rather than
silently worked around.

**Requires:** nothing. Run with `.venvs/demo` (`PYTHONPATH=libs/agno` from a worktree).

**Renumbered from `34_` to `36_`: `feat/SM-L9` took `34_sleep_time_scheduling.py`, then
`feat/SM-L8` took `35_hierarchical_summarization.py`, while this branch was still catching up.**
Per this file's own convention, whoever merges second renumbers.

---

### 37_procedural_memory.py

**Status:** PARTIAL

**Description:** Procedural memory (SM-S2): runs a `ProceduralReflectionPipeline` (threshold 1.2)
directly over two episodes about the same Friday-deploy incident (importances 0.6/0.7, summing
past the threshold), writes the synthesized procedure through the ordinary `CoLocatedMemoryProvider
.write`, and reads it back through an ordinary default-tier `read`, showing it renders under a
`[procedural]` line in `MemorySlice.as_context()`.

**Result:** Verified structurally with a stub synthesizer standing in for
`LLMProcedureSynthesizer` (no network) -- the pipeline/write/read path runs end to end:
`report.details["triggered"]` is `True`, the write lands on the PROCEDURAL tier with no new
routing, and the read returns it with `[procedural] Avoid Friday deploys` in the rendered context.
**Not run against a real model**: no `OPENAI_API_KEY` was available in the environment that wrote
this cookbook, the same gap `33_reflection.py` logs for its own `LLMInsightSynthesizer`.
`ProceduralReflectionPipeline`'s own decisions (threshold gating, provenance, construction guards)
and `LLMProcedureSynthesizer._extract`'s response-format handling are covered against a stub and a
mocked `Model` respectively by `libs/agno/tests/unit/memory/test_procedural.py` -- what remains
unverified is specifically the real-model response shape that mock cannot guarantee to match.
Re-run with a real key and flip this entry to a full PASS/FAIL when it lands.

**Requires:** `OPENAI_API_KEY`; no database. Run with `.venvs/demo`.

**Renumbered from `35_` to `37_`: `feat/SM-L8` took `35_hierarchical_summarization.py` and
`feat/SM-H1` took `36_hybrid_retrieval_orchestration.py` while this branch was still catching up.**
Per this file's own convention, whoever merges second renumbers.

---

### 38_kb_and_memory_unified_retrieval.py

**Status:** PASS

**Description:** KB vs memory boundary (SM-S4): `unified_read()` fans a query out across a
`MemoryProvider` and a `KnowledgeRetrievable` leg, labeling every result `RetrievalSource.KB` or
`RetrievalSource.MEMORY`. Shows a KB-only match, a memory-only match, and a leg with no store
wired returning an empty list rather than being silently absent.

**Result:** Ran end to end -- a rate-limit query returns only the KB document (labeled `kb`), a
dark-mode query returns only the memory record (labeled `memory`), and unwiring the KB leg leaves
`slice_.kb == []` while memory still answers.

**Requires:** No database, no API key. Run with `.venvs/demo`.

**Renumbered from `34_` to `38_`: `feat/SM-L9` took `34_sleep_time_scheduling.py`,
`feat/SM-L8` took `35_hierarchical_summarization.py`, `feat/SM-H1` took
`36_hybrid_retrieval_orchestration.py`, and `feat/SM-S2` took `37_procedural_memory.py`, all
while this branch was still catching up.** Per this file's own convention, whoever merges second
renumbers -- four times, this time.

---

### 39_working_memory.py

**Status:** PASS

**Description:** The WORKING-tier buffer (SM-C6): builds a `WorkingMemory` (max_messages=3,
keep_after_offload=1) with a goal, a scratchpad note, and messages, shows a direct
`write_tier(WORKING, ...)` is still refused through the seam, grows the buffer past its threshold,
calls `offload(provider=...)`, and reads `provider.assemble_context()` afterward.

**Result:** Verified end to end with no database, no API key, no network (a minimal `Model` double
stands in for `SessionSummaryManager`'s model call, the same one
`libs/agno/tests/unit/memory/test_working_memory.py` exercises under test). `should_offload()` is
`True` at the threshold; `offload` summarizes the two oldest messages, keeps the newest verbatim,
and writes the two offloaded messages through the provider as `EPISODIC` events -- confirmed by
reading them back out of the toy vector substrate. `assemble_context()` then renders the buffer's
goal/summary/note/message lines followed by the offloaded episode from a persistent-tier read.

**Requires:** Nothing. Run with `.venvs/demo`.

**Renumbered from `35_` to `37_` to `39_`: `feat/SM-L8` took `35_hierarchical_summarization.py`,
`feat/SM-H1` took `36_hybrid_retrieval_orchestration.py`, then `feat/SM-S2` took
`37_procedural_memory.py` and `feat/SM-S4` took `38_kb_and_memory_unified_retrieval.py` while
this branch was still catching up.** Per this file's own convention, whoever merges second
renumbers -- twice, this time.

---

### 40_user_profile_store.py

**Status:** PARTIAL

**Description:** Structured user profile updated on consolidation (SM-S3 #120): wires a
`UserProfileStore` (model-only, no `db`) into `CoLocatedMemoryProvider` via `profile_store=`,
writes one episode where Ada introduces herself and states a preferred name, consolidates, and
reads the resulting profile back through `SemanticProfileStore`.

**Result:** Ran end to end up to the real model call against a real pgvector
(`smv1-pgvector-5532`, table `memory_vectors_sm_s3`): the episodic write and the fact/entity
consolidation passes all succeeded, `_update_profile` was reached and called
`UserProfileStore.extract_profile`, which raised `ModelAuthenticationError` (`OPENAI_API_KEY not
set`) -- caught and re-raised as `PartialWriteError`, exactly the behavior `co_located.py`'s own
"a generic exception gives no reliable signal about which side of a fan-out it interrupted"
comment documents. **Not run against a real model**: no `OPENAI_API_KEY` was available in this
environment, the same gap `32_`/`33_` log. `UserProfileStore.extract_profile`/`aextract_profile`
(the storage-free extraction this file exercises) are covered against a fake model in
`libs/agno/tests/unit/learn/stores/test_user_profile_extract.py`, and the consolidation wiring
(`_update_profile`/`_aupdate_profile`, the receipt-checking and scope-normalization fixed across
QC rounds 2-4) against a stub `profile_store` in
`libs/agno/tests/unit/memory/test_provider_profile_hook.py` -- what remains unverified is
specifically the real-model extraction shape those doubles cannot guarantee to match. Re-run with
a real key and flip this entry to a full PASS/FAIL when it lands.

**Requires:** `./cookbook/scripts/run_pgvector.sh` and an `OPENAI_API_KEY`. Run with `.venvs/demo`
with `PYTHONPATH=libs/agno` set from a worktree (no editable install there -- see this repo's
`saturam-imp` skill).

**Renumbered from `34_` to `40_`: `feat/SM-L9` took `34_sleep_time_scheduling.py`, `feat/SM-L8`
took `35_hierarchical_summarization.py`, `feat/SM-H1` took `36_hybrid_retrieval_orchestration.py`,
`feat/SM-S2` took `37_procedural_memory.py`, `feat/SM-S4` took
`38_kb_and_memory_unified_retrieval.py`, and `feat/SM-C6` took `39_working_memory.py`, all while
this branch was still catching up.** Per this file's own convention, whoever merges second
renumbers.

---

### 41_ppr_multihop.py

**Status:** PASS

**Description:** Optional Personalized PageRank multi-hop retrieval (SM-H4 #134), off by default
(`multihop_ppr_enabled`). Writes a four-hop entity chain (Ada -> NorthStar -> Berlin -> Germany ->
EU) directly via `upsert_entities`/`upsert_facts`, then: (1) confirms plain BFS traversal
(`max_traverse_hops=2` default) cannot reach the fourth hop; (2) confirms the same query, with the
PPR leg enabled, does reach it; (3) invalidates the first edge and confirms PPR excludes it once
`as_of` passes its `t_invalid` bound, and includes it before that bound -- the bi-temporal
composition acceptance criterion.

**Result:** Run against a real GDS-plugin Neo4j (`./cookbook/scripts/run_neo4j_gds.sh`, GDS
2.13.11 Community): BFS alone reaches `{ada-works-at-northstar, northstar-located-in-berlin}`;
with PPR enabled the same query reaches all four edges including
`germany-member-of-eu`; the bi-temporal check reports `True` before the invalidation bound and
`False` after. No API key needed -- entities and fact edges are written directly, the same way
`28_` does. Re-verified after round-1, round-2, and round-3 review fixes (empty-anchor guard,
score ceiling, window overlap semantics, unique-per-call GDS catalog name, project-inside-try
cleanup, seeds-into-rank TOCTOU fix): unchanged output.

**Requires:** `./cookbook/scripts/run_neo4j_gds.sh` (a GDS-plugin Neo4j, not `28_`'s plain
instance); no API key. Run with `.venvs/demo`.

**Renumbered from `34_` to `35_` to `36_` to `37_` to `38_` to `41_` across five merges:
`feat/SM-L9` (#123) took `34_`, then `feat/SM-L8` (#127) took `35_`, then `feat/SM-H1` (#125) took
`36_`, then `feat/SM-S2` (#133) took `37_`, then `feat/SM-S4`/`feat/SM-C6`/`feat/SM-S3` took
`38_`/`39_`/`40_` together, all while this branch was still catching up.**

---

### 42_rrf_fusion.py

**Status:** PASS

**Description:** Reciprocal Rank Fusion (SM-H2 #132): the same "cat dog" / `higher_overlap` vs
`has_phrase` scenario `36_hybrid_retrieval_orchestration.py` uses, read three times against the
same two in-memory records with `config.fusion` set to `{}` (default), `{"method": "rrf"}`, and
`{"method": "rrf", "k": 1}`.

**Result:** Ran end to end, no database, no model. `score_sum` (default) ranks `higher_overlap`
first and reports `method="score_sum"`, matching `36_`'s own run. `rrf` (`k=60`) flips the order --
`has_phrase` first -- and reports `method="rrf"`; `lexical_hits=1` in both runs, confirming the
leg ran identically and only the fusion strategy's use of it differed. `k=1` reaches the fusion (the
fused scores visibly change) without flipping this fixture's ranking again.

**Requires:** Nothing; no database, no API key, no network.

**Renumbered from `41_` to `42_`: `feat/SM-H4` took `41_ppr_multihop.py` while this branch was
still catching up. Per this file's own convention, whoever merges second renumbers.**

---

### 43_mode_a_end_to_end.py

**Status:** PASS

**Description:** Mode A -- co-located memory (SM-X1 #137) -- pgvector and Neo4j wired into one
`CoLocatedMemoryProvider` by config alone (`mode=ProviderMode.LOCAL`), then driven through the full
episodic -> semantic pass: write two identical episodes, `consolidate` (deterministic dedup, no
extraction pipeline wired), read the resulting fact back fused across both substrates.

**Result:** Run against real Postgres+pgvector (`./cookbook/scripts/run_pgvector.sh`) and real
Neo4j (`./cookbook/scripts/run_neo4j.sh`), with the real Mode A default embedder
(`bge-base-en-v1.5`, downloaded on first run, same as `13_`): `substrates()` reports both wired;
2 episodic rows written; `consolidate` reports `episodic_scanned=2`, `duplicates_merged=1`,
`semantic_written=1`; the fused read returns exactly the one distilled fact, present in both the
vector and graph legs (`fusion["vector_hits"] == fusion["graph_hits"] == 1`). Re-run twice with no
code change: identical output both times (stable ids upsert the same rows rather than
accumulating a fresh set).

**Requires:** `./cookbook/scripts/run_pgvector.sh`, `./cookbook/scripts/run_neo4j.sh`; no API key.
Run with `.venvs/demo`.

**Renumbered from `42_` to `43_`: `feat/SM-H2` took `42_rrf_fusion.py` while this branch was still
catching up. Per this file's own convention, whoever merges second renumbers.**

---

### 44_time_aware_ranking.py

**Status:** PASS

**Description:** Time-aware blended ranking (SM-H5 #135): `rank()` over a plain in-memory
`MemoryRecord` list -- an explicit time-window pre-filter, then SM-E7's composite score (`26_`)
blended into every episodic record's fused score. Five sections: a three-episode fixture ranked by
composite score, the temporal filter run once with no window and once with a window that drops an
out-of-range episode, a nonzero recency weight shown to change nothing (the composite's recency
*signal* is pinned to `0.0`), and the refusal an episodic record with no `importance` in metadata
gets.

**Result:** Ran end to end, no database, no model, no network. Fixture ranked `shipped_v2` (1.75)
> `status_ping` (0.80) > `small_talk` (0.25), matching the issue's own Testing Note ("a recent +
important + relevant episode ranks correctly"). The unscoped query kept both dated episodes; the
windowed query kept only `today`. `recency=0.0` and `recency=5.0` both scored `1.00` on the same
record -- confirms the recency signal, not just the weight, is what is pinned. The malformed
record raised `MemoryOperationError` naming `field_name="importance_metadata"` -- distinct from
`composite_episodic_score`'s own `field_name="importance"` for a present-but-out-of-range value
(PR #146 review round 1), and from `field_name="importance_metadata_type"` for a present-but-
non-numeric value, and `field_name="score"` for a non-finite fused score (PR #146 review round
2 -- `_clamp_unit` now refuses `NaN`/`+-inf` rather than silently mapping `NaN`/`+inf` to `1.0`,
the maximum relevance, under plain `min`/`max`).

**Requires:** Nothing; no database, no API key, no network.

**Renumbered from `43_` to `44_`: `feat/SM-X1` took `43_mode_a_end_to_end.py` while this branch
was still catching up. Per this file's own convention, whoever merges second renumbers.**

---

### 45_token_bounded_memory_slice.py

**Status:** PASS

**Description:** Token-bounded `MemorySlice` assembly + context injection (SM-H6 #136):
`provider.assemble_context(..., max_tokens=...)` against the same toy vector substrate
`39_working_memory.py` uses, read three times -- unbounded (the pre-SM-H6 default,
`max_tokens=None`), bounded to fit the working buffer plus exactly one ranked record, and bounded
to `0`.

**Result:** Ran end to end, no database, no model. Unbounded appends all three ranked records
after the working buffer. Bounded-to-one-record keeps only the top-ranked episode, matching the
ranking `36_`/`42_` already produce -- a prefix cut, not a re-rank. Bounded-to-zero renders the
working buffer (goal) alone and drops every persistent record, confirmed by an in-file assertion
that the working half is never truncated even when the whole budget is spent on it.

**Round-4 review fix (PR #147):** the file originally imported its cross-cookbook dependency as
`import_module("cookbook.11_memory.39_working_memory")`, which needs the repo root on `sys.path`
-- present when a caller already has `cookbook` importable, but *not* provided by running the
script directly (`.venvs/demo/bin/python cookbook/11_memory/45_....py`, the documented
invocation), which only prepends the script's own directory. Reproduced live as
`ModuleNotFoundError: No module named 'cookbook'`. Fixed to match `gemini_3/21_agent_os.py`'s own
precedent: `sys.path.insert(0, str(Path(__file__).parent))` + bare-module-name
`import_module("39_working_memory")`. Also fixed the cookbook's own token-budget arithmetic, which
shared the additive-sum bug the round-3 fix closed in `bound_by_tokens` itself (see round-3 entry
above) -- it was silently excluding the intended top-ranked record from the "bounded" demo output.
Re-verified via the exact documented invocation from a clean `PYTHONPATH` matching a real
single-checkout deployment (`PYTHONPATH=libs/agno .venvs/demo/bin/python
cookbook/11_memory/45_....py`, exit 0, correct output). Note: run bare (no `PYTHONPATH`) from
*this development worktree specifically*, the script still raises `TypeError:
MemoryProvider.assemble_context() got an unexpected keyword argument 'max_tokens'` -- not a code
defect, but this worktree's editable `.venvs/demo` install pointing at the main checkout's
`libs/agno`, which does not yet have this PR's `provider.py` changes (per `CLAUDE.md`'s own "the
editable install points at the main checkout" caveat for worktrees). A single-checkout deployment
(a fresh clone, CI, or this worktree once merged) does not have that mismatch.

**Requires:** Nothing; no database, no API key, no network. Runs correctly whether or not
`tiktoken` is installed (`count_text_tokens` falls back to a character-based estimate).

**Renumbered from `43_` to `45_`: `feat/SM-X1` took `43_mode_a_end_to_end.py` and `feat/SM-H5`
took `44_time_aware_ranking.py` while this branch was still catching up. Per this file's own
convention, whoever merges second renumbers.**

---

### 46_rerank.py

**Status:** PASS

**Description:** Optional cross-encoder rerank stage after fusion (SM-H3 #133): three reads
against the same two in-memory vector records with `config.rerank` set to `{}` (default, off),
`{"enabled": True, "reranker": PhraseFlipReranker()}` (a fast deterministic double), and
`{"enabled": True, "reranker": SlowReranker(delay_s=1.0), "budget_ms": 50}` (a reranker slower
than its own budget).

**Result:** Ran end to end, no database, no model. Off: fused (word-overlap) order stands,
`overlap_only` first, no `rerank_*` keys in `fusion` (the extras `fusion_diagnostics` folds in are
absent when the stage never ran). On with the fast reranker: order flips to `has_phrase` first
(the phrase-containment signal the reranker scores on, which word-overlap cannot see) and reports
`rerank_applied=True`, `rerank_budget_exceeded=False`. On with the slow reranker and a 50ms
budget: falls back to the fused order and reports `rerank_budget_exceeded=True`; wall time
(~55-60ms) tracks the budget, not the reranker's 1s delay, confirming the call was abandoned
rather than merely timed.

**Requires:** Nothing; no database, no API key, no network.

**Renumbered from `43_` to `46_`: `feat/SM-X1`/`feat/SM-H5`/`feat/SM-H6` took
`43_mode_a_end_to_end.py`/`44_time_aware_ranking.py`/`45_token_bounded_memory_slice.py` while
this branch was still catching up. Per this file's own convention, whoever merges second
renumbers.**

---

### 47_per_tenant_memory_config.py

**Status:** PASS

**Description:** SM-C5's per-tenant resolution seam (#93). Builds one `MemoryConfigResolver` over
four tenants with genuinely different deployments -- an air-gapped Mode A customer with a graph
backend, a hosted Mode B vector-only customer, an enterprise tenant with one agent pinned to its
own store beside a tenant-wide policy, and a `default` for everyone else -- then shows the
decisions the seam makes: which config answers for whom, the candidate probes in
most-specific-first order, the per-tenant graph toggle read off the built provider's
`substrates()`, the tenant-wildcard-beats-agent-wildcard precedence, and identity padding
resolving to the same policy rather than a second tenant. Closes with the five refusals
(duplicate spelling, `(None, None)` shadowing `default`, blank policy key, non-string identity,
unmatched identity) and the required-`tenant_id` build path.

**Result:** Runs clean. `enterprise/research-agent` resolves to the exact-pair policy while
`enterprise/some-other-agent` falls to the tenant-wide one; the probe list marks the exact pair
`<-- wins` and the tenant-wide key `(also defined, but never reached)`, which is the point of
most-specific-first. `airgapped-bank` reports `vector + graph` and `hosted-startup` reports
`vector-only` through `provider.substrates()`, not through the dict the example passed in.
`lookup('airgapped-bank ')` returns the same Mode A policy rather than the Mode B default. All
five misconfigurations raise `MemoryProviderConfigError` with a `field_name` and a message naming
what collided, and `build_memory_provider_for(resolver)` with no tenant raises `TypeError` while
`resolver.lookup()` still answers for diagnostics.

**Requires:** Nothing; no database, no API key, no network.

---

### 48_memory_cost_and_growth.py

**Status:** PASS

**Description:** SM-X8's embedding + extraction cost and per-tenant store-growth metering, driven
through the real production hooks rather than the aggregator API directly: `MemoryEmbedder.
embed_texts(scope=...)` for embedding cost, `LLMEntityExtractor.extract(episode)` for extraction
cost, and `CoLocatedMemoryProvider.write`/`consolidate` for store growth. A demo embedder and a
runtime-registered demo extraction model, neither reaching the network, give `compute_cost` a
synthetic price row to find (mirroring `test_pricing.py`'s pattern, since the shipped `PRICES`
table is intentionally empty). Two tenants (`acme`, `globex`) exercise all three hooks.

**Result:** Runs clean. Embedding calls for both tenants land 3 cost entries; entity extraction
adds 2 more (5 total); write + consolidate across both tenants records 7 growth entries.
`cost_by_tenant()` reports a nonzero cost for both `acme` and `globex` (both demo components are
priced), `growth_by_tenant()` reports `acme=6`, `globex=4` semantic facts written, and 0 entries
came back unpriced (`cost=None`) since every component in this run has a synthetic price row.

**Requires:** Nothing; no database, no API key, no network.

---
