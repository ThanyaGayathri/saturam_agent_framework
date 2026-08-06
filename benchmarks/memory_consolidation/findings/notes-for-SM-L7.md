# Findings & notes for the SM-L7 implementation phase

No core bug required a local fix during this spike — `libs/agno/egpt/**` ran as-is,
so there is **no `.patch` in this folder**. The notes below are design/plumbing
observations for whoever implements SM-L7.

## 1. Core lacks an Azure *Responses* model class

`egpt.models.azure` only has `AzureOpenAI` (a Chat-completions `OpenAILike`,
`models/azure/openai_chat.py:20`) — there is no Azure **Responses API** class,
while the house rule is "Responses, not Chat". The harness added a thin
`AzureOpenAIResponses(OpenAIResponses)` that overrides `get_client`/`get_async_client`
to return the SDK's `AzureOpenAI`/`AsyncAzureOpenAI` clients (which expose
`.responses`). See `harness/llm.py`.

**Recommendation:** promote a first-class `AzureOpenAIResponses` into
`egpt/models/azure/` so on-prem/Azure tenants can follow the Responses rule
without a shim. Env vars used: `AZURE_OPENAI_API_KEY/ENDPOINT/API_VERSION/DEPLOYMENT`.

## 2. Provenance needs a `UserMemory` schema extension

`db/schemas/memory.py:9` `UserMemory` has no provenance/bi-temporal fields. SM-L7
should add (see RESEARCH.md §7): `source_episode_ids: list[str]`,
`valid_from`/`valid_to`, `superseded_by`, `derived_by`, `importance`. Storage of
the `fact <-> episode` link is an SM-R3 decision (join table vs graph edge).

## 3. Merge prompts must reference neighbours by index, not by id

Early harness runs embedded random `fact_id`s in the merge prompt, which made the
prompt (and the record/replay cache key) non-deterministic and broke
reproducibility. Fix: number the candidate neighbours and have the LLM return a
`target_index`, resolved to an id in code (`strategies/base.py`). Keep this in the
core merge prompt so consolidation is reproducible and cacheable.

## 4. Reuse `LearnedKnowledgeStore` for reflection, build merge fresh

Per AC#5 (RESEARCH.md §11): wire the reflection layer to
`LearnedKnowledgeStore._extract_learnings`/`_summarize_existing`
(`learn/stores/learned_knowledge.py:1109,1243`); implement the ADD/UPDATE/DELETE/
NOOP provenance merge as new code (seed: `memory/manager.py:967`).

## 5. Evaluation caveats to carry into SM-R13

- Recall/conflict/QA saturate on small corpora with a strong reader — gate on
  **store-quality** (provenance precision, dedup, noise, footprint) and **scale**.
- Replace the lexical retrieval stub with real semantic/graph retrieval before
  trusting QA numbers; it currently understates strategies whose phrasing diverges
  from the question.
- Broaden beyond one synthetic scenario + one LOCOMO sample; consider LongMemEval
  for temporal/knowledge-update stress.
