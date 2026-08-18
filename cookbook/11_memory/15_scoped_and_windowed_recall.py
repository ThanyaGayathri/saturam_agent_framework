"""Scoped, time-windowed vector recall (SM-V4 #98).

Runs against the same infrastructure as `13_vector_substrate_pgvector.py`:

    ./cookbook/scripts/run_pgvector.sh

That file shows the backend is a config edit. This one shows what the backend does with the
question: tenant, user, tier and a valid-time window are all applied **inside** the search, in the
same WHERE clause as the similarity ordering.

Applying them afterwards is the difference between a filtered page and a short one. A read whose
best matches all sit outside the window returns in-window rows here; a post-filtering
implementation spends the limit first and returns nothing, which reads as an empty corpus rather
than as a filter doing its job.

**The window is about validity, not authorship.** `valid_from`/`valid_to` is half-open, in epoch
seconds, and selects records whose valid-time interval *overlaps* it -- not records written during
it. A memory with no end date is an open-ended interval, so it overlaps every later window and
keeps answering until something bounds it. That is the point of bi-temporal storage and it is what
the third read below demonstrates; bounding a memory is the graph's job (SM-G5 #103), not a
property a vector row decides about itself.

`as_of` is the other question -- which revision is believed at one instant -- and needs a graph leg
to answer. The two are refused together, because a provider would otherwise have to intersect them.
"""

import os

from egpt.integrations.knowledge.embedder.sentence_transformer import (
    SentenceTransformerEmbedder,
)
from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")

# Quarter boundaries as epoch seconds. Constants rather than wall-clock arithmetic, so this file
# prints the same thing on every run.
Q1 = 1_735_689_600  # 2025-01-01
Q2 = 1_743_465_600  # 2025-04-01
Q3 = 1_751_328_000  # 2025-07-01
Q4 = 1_759_276_800  # 2025-10-01

config = MemoryProviderConfig(
    provider="co_located",
    mode=ProviderMode.LOCAL,
    vector={
        "backend": "pgvector",
        "db_url": os.getenv(
            "EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
        ),
        # Its own table, so the reads below show this file's five rows and not whatever else a
        # shared default table has accumulated from the other cookbooks and the test suite.
        "table": "cookbook_windowed",
        "embedder": SentenceTransformerEmbedder(
            id="BAAI/bge-base-en-v1.5", dimensions=768
        ),
        "ef_search": 256,
    },
)

provider = build_memory_provider(config)

# Guarded from here on: every write and read below can fail transiently against a real database,
# and an exception escaping mid-script would leak the connection pool `provider.close()` exists to
# release.
try:
    # Ids are stated so a second run replaces these rows rather than accumulating a fresh set.
    # `occurred_at` is valid time -- when it was true -- and is what the window filters on.
    history = [
        ("ada-q1", "ada joined the berlin office", ACME_ADA, Q1),
        ("ada-q2", "ada leads the payments team", ACME_ADA, Q2),
        ("ada-q3", "ada relocated to the lisbon office", ACME_ADA, Q3),
        ("bob-q3", "bob relocated to the lisbon office", ACME_BOB, Q3),
        ("other-q3", "ada relocated to the lisbon office", GLOBEX, Q3),
    ]
    for memory_id, content, scope, occurred_at in history:
        provider.write(
            scope,
            [
                MemoryEvent(
                    id=memory_id, content=content, scope=scope, occurred_at=occurred_at
                )
            ],
        )

    question = "where does ada work"
    episodic = {"tiers": [MemoryTier.EPISODIC]}

    def show(label, memory_slice):
        print(f"\n{label}")
        for record in memory_slice.records:
            print(f"  {record.id}: {record.content}")

    show(
        "Unwindowed -- everything acme/ada can read:",
        provider.read(ACME_ADA, MemoryQuery(text=question, **episodic)),
    )

    # Upper bound only: nothing that became true at or after Q3 is a candidate. The lisbon
    # relocation starts exactly at Q3, and the window is half-open, so it is excluded.
    show(
        "valid_to=Q3 -- what was true before July:",
        provider.read(ACME_ADA, MemoryQuery(text=question, valid_to=Q3, **episodic)),
    )

    # Lower bound only, and the interesting case: the two earlier memories come back as well.
    # Neither has an end date, so both are still true in the second half of the year -- joining the
    # berlin office in Q1 does not stop being true because a later memory was written. A window is
    # not a created-at filter, and nothing here has claimed the earlier facts ended.
    show(
        "valid_from=Q3 -- what is true from July onward:",
        provider.read(ACME_ADA, MemoryQuery(text=question, valid_from=Q3, **episodic)),
    )

    # The window narrows time and nothing else. Every scope slot still applies, so acme/bob never
    # sees ada's rows and globex never sees acme's -- a filter that widened the scope while
    # narrowing the time would be a leak that looks like a feature.
    print("\nThe same window [Q3, Q4), read by each scope:")
    windowed = MemoryQuery(text=question, valid_from=Q3, valid_to=Q4, **episodic)
    for label, scope in (
        ("acme/ada", ACME_ADA),
        ("acme/bob", ACME_BOB),
        ("globex/ada", GLOBEX),
    ):
        print(f"  {label}: {[r.id for r in provider.read(scope, windowed).records]}")

    # `dropped_outside_window` is reported separately from `dropped_by_validity`: one says the
    # caller asked a narrower question, the other says the world moved on. Both zero here means the
    # substrate filtered in-search and the provider had nothing left to drop -- the whole design.
    print("\nDiagnostics for the windowed read:")
    diagnostics = provider.read(ACME_ADA, windowed).fusion
    for key in (
        "vector_hits",
        "returned",
        "dropped_outside_window",
        "dropped_by_validity",
    ):
        print(f"  {key}: {diagnostics[key]}")

    # One valid-time question per read. Both set would have to be intersected, and an empty
    # intersection comes back as a plain miss rather than as the bad request it is.
    try:
        MemoryQuery(text=question, as_of=Q2, valid_from=Q3, valid_to=Q4)
    except ValueError as error:
        print(f"\nas_of together with a window: refused -- {type(error).__name__}")
finally:
    provider.close()
