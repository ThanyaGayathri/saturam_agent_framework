"""
The WORKING Tier's Own Buffer
==============================

This example shows the WORKING-tier buffer (SM-C6): a per-run context window,
scratchpad and goal list that is never itself persisted, and its
summarize/offload-when-large policy.

Key concepts:
- `TIER_POLICY[WORKING]` (SM-C2, `11_memory_tiers.py`) routes to no substrate
  and names this ticket as owner. `WorkingMemory` is that row made concrete:
  it holds a run's context, but a direct write to WORKING through the seam is
  still refused, exactly as it was before this file existed.
- `should_offload()` triggers once the buffer reaches `max_messages`.
  `offload()` reuses `SessionSummaryManager` (the same manager
  `session/summary.py` already uses to compact a whole session) rather than
  reinventing summarization.
- When a `MemoryProvider` is passed to `offload`, the overflow is *also*
  written through the provider's ordinary `write()` as EPISODIC events before
  being summarized away -- this is the literal boundary crossing into
  long-term memory, and it is the same write path any other caller uses, not
  a second one.
- `provider.assemble_context()` renders the buffer next to a persistent-tier
  read into one prompt string -- the WORKING half of SM-H6's future
  retrieval assembly.

The vector substrate is the same tiny in-memory stand-in `11_memory_tiers.py`
uses. Summarization needs a `Model`, but this file's subject is the buffer's
policy, not a model call, so a minimal double stands in for one -- the same
role `unittest.mock.Mock(spec=Model)` plays in
`libs/agno/tests/unit/memory/test_working_memory.py`. This needs no
database, no API key, and no network.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple
from unittest.mock import Mock

from egpt.memory import (
    DEFAULT_WRITE_TIER,
    MemoryOperationError,
    MemoryProviderConfig,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    WorkingMemory,
    build_memory_provider,
    policy_for_tier,
    require_scope,
)
from egpt.models.base import Model
from egpt.models.message import Message
from egpt.session.summary import SessionSummaryManager, SessionSummaryResponse

# ---------------------------------------------------------------------------
# A toy vector substrate -- the same shape 11_memory_tiers.py uses
# ---------------------------------------------------------------------------


class InMemoryVector:
    def __init__(self) -> None:
        self.rows: Dict[str, MemoryRecord] = {}

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        for record in records:
            self.rows[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        self.upsert(scope, records)

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        wanted = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(record, score=1.0)
            for record in self.rows.values()
            if record.tier in wanted and query.text.split()[0] in record.content
        ]
        return hits[: query.limit] if query.limit else []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        return self.search(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        rows = [replace(r) for r in self.rows.values() if r.tier is tier]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        return rows if limit is None else rows[:limit]

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)


def stub_summary_model(summary: str) -> Model:
    """A minimal `Model` double so this example needs no network or API key.

    `SessionSummaryManager` only ever calls `.response()`/`.aresponse()` and reads
    `.supports_native_structured_outputs` -- see
    `libs/agno/tests/unit/memory/test_working_memory.py`'s `fake_model` for the same double
    exercised under test.
    """
    model = Mock(spec=Model)
    model.supports_native_structured_outputs = True
    model.supports_json_schema_outputs = False
    parsed = SessionSummaryResponse(summary=summary, topics=["pricing"])
    model.response = Mock(return_value=Mock(content=None, parsed=parsed))
    return model


if __name__ == "__main__":
    scope = Scope(tenant_id="acme", user_id="ada@example.com")

    # -----------------------------------------------------------------
    # WORKING persists nowhere -- the row this class makes concrete
    # -----------------------------------------------------------------
    policy = policy_for_tier(MemoryTier.WORKING)
    print("WORKING tier policy:")
    print("  substrates:", policy.substrates or "none (in-context only)")
    print("  owner:     ", policy.owner)

    vector = InMemoryVector()
    provider = build_memory_provider(
        MemoryProviderConfig(
            provider="co_located", options={"vector_substrate": vector}
        )
    )

    # A direct write to WORKING is still refused through the seam -- WorkingMemory does not
    # change that, it lives entirely alongside it.
    try:
        provider.write_tier(scope, MemoryTier.WORKING, [])
    except MemoryOperationError as error:
        print("  a direct write_tier(WORKING, ...) is refused:", error)

    # -----------------------------------------------------------------
    # The buffer: context, scratchpad, goals
    # -----------------------------------------------------------------
    print()
    print("Buffer contents:")
    working = WorkingMemory(
        scope=scope,
        max_messages=3,
        keep_after_offload=1,
        summary_manager=SessionSummaryManager(
            model=stub_summary_model("Ada asked about pricing tiers.")
        ),
    )
    working.set_goals(["answer Ada's pricing question"])
    working.note("Ada is on the free plan today")
    working.add_message(Message(role="user", content="What plans do you offer?"))
    print(working.as_context())

    # -----------------------------------------------------------------
    # Summarize/offload-when-large
    # -----------------------------------------------------------------
    print()
    print("Growing the buffer past max_messages:")
    working.add_message(
        Message(role="assistant", content="We have Starter, Pro, and Enterprise.")
    )
    working.add_message(
        Message(
            role="user", content="What's the difference between Pro and Enterprise?"
        )
    )
    print("  should_offload():", working.should_offload())

    # A provider passed to offload() writes the overflow through as ordinary EPISODIC events --
    # long-term storage learns about it through the same write() path any other caller uses.
    summary = working.offload(provider=provider)
    print("  summary:", summary.summary if summary else None)
    print(
        "  messages kept verbatim after offload:", [m.content for m in working.messages]
    )
    print(
        "  overflow reached long-term storage as EPISODIC events:",
        [r.content for r in vector.rows.values() if r.tier is DEFAULT_WRITE_TIER],
    )

    # -----------------------------------------------------------------
    # Assembling context for a model call -- the WORKING half of SM-H6
    # -----------------------------------------------------------------
    print()
    print("assemble_context(): buffer, then a persistent-tier read")
    # The toy substrate's `search` matches on its query's first word (see `InMemoryVector.search`
    # above), so the query below is picked to hit the offloaded episode -- an empty-text query
    # would not, since it is a real similarity query with nothing to match (see
    # `MemoryProvider.assemble_context`'s docstring for why that is not "unrestricted").
    rendered = provider.assemble_context(scope, working, MemoryQuery(text="Enterprise"))
    print(rendered)
