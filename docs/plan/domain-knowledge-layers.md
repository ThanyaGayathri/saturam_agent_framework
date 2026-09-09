# Domain Knowledge Layers

_Domain Knowledge Layers_

> Seven layers in three groups, separated by where the knowledge comes from and who is accountable for it being right. Everything is reached through one retrieval path, not seven.

- **1 · Domain prose** — Curated retail writing. What a metric means, why a pattern occurs, what good looks like. Split on headings so a section stays whole. `Postgres · pgvector`
- **2 · Domain ontology** — Retail concepts, the relations between them, and the words people actually use for each. Types only, never instances. `Neo4j · ontology database`
- **3 · Semantic layer** — Their tables, columns and joins, read from the warehouse. `Neo4j · own labels`
- **4 · Enrichment** — Per concept: synonyms, the questions people ask of it, the filter that scopes it, personal-data fields, compliance tags. `Neo4j · own labels`
- **5 · Mapping** — Edges joining their tables to our concepts. This deals table is a Quote. Everything the ontology knows then applies to it. `Neo4j · edges`
- **6 · KPI definitions** — Formula, grain, source tables, and whether their data can actually support it. `Postgres · mirrored in Neo4j`
- **7 · Memory** — What happened before and what was established as true here. Episodic holds past runs and conversations, semantic holds durable facts about this customer, procedural holds rules that worked. `Postgres + Neo4j · memory database`
- **Customer documents** — Their own material, ingested alongside our prose and tagged by origin so an answer can say which it came from. `Postgres · pgvector`

## Who is accountable for each

The distinction that matters commercially is the middle group. The first is our product, the third accumulates on its own, and the second is the work.

| Layer | Built by | Right or wrong is judged by | Changes when |
|---|---|---|---|
| **Domain prose** | Us, once | A retail practitioner reading it | We release a new version |
| **Domain ontology** | Us, once | A retail practitioner reading it | We release a new version |
| **Semantic layer** | Read from their warehouse | Matching their schema exactly | Their schema changes |
| **Enrichment** | Proposed by an agent, confirmed by their team | Their team, at review | Someone corrects it, or new data arrives |
| **Mapping** | Proposed by an agent, confirmed by their team | Their team, at review | A new source is connected |
| **KPI definitions** | Suggested by an agent, adopted by their team | The number matching what finance says | A definition is disputed or the schema moves |
| **Memory** | The system, as it runs | Whether recall is useful and correct | Continuously |

### The middle group is the moat

Groups one and three are not differentiators. Anyone can write retail prose, and every system accumulates history. What is hard to copy is the middle: an accurate map of one organisation's data, expressed in that organisation's own vocabulary, with the filters and sensitivities that only apply to them. It cannot be bought, and it does not transfer to a competitor's deployment.

### Nothing enters the middle group unreviewed

Every enrichment and every mapping arrives as a proposal and waits for a person to accept it. This is deliberate rather than cautious. A wrong synonym or a wrong filter does not fail loudly; it silently changes the meaning of every answer built on top of it afterwards. Review is cheap, and reviewing in bulk is cheaper still.

### Concepts live in the graph, records stay in the warehouse

The graph holds types and categories. Customer rows, order lines and transactions never enter it. That keeps the graph small enough to traverse quickly, keeps sensitive records in the system that already governs them, and means the knowledge layers describe the data rather than duplicating it.

> **The one number to remember**
>
> Seven layers, one retrieval path. The layering is how the knowledge is built and governed. It is not something a user, or an agent asking a question, ever has to think about.
