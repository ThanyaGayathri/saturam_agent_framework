"""Bi-temporal scenario set (SM-R10) -- the eval data.

Each scenario is a constructed world history (a sequence of assertions/retractions) plus
point-in-time probes whose expected answers are derived **by hand from the world timeline**,
independently of any store implementation. All backends (reference, Postgres, Neo4j) and both
options (bi-temporal A vs single-time B) are scored against these same ground-truth answers,
so the metric is not circular.

Data-quality note: the answers are mechanically determined by the constructed timeline (given
a known sequence of world events, "what was true at T" has one correct value), so labels are
objective, not subjective judgments. The set covers the load-bearing bi-temporal cases:
insert, real-world supersession, idempotent NOOP, multi-step chains, back-dated ("since Jan")
backfill, error retraction, open intervals, predicate/tenant isolation, and belief-evolution
(transaction-time) probes. Scale is provisional (small, illustrative) -- flagged for SM-R1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# Named epoch-second anchors (2024 months, UTC) for readable timelines.
DEC23 = 1_701_388_800  # 2023-12-01
JAN = 1_704_067_200  # 2024-01-01
FEB = 1_706_745_600  # 2024-02-01
MAR = 1_709_251_200  # 2024-03-01
JUN = 1_717_200_000  # 2024-06-01
JUL = 1_719_792_000  # 2024-07-01
SEP = 1_725_148_800  # 2024-09-01
DEC = 1_733_011_200  # 2024-12-01


@dataclass
class Op:
    """A single write against the store, in timeline order."""

    kind: str  # "assert" | "retract"
    subject: str = ""
    predicate: str = ""
    object: str = ""
    user: str = "u1"
    t_valid: Optional[int] = None  # world-time the fact became true
    at: Optional[int] = None  # transaction-time of this write (created_at / expired_at)
    edge_id: Optional[str] = None  # explicit id (so a later retract can reference it)


@dataclass
class Probe:
    """A read whose expected answer is fixed by the world timeline."""

    subject: str
    predicate: str
    user: str
    t: int  # world-time
    expect: Optional[str]  # expected object_id per current belief (None = no fact)
    tt: Optional[int] = None  # if set -> bi-temporal 5b probe ("as believed at tt")
    note: str = ""


@dataclass
class Scenario:
    name: str
    ops: List[Op]
    probes: List[Probe]
    description: str = ""
    tests_correction: bool = False  # true => a single-time store (Option B) cannot satisfy it
    tests_bitemporal: bool = False  # true => requires the transaction axis (5b)


def build_scenarios() -> List[Scenario]:
    S: List[Scenario] = []

    # 1. Simple insert + open interval.
    S.append(
        Scenario(
            name="simple_insert",
            description="One fact, no correction; valid from Jan onward.",
            ops=[Op("assert", "user", "lives_in", "NYC", t_valid=JAN, at=JAN)],
            probes=[
                Probe("user", "lives_in", "u1", DEC23, None, note="before valid start"),
                Probe("user", "lives_in", "u1", FEB, "NYC"),
                Probe("user", "lives_in", "u1", DEC, "NYC", note="open interval -> still true"),
            ],
        )
    )

    # 2. Real-world supersession (relocation).
    S.append(
        Scenario(
            name="supersede_relocation",
            description="NYC since Jan, then LA since Jun -> invalidate NYC, do not delete.",
            tests_correction=True,
            ops=[
                Op("assert", "user", "lives_in", "NYC", t_valid=JAN, at=JAN),
                Op("assert", "user", "lives_in", "LA", t_valid=JUN, at=JUN),
            ],
            probes=[
                Probe("user", "lives_in", "u1", FEB, "NYC", note="past truth preserved"),
                Probe("user", "lives_in", "u1", JUL, "LA", note="current truth"),
                Probe("user", "lives_in", "u1", DEC23, None),
            ],
        )
    )

    # 3. Idempotent NOOP.
    S.append(
        Scenario(
            name="idempotent_noop",
            description="Re-asserting the same current fact is a NOOP; still one answer.",
            ops=[
                Op("assert", "user", "lives_in", "LA", t_valid=JUN, at=JUN),
                Op("assert", "user", "lives_in", "LA", t_valid=SEP, at=SEP),
            ],
            probes=[Probe("user", "lives_in", "u1", DEC, "LA")],
        )
    )

    # 4. Three-way chain.
    S.append(
        Scenario(
            name="three_way_chain",
            description="NYC -> LA -> SF; each window reconstructs correctly.",
            tests_correction=True,
            ops=[
                Op("assert", "user", "lives_in", "NYC", t_valid=JAN, at=JAN),
                Op("assert", "user", "lives_in", "LA", t_valid=JUN, at=JUN),
                Op("assert", "user", "lives_in", "SF", t_valid=SEP, at=SEP),
            ],
            probes=[
                Probe("user", "lives_in", "u1", FEB, "NYC"),
                Probe("user", "lives_in", "u1", JUL, "LA"),
                Probe("user", "lives_in", "u1", DEC, "SF"),
            ],
        )
    )

    # 5. Back-dated "since Jan" backfill (arrives after a later fact).
    S.append(
        Scenario(
            name="backfilled_since_jan",
            description="Learn LA (Jun) first, later backfill 'NYC since Jan' -> bound NYC to Jun.",
            tests_correction=True,
            ops=[
                Op("assert", "user", "lives_in", "LA", t_valid=JUN, at=JUN),
                # backfilled record: valid since Jan, but learned in July (created later).
                Op("assert", "user", "lives_in", "NYC", t_valid=JAN, at=JUL),
            ],
            probes=[
                Probe("user", "lives_in", "u1", FEB, "NYC", note="backfilled past"),
                Probe("user", "lives_in", "u1", JUL, "LA", note="later fact wins its window"),
            ],
        )
    )

    # 6. Retract an error (transaction-time correction).
    S.append(
        Scenario(
            name="retract_error",
            description="Assert 'likes tea' then retract as mis-recorded; audit trail kept.",
            tests_correction=True,
            tests_bitemporal=True,
            ops=[
                Op("assert", "user", "likes", "tea", t_valid=JAN, at=JAN, edge_id="e_tea"),
                Op("retract", edge_id="e_tea", at=MAR),
            ],
            probes=[
                Probe("user", "likes", "u1", FEB, None, note="current belief: retracted"),
                Probe("user", "likes", "u1", FEB, "tea", tt=FEB, note="5b: believed tea before retract"),
            ],
        )
    )

    # 7. Predicate isolation.
    S.append(
        Scenario(
            name="predicate_isolation",
            description="lives_in vs works_at do not interfere on supersede.",
            ops=[
                Op("assert", "user", "lives_in", "NYC", t_valid=JAN, at=JAN),
                Op("assert", "user", "works_at", "Acme", t_valid=JAN, at=JAN),
                Op("assert", "user", "lives_in", "LA", t_valid=JUN, at=JUN),
            ],
            probes=[
                Probe("user", "works_at", "u1", JUL, "Acme", note="unaffected by lives_in change"),
                Probe("user", "lives_in", "u1", JUL, "LA"),
            ],
        )
    )

    # 8. Multi-tenant isolation.
    S.append(
        Scenario(
            name="tenant_isolation",
            description="Same subject/predicate, different users; supersede stays within tenant.",
            ops=[
                Op("assert", "user", "lives_in", "NYC", user="u1", t_valid=JAN, at=JAN),
                Op("assert", "user", "lives_in", "Paris", user="u2", t_valid=JAN, at=JAN),
                Op("assert", "user", "lives_in", "LA", user="u1", t_valid=JUN, at=JUN),
            ],
            probes=[
                Probe("user", "lives_in", "u2", JUL, "Paris", note="u2 unaffected"),
                Probe("user", "lives_in", "u1", JUL, "LA"),
            ],
        )
    )

    # 9. Belief evolution (pure transaction-time / 5b).
    S.append(
        Scenario(
            name="belief_evolution",
            description="As of March we still believed NYC held in July (LA not yet learned).",
            tests_bitemporal=True,
            ops=[
                Op("assert", "user", "lives_in", "NYC", t_valid=JAN, at=JAN),
                Op("assert", "user", "lives_in", "LA", t_valid=JUN, at=JUL),  # learned in July
            ],
            probes=[
                Probe("user", "lives_in", "u1", JUL, "NYC", tt=MAR, note="as-of Mar: LA unknown"),
                Probe("user", "lives_in", "u1", JUL, "LA", tt=SEP, note="as-of Sep: LA known"),
                Probe("user", "lives_in", "u1", FEB, "NYC", tt=SEP, note="as-of Sep about Feb"),
            ],
        )
    )

    return S
