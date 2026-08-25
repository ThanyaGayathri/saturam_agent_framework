"""
Token-Bounded MemorySlice Assembly + Context Injection (SM-H6)
================================================================

`36_hybrid_retrieval_orchestration.py` and `42_rrf_fusion.py` show how a read
fuses the vector/lexical/graph legs into one cross-tier-**ranked**
`MemorySlice`; `39_working_memory.py` shows `provider.assemble_context()`
appending that slice, in full, after a `WorkingMemory` buffer. Neither bounds
the result by how many tokens it actually costs the prompt it lands in --
`MemoryQuery.limit` caps `MemorySlice.records` by *count*, and a count has no
relationship to token cost.

This file shows the piece SM-H6 adds: `assemble_context(..., max_tokens=...)`.
The ranking itself is unchanged (still RRF/`score_sum`, still SM-H2's job) --
what is new is a token-aware **prefix cut** of that ranking, spending the
budget on the (never-truncated) working buffer first and handing whatever
remains to the persistent slice.

Self-contained: no database, no model, no network. `tiktoken` is not
required -- `count_text_tokens` falls back to a `len(text) // 4` estimate
when it is absent, so the token counts below are approximate but stable.
"""

# The same toy vector substrate `39_working_memory.py` uses.
from importlib import import_module

from egpt.memory import (
    MemoryEvent,
    MemoryProviderConfig,
    MemoryQuery,
    Scope,
    WorkingMemory,
    build_memory_provider,
)
from egpt.utils.tokens import count_text_tokens

_working_memory_demo = import_module("cookbook.11_memory.39_working_memory")
InMemoryVector = _working_memory_demo.InMemoryVector


if __name__ == "__main__":
    scope = Scope(tenant_id="acme", user_id="ada@example.com")
    vector = InMemoryVector()
    provider = build_memory_provider(
        MemoryProviderConfig(
            provider="co_located", options={"vector_substrate": vector}
        )
    )

    # Three episodes ranked by lexical overlap with the query below -- see `InMemoryVector.search`
    # in `39_working_memory.py`: it matches on the query's first word.
    provider.write(
        scope,
        [
            MemoryEvent(id="e1", content="pricing starts at $10 per seat", scope=scope),
            MemoryEvent(
                id="e2",
                content="pricing tiers are Starter, Pro, Enterprise",
                scope=scope,
            ),
            MemoryEvent(
                id="e3", content="pricing support is available by chat", scope=scope
            ),
        ],
    )

    working = WorkingMemory(scope=scope, max_messages=40)
    working.set_goals(["answer Ada's pricing question"])
    query = MemoryQuery(text="pricing", limit=5)

    print(
        "Unbounded (max_tokens=None, the pre-SM-H6 default) -- every ranked record appended:"
    )
    unbounded = provider.assemble_context(scope, working, query)
    print(unbounded)
    print(f"  ~{count_text_tokens(unbounded)} tokens")

    print("\nToken-bounded -- just enough budget for the working half plus one record:")
    working_only = working.as_context()
    one_record_budget = count_text_tokens(working_only) + count_text_tokens(
        "[episodic] pricing starts at $10 per seat"
    )
    bounded = provider.assemble_context(
        scope, working, query, max_tokens=one_record_budget
    )
    print(bounded)
    print(
        f"  budget: {one_record_budget} tokens -- only the top-ranked record made it in"
    )

    print(
        "\nToken-bounded to zero -- the working half is never truncated, even at budget 0:"
    )
    zero_budget = provider.assemble_context(scope, working, query, max_tokens=0)
    print(zero_budget)
    assert zero_budget == working_only, (
        "the working half must survive an exhausted budget"
    )
