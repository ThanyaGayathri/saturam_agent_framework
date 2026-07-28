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

    # 10. Same-`t_valid` tie-break (concurrent / simultaneous conflicting asserts).
    #     Two contradictory facts claim the SAME valid-from. SPEC SS6 R1-TIE resolves this on the
    #     transaction axis: last writer wins. The loser keeps a zero-length valid interval
    #     [JUN, JUN) -- retained for audit (5b) but never current-valid, since `valid_at` is
    #     half-open (`t_valid <= t < t_invalid`).
    S.append(
        Scenario(
            name="same_t_valid_tiebreak",
            description="NYC and LA both asserted valid-from Jun; later write (Jul) wins the window.",
            tests_correction=True,
            tests_bitemporal=True,
            ops=[
                Op("assert", "user", "lives_in", "NYC", t_valid=JUN, at=JUN),
                Op("assert", "user", "lives_in", "LA", t_valid=JUN, at=JUL),  # same t_valid, later write
            ],
            probes=[
                Probe("user", "lives_in", "u1", JUN, "LA", note="tie-break: last writer wins at t_valid"),
                Probe("user", "lives_in", "u1", DEC, "LA", note="and onward"),
                Probe("user", "lives_in", "u1", JUL, "NYC", tt=JUN, note="5b: loser auditable pre-tie-break"),
                Probe("user", "lives_in", "u1", JUN, "LA", tt=SEP, note="5b: winner after tie-break"),
            ],
        )
    )

    # 11. Deep re-correction: correct a belief that was ITSELF already superseded.
    #     NYC is superseded by LA, then SF corrects the *middle* of the already-bounded NYC
    #     window -- a second-level SCD-2 version. World-time Mar therefore has three distinct
    #     beliefs depending on transaction time, which is the multi-level 5b case.
    S.append(
        Scenario(
            name="deep_recorrection",
            description="NYC->LA, then SF corrects the already-superseded NYC window; 3 beliefs about Mar.",
            tests_correction=True,
            tests_bitemporal=True,
            ops=[
                Op("assert", "user", "lives_in", "NYC", t_valid=JAN, at=JAN),
                Op("assert", "user", "lives_in", "LA", t_valid=JUN, at=JUN),  # bounds NYC to [Jan, Jun)
                Op("assert", "user", "lives_in", "SF", t_valid=MAR, at=JUL),  # re-corrects inside [Jan, Jun)
            ],
            probes=[
                Probe("user", "lives_in", "u1", FEB, "NYC", note="NYC re-bounded to [Jan, Mar)"),
                Probe("user", "lives_in", "u1", MAR, "SF", note="SF owns [Mar, Jun)"),
                Probe("user", "lives_in", "u1", JUL, "LA", note="LA still owns [Jun, inf)"),
                Probe("user", "lives_in", "u1", MAR, "NYC", tt=JUN, note="5b lvl1: as-of Jun, Mar was NYC"),
                Probe("user", "lives_in", "u1", MAR, "SF", tt=SEP, note="5b lvl2: as-of Sep, Mar is SF"),
            ],
        )
    )

    return S


# ---------------------------------------------------------------------- #
# 5c -- vector/episodic-tier filter scenarios (SPEC SS9).
# ---------------------------------------------------------------------- #


@dataclass
class VectorRowSpec:
    """A vector-tier row to seed, plus an optional derived `valid_to` propagated later."""

    memory_id: str
    memory: str
    created_at: int
    user: str = "u1"
    valid_from: Optional[int] = None  # None => pre-migration row ("unknown")
    valid_to: Optional[int] = None
    # Applied AFTER all rows are inserted, simulating the SS9.1 propagation from a graph
    # invalidation. None => no propagation for this row.
    propagate_valid_to: Optional[int] = None


@dataclass
class FilterProbe:
    """A 5c recall probe: which memory ids survive the valid-time filter at world-time `t`."""

    user: str
    t: int
    expect: List[str]  # expected memory ids, sorted
    note: str = ""


@dataclass
class VectorScenario:
    name: str
    rows: List[VectorRowSpec]
    probes: List[FilterProbe]
    description: str = ""


def build_vector_scenarios() -> List[VectorScenario]:
    """5c probes: the SS9 filter-only decision, measured rather than asserted."""
    V: List[VectorScenario] = []

    # 1. Open interval + lower bound.
    V.append(
        VectorScenario(
            name="vec_open_interval",
            description="A memory valid from Jan with no upper bound.",
            rows=[VectorRowSpec("m_nyc", "lives in NYC", created_at=JAN, valid_from=JAN)],
            probes=[
                FilterProbe("u1", DEC23, [], note="before valid_from"),
                FilterProbe("u1", FEB, ["m_nyc"]),
                FilterProbe("u1", DEC, ["m_nyc"], note="open valid_to -> still recalled"),
            ],
        )
    )

    # 2. Derived valid_to propagated from a graph invalidation (SS9.1). The vector tier never
    #    decides this itself -- it records what the graph decided.
    V.append(
        VectorScenario(
            name="vec_derived_valid_to",
            description="Graph invalidates NYC at Jun; the propagated valid_to bounds recall.",
            rows=[
                VectorRowSpec("m_nyc", "lives in NYC", created_at=JAN, valid_from=JAN, propagate_valid_to=JUN),
                VectorRowSpec("m_la", "lives in LA", created_at=JUN, valid_from=JUN),
            ],
            probes=[
                FilterProbe("u1", FEB, ["m_nyc"], note="past still recalled -- not deleted"),
                FilterProbe("u1", JUL, ["m_la"], note="superseded memory filtered out"),
            ],
        )
    )

    # 3. THE MIGRATION CASE. The SS9 migration is additive, so every row written before it has
    #    valid_from IS NULL. A bare `valid_from <= :T` drops all of them (NULL comparison).
    V.append(
        VectorScenario(
            name="vec_legacy_null_valid_from",
            description="Pre-migration rows (valid_from IS NULL) must stay recallable at every T.",
            rows=[
                VectorRowSpec("m_legacy", "pre-migration memory", created_at=DEC23, valid_from=None),
                VectorRowSpec("m_nyc", "lives in NYC", created_at=JAN, valid_from=JAN),
            ],
            probes=[
                FilterProbe("u1", DEC23, ["m_legacy"], note="unknown valid_from = assume always valid"),
                FilterProbe("u1", FEB, ["m_legacy", "m_nyc"]),
                FilterProbe("u1", DEC, ["m_legacy", "m_nyc"]),
            ],
        )
    )

    # 4. Tenant isolation on the filter path.
    V.append(
        VectorScenario(
            name="vec_tenant_isolation",
            description="The 5c filter is tenant-scoped like the graph predicates.",
            rows=[
                VectorRowSpec("m_u1", "u1 memory", created_at=JAN, user="u1", valid_from=JAN),
                VectorRowSpec("m_u2", "u2 memory", created_at=JAN, user="u2", valid_from=JAN),
            ],
            probes=[
                FilterProbe("u1", FEB, ["m_u1"]),
                FilterProbe("u2", FEB, ["m_u2"]),
            ],
        )
    )

    return V
