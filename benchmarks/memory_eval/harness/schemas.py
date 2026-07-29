"""Unified data model for the memory eval harness.

Every benchmark (LongMemEval, LOCOMO, DMR, and our custom temporal/decay set) is
normalized into the *same* shape so one runner, one provider seam and one metric
suite serve all of them. The shape is deliberately close to the sibling
``benchmarks/memory_consolidation`` harness's ``Episode`` so the two can be
unified by SM-X7 later (see findings/01-data-quality-and-licensing.md).

Design decisions surfaced here (they drive the metrics in Phase 2):

- A ``Turn`` carries an explicit ``turn_id`` and ``timestamp``. The id is the
  unit of *retrieval ground truth* (recall@k / nDCG@k check whether a probe's
  ``evidence_ids`` were retrieved). The timestamp drives bi-temporal "true at T"
  and decay.
- A ``Probe`` (one QA item) carries a canonical ``category`` so temporal / update
  / abstention accuracy can be reported per-slice, plus optional ``as_of`` (the
  point in time the question is asked — for "true at T") and a ``decay`` marker.
- ``evidence_ids`` are gold supporting ``turn_id``s. Public sets that lack them
  (DMR is thin here) degrade gracefully: recall/nDCG are simply not reported for
  probes with no evidence, rather than being faked.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Canonical question categories.
#
# Each source benchmark uses its own category vocabulary; we normalize to this
# small canonical set so "temporal-reasoning accuracy" means the same thing
# across LongMemEval, LOCOMO and our custom set. ``raw_category`` keeps the
# original label for provenance/debugging.
# --------------------------------------------------------------------------- #
CAT_SINGLE_SESSION = "single_session"  # answer lives in one session
CAT_MULTI_SESSION = "multi_session"  # requires joining facts across sessions
CAT_TEMPORAL = "temporal"  # reasoning about when / ordering / durations
CAT_KNOWLEDGE_UPDATE = "knowledge_update"  # a fact changed; latest value wins
CAT_PREFERENCE = "preference"  # a stated user preference
CAT_ABSTENTION = "abstention"  # correct answer is "I don't know / not stated"
CAT_OPEN_DOMAIN = "open_domain"  # world knowledge, not memory-bound
CAT_ADVERSARIAL = "adversarial"  # answer is unanswerable / a distractor
CAT_AS_OF_T = "as_of_t"  # bi-temporal: value true *as of* a given time
CAT_DECAY = "decay"  # stale/low-salience memory should be de-ranked/forgotten

CANONICAL_CATEGORIES = frozenset(
    {
        CAT_SINGLE_SESSION,
        CAT_MULTI_SESSION,
        CAT_TEMPORAL,
        CAT_KNOWLEDGE_UPDATE,
        CAT_PREFERENCE,
        CAT_ABSTENTION,
        CAT_OPEN_DOMAIN,
        CAT_ADVERSARIAL,
        CAT_AS_OF_T,
        CAT_DECAY,
    }
)

# Categories that count toward the headline temporal-reasoning accuracy slice.
TEMPORAL_CATEGORIES = frozenset({CAT_TEMPORAL, CAT_KNOWLEDGE_UPDATE, CAT_AS_OF_T})


@dataclass(frozen=True)
class Turn:
    """One message in a conversation. The ingest unit written to a MemoryProvider.

    ``turn_id`` must be unique within a Benchmark: it is the retrieval-relevance
    key that ``evidence_ids`` point at.
    """

    turn_id: str
    speaker: str
    text: str
    timestamp: int  # epoch seconds; when the turn was uttered (drives ordering / decay)
    # Bi-temporal VALID-TIME window (SM-R10 canonical: "true at T" = t_valid <= T <
    # t_invalid, BOTH bounds required — SPEC §7 pitfall callout). For a fact stated
    # by this turn: t_valid = when it became true (defaults to ``timestamp`` when
    # None), t_invalid = when it was superseded/invalidated (None => open interval,
    # COALESCE'd to +inf). These let the bi-temporal metric catch a stale-superseded
    # fact returned as "current", which a one-sided (future-only) check cannot.
    t_valid: Optional[int] = None
    t_invalid: Optional[int] = None

    def as_memory_text(self) -> str:
        """The text a provider ingests: speaker-attributed so 'who said it' survives."""
        return f"{self.speaker}: {self.text}"

    def valid_at(self, t: int) -> bool:
        """SM-R10 point-in-time truth: ``t_valid <= t < t_invalid`` (open => +inf)."""
        lo = self.t_valid if self.t_valid is not None else self.timestamp
        hi = self.t_invalid  # None => open (+inf)
        return lo <= t and (hi is None or t < hi)


@dataclass(frozen=True)
class Session:
    """An ordered batch of turns. Sessions are fed to the provider in time order."""

    session_id: str
    turns: List[Turn] = field(default_factory=list)

    @property
    def start_ts(self) -> int:
        return min((t.timestamp for t in self.turns), default=0)


@dataclass(frozen=True)
class Probe:
    """One QA probe: a question plus its gold answer and retrieval ground truth."""

    probe_id: str
    question: str
    answer: str
    category: str = CAT_SINGLE_SESSION
    # Gold supporting turn ids (for recall@k / nDCG@k). Empty => retrieval metrics
    # are skipped for this probe (not faked).
    evidence_ids: List[str] = field(default_factory=list)
    # Bi-temporal: the instant the question is asked. The provider must answer
    # with the value that was true *as of* this time, ignoring later updates.
    as_of: Optional[int] = None
    # Decay probe: the answer is a fact that SHOULD have decayed/been forgotten;
    # a well-behaved memory de-ranks it. See metrics.decay_* in Phase 2.
    decay_target_turn_id: Optional[str] = None
    raw_category: Optional[str] = None  # original benchmark label, for provenance

    def __post_init__(self) -> None:
        if self.category not in CANONICAL_CATEGORIES:
            raise ValueError(
                f"probe {self.probe_id!r}: category {self.category!r} is not canonical; "
                f"map it in the loader. Allowed: {sorted(CANONICAL_CATEGORIES)}"
            )


@dataclass(frozen=True)
class Benchmark:
    """A whole benchmark instance: the conversation corpus plus its probes."""

    name: str
    sessions: List[Session] = field(default_factory=list)
    probes: List[Probe] = field(default_factory=list)
    # Provenance + honesty: where the data came from, its license, and any
    # data-quality caveat (e.g. single-authored). Surfaced in reports.
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def n_turns(self) -> int:
        return sum(len(s.turns) for s in self.sessions)

    @property
    def turn_index(self) -> Dict[str, Turn]:
        """turn_id -> Turn, for evidence lookup during scoring."""
        return {t.turn_id: t for s in self.sessions for t in s.turns}

    def validate(self) -> None:
        """Fail loudly on a malformed benchmark (duplicate ids, dangling evidence)."""
        ids: set = set()
        for s in self.sessions:
            for t in s.turns:
                if t.turn_id in ids:
                    raise ValueError(f"{self.name}: duplicate turn_id {t.turn_id!r}")
                ids.add(t.turn_id)
        for p in self.probes:
            for ev in p.evidence_ids:
                if ev not in ids:
                    raise ValueError(
                        f"{self.name}: probe {p.probe_id!r} cites evidence {ev!r} that is not a real turn_id"
                    )
            if p.decay_target_turn_id and p.decay_target_turn_id not in ids:
                raise ValueError(
                    f"{self.name}: probe {p.probe_id!r} decay target {p.decay_target_turn_id!r} is not a real turn_id"
                )


# --------------------------------------------------------------------------- #
# Unified JSON (de)serialization.
#
# The committed fixtures under fixtures/ are stored in this unified schema so a
# single ``load_unified`` reads any of them. Each benchmark's *raw-format* parser
# (locomo.py, longmemeval.py, ...) maps the real corpus into these same objects.
# --------------------------------------------------------------------------- #
def to_json(benchmark: Benchmark) -> str:
    return json.dumps(asdict(benchmark), indent=2, sort_keys=False)


def load_unified(path: Path) -> Benchmark:
    """Load a Benchmark from a unified-schema JSON file (used by the fixtures)."""
    raw = json.loads(Path(path).read_text())
    sessions = [
        Session(
            session_id=s["session_id"],
            turns=[Turn(**t) for t in s.get("turns", [])],
        )
        for s in raw.get("sessions", [])
    ]
    probes = [Probe(**p) for p in raw.get("probes", [])]
    bench = Benchmark(
        name=raw["name"],
        sessions=sessions,
        probes=probes,
        meta=raw.get("meta", {}),
    )
    bench.validate()
    return bench
