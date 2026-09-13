"""K7 -- the proposals table, and the review that stands between an agent and the graph (G5, D10).

This package exists to make one sentence true: **nothing enters the semantic graph unproposed
and unconfirmed.** An agent writes proposals. A person accepts them. Only accepted rows are
applied. Every other Lane K package that produces content -- enrichment, ontology mapping, KPI
adoption, use case suggestion -- writes here rather than to the graph, which is why this is
built once rather than four times.

**Why the table lives in Postgres and not the graph.** A proposal is an audit record: who
proposed, who decided, when, and what it said at the time. The graph is a live structure that
gets rewritten by the next harvest; it is the wrong home for a record whose value is that it
does not change. The graph holds the *outcome* of review, never the queue.

**Three defences, because one is not enough.**

1. *State.* A proposal is pending, accepted or rejected, and a decided proposal cannot be
   decided again. Re-accepting something a person rejected is refused, not silently applied.
2. *Identity.* A proposal's id is derived from its content, so an agent re-run proposing the
   same thing produces the same id and the insert does nothing. This matters more than it
   sounds: K8's proposers will be re-run, and without it a rejected proposal would be
   resurrected by the next run and a review queue would double every night.
3. *Shape.* A payload is validated into its typed contract object at insert, not at apply.
   A proposal that could never be applied must not sit in a queue looking reviewable -- a
   person accepting it would have their decision fail somewhere they never see.

**Two status enums, deliberately.** ``contracts.semantic.ProposalStatus`` has two values,
because the graph only ever holds accepted or proposed rows -- a rejected row is not in the
graph at all, so a third value there would describe a state that cannot exist. This module's
``ReviewState`` has three, because the queue is exactly where rejection lives. Collapsing them
into one enum would put ``REJECTED`` into the graph's vocabulary and invite a query that
filters for it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt_retail.contracts.semantic import (
    AuthoritativeSource,
    Concept,
    JoinEdge,
    ProposalStatus,
)
from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set

__all__ = [
    "CARDINALITIES",
    "GRAPH_KINDS",
    "PROPOSAL_SCHEMA",
    "ApplyOutcome",
    "PayloadError",
    "Proposal",
    "ProposalKind",
    "ProposalStore",
    "Refusal",
    "ReviewOutcome",
    "ReviewState",
]

#: The same schema K6's snapshots use, in the control-plane database.
PROPOSAL_SCHEMA = "semantic"


class ProposalKind(str, Enum):
    """What a proposal is about. A closed set, and closed on purpose.

    An agent that can invent a kind will, and a review screen would then have a row it does
    not know how to render or apply. Adding a kind is a code change with a payload validator
    and an apply path attached, which is the point -- it cannot be added by a model at runtime.
    """

    #: Everything on a concept that makes it answerable: synonyms, question patterns, the
    #: scoping filter, personal-data fields, compliance tags, candidate metrics (K8).
    ENRICHMENT = "enrichment"
    #: This customer's table is an instance of this ontology concept (K8).
    MAPPING = "mapping"
    #: These two tables can be joined, and here is the predicate (G19).
    JOIN = "join"
    #: This table is the one that answers for this concept (G20).
    AUTHORITATIVE_SOURCE = "authoritative_source"
    #: What one side of a colliding column name means, and which concept it belongs to (G19).
    COLUMN_MEANING = "column_meaning"
    #: Adopt a metric as a KPI definition, carried in full (K11).
    KPI = "kpi"
    #: A question this deployment could now answer.
    USE_CASE = "use_case"


#: The kinds whose acceptance writes to the semantic graph. The other two are accepted into
#: their own consumers -- a KPI lands in the registry, a use case on a screen -- so ``apply``
#: leaves them alone rather than pretending to have applied them. A kind that has no graph
#: target is not a lesser proposal; it is a proposal about somewhere else.
GRAPH_KINDS = frozenset(
    {
        ProposalKind.ENRICHMENT,
        ProposalKind.MAPPING,
        ProposalKind.JOIN,
        ProposalKind.AUTHORITATIVE_SOURCE,
        ProposalKind.COLUMN_MEANING,
    }
)


#: The ways two tables can relate. Closed, like every other value an agent may emit: a
#: proposer that writes ``"1:n"`` or ``"many"`` has invented a term, and a planner reading
#: the edge later would have to guess what it meant.
CARDINALITIES = frozenset({"one-to-one", "one-to-many", "many-to-one", "many-to-many"})


class ReviewState(str, Enum):
    """Where a proposal is in review. One way out of ``PENDING``, and no way back."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PayloadError(ValueError):
    """A proposal's payload is not the shape its kind requires.

    Raised at ``propose`` rather than at apply. A malformed proposal that reached the queue
    would be reviewed by a person whose decision then failed silently in a background job.
    """


@dataclass(frozen=True)
class Proposal:
    """One thing an agent proposes, and the record of what happened to it."""

    kind: ProposalKind
    #: What the proposal is about -- a concept name, a qualified table, a metric identifier.
    #: Carried as a column rather than dug out of the payload so a review screen can group and
    #: filter without parsing jsonb.
    target: str
    payload: Mapping[str, Any]
    #: Which agent or person produced it. Never blank: a proposal without provenance cannot be
    #: judged, because half of judging it is knowing what produced it.
    proposer: str
    state: ReviewState = ReviewState.PENDING
    reviewer: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_note: str = ""
    applied_at: Optional[datetime] = None
    #: Why the last apply of this accepted proposal failed, if it did. Cleared on success.
    #: Visible so a reviewer can see an acceptance that has not landed, rather than trusting
    #: that "accepted" means "in the graph".
    apply_error: Optional[str] = None
    created_at: Optional[datetime] = None
    identifier: str = ""

    def __post_init__(self) -> None:
        if not self.proposer.strip():
            raise PayloadError(
                "a proposal must say what produced it; proposer was blank"
            )
        if not self.target.strip():
            raise PayloadError(
                "a proposal must name what it is about; target was blank"
            )
        object.__setattr__(self, "identifier", self.identifier or content_id(self))

    @property
    def is_decided(self) -> bool:
        return self.state is not ReviewState.PENDING

    def __str__(self) -> str:
        return f"{self.kind.value}/{self.target} [{self.state.value}]"


@dataclass(frozen=True)
class Refusal:
    """A decision that was not applied, and why.

    Carries the existing decision, not just the fact of one. "Already rejected" is a shrug;
    "rejected by dana on 8 Sep" is something a reviewer can act on.
    """

    identifier: str
    reason: str
    current_state: Optional[ReviewState] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None

    def __str__(self) -> str:
        who = f" by {self.decided_by}" if self.decided_by else ""
        return f"{self.identifier}: {self.reason}{who}"


@dataclass(frozen=True)
class ReviewOutcome:
    """What a bulk decision actually did.

    Returned rather than raised, because a bulk accept of twelve must not be destroyed by one
    id a colleague decided a moment earlier. The eleven good decisions commit; the refusal is
    reported. Raising would force the caller to choose between losing the batch and catching
    an exception to find out which row caused it.
    """

    decided: Tuple[str, ...] = ()
    refused: Tuple[Refusal, ...] = field(default_factory=tuple)

    @property
    def all_applied(self) -> bool:
        return not self.refused

    def __str__(self) -> str:
        return f"{len(self.decided)} decided, {len(self.refused)} refused"


@dataclass(frozen=True)
class ApplyOutcome:
    """What one apply run did to the graph.

    A proposal whose write fails is reported here and left accepted-and-unapplied with its
    error recorded, and the run continues. The first version raised on the first failure,
    which meant one accepted mapping onto a concept nobody had defined yet would block every
    proposal behind it in the queue -- forever, since the same one fails first every run.
    """

    applied: Tuple[str, ...] = ()
    failed: Tuple[Refusal, ...] = ()

    def __str__(self) -> str:
        return f"{len(self.applied)} applied, {len(self.failed)} failed"


# --- payload validation -----------------------------------------------------------------
#
# One validator per kind, each building the typed contract object the apply path will use.
# Building it here means a payload is proved applicable before anyone reviews it, and means
# apply has nothing left to decide.


def _concept_from(payload: Mapping[str, Any]) -> Concept:
    return Concept(
        name=_required(payload, "name"),
        description=payload.get("description", ""),
        synonyms=tuple(payload.get("synonyms", ())),
        question_patterns=tuple(payload.get("question_patterns", ())),
        sql_filter=payload.get("sql_filter", ""),
        personal_data_fields=tuple(payload.get("personal_data_fields", ())),
        compliance_tags=tuple(payload.get("compliance_tags", ())),
        metrics=tuple(payload.get("metrics", ())),
        business_area=payload.get("business_area"),
        status=ProposalStatus.ACCEPTED,
    )


def _join_from(payload: Mapping[str, Any]) -> JoinEdge:
    cardinality = payload.get("cardinality", "many-to-one")
    if cardinality not in CARDINALITIES:
        raise PayloadError(
            f"cardinality {cardinality!r} is not one of {sorted(CARDINALITIES)}"
        )
    return JoinEdge(
        left_table=_required(payload, "left_table"),
        right_table=_required(payload, "right_table"),
        on=_required(payload, "on"),
        cardinality=cardinality,
    )


def _authoritative_from(payload: Mapping[str, Any]) -> AuthoritativeSource:
    return AuthoritativeSource(
        concept=_required(payload, "concept"),
        table=_required(payload, "table"),
        alternatives=tuple(payload.get("alternatives", ())),
        rationale=payload.get("rationale", ""),
        status=ProposalStatus.ACCEPTED,
    )


def _mapping_from(payload: Mapping[str, Any]) -> Tuple[str, str]:
    return _required(payload, "table"), _required(payload, "concept")


def _column_meaning_from(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "table": _required(payload, "table"),
        "column": _required(payload, "column"),
        "concept": _required(payload, "concept"),
        "means": _required(payload, "means"),
        "joinable": bool(payload.get("joinable", False)),
    }


def _kpi_from(payload: Mapping[str, Any]) -> Any:
    """A KPI proposal carries the whole definition, and it is built here so it fails here.

    The first version required only an identifier, which made a KPI proposal a name a person
    could accept and nothing could adopt. ``KpiDefinition``'s own construction checks the
    formula against its declared parameters and tables, so a proposal that clears this
    validator is one the registry can take without a second opinion.
    """
    from egpt_retail.contracts.kpi import (
        Dialect,
        KpiDefinition,
        KpiParameter,
        ParameterKind,
        ResultShape,
        TimeWindow,
        TimeWindowUnit,
    )

    try:
        return KpiDefinition(
            identifier=_required(payload, "identifier"),
            name=_required(payload, "name"),
            description=payload.get("description", ""),
            sql=_required(payload, "sql"),
            grain=_required(payload, "grain"),
            business_area=_required(payload, "business_area"),
            source_tables=frozenset(payload.get("source_tables", ())),
            result_shape=ResultShape(payload.get("result_shape", "scalar")),
            dialect=Dialect(payload.get("dialect", "postgres")),
            parameters=tuple(
                KpiParameter(
                    name=p["name"],
                    kind=ParameterKind(p.get("kind", "scalar")),
                    description=p.get("description", ""),
                )
                for p in payload.get("parameters", ())
            ),
            # Carried, not dropped. A declared lookback is the difference between a metric
            # that can be recomputed for a past date and one that silently means "since the
            # beginning"; the first version accepted `time_window` in a payload and built a
            # definition without it, so a 30-day metric registered as unbounded and nothing
            # anywhere said so.
            time_window=(
                TimeWindow(
                    length=int(window["length"]), unit=TimeWindowUnit(window["unit"])
                )
                if (window := payload.get("time_window"))
                else None
            ),
            concept=payload.get("concept"),
        )
    except (ValueError, TypeError, KeyError) as error:
        # A malformed payload is a PayloadError whatever shape the malformation takes. A raw
        # KeyError from a parameter missing "name", or a TypeError from a string where an int
        # belongs, escaped as itself -- so a caller catching PayloadError to report "this
        # proposal cannot be applied" saw an unhandled exception instead.
        raise PayloadError(f"{type(error).__name__}: {error}") from None


def _use_case_from(payload: Mapping[str, Any]) -> str:
    return _required(payload, "question")


def _required(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PayloadError(
            f"payload is missing a non-empty {key!r}; got {payload.get(key)!r}"
        )
    return value


_VALIDATORS: Dict[ProposalKind, Callable[[Mapping[str, Any]], Any]] = {
    ProposalKind.ENRICHMENT: _concept_from,
    ProposalKind.MAPPING: _mapping_from,
    ProposalKind.JOIN: _join_from,
    ProposalKind.AUTHORITATIVE_SOURCE: _authoritative_from,
    ProposalKind.COLUMN_MEANING: _column_meaning_from,
    ProposalKind.KPI: _kpi_from,
    ProposalKind.USE_CASE: _use_case_from,
}


def validate_payload(
    kind: ProposalKind,
    payload: Mapping[str, Any],
    *,
    vocabularies: Optional[VocabularySet] = None,
) -> Any:
    """Build the typed object a kind's payload describes, or raise ``PayloadError``.

    With ``vocabularies``, every field that is a term from a closed set is checked against
    it. An enrichment carrying ``business_area: "marketng"`` is the case: it parses, it
    builds a ``Concept``, and once in the graph it is an area no screen lists and no KPI
    matches. The set difference that feasibility rests on only works if both sides use the
    same terms, and the proposal gate is where that is enforced.
    """
    validator = _VALIDATORS.get(kind)
    if validator is None:  # pragma: no cover -- unreachable while the enum is closed
        raise PayloadError(f"no payload validator registered for kind {kind!r}")
    built = validator(payload)
    if (
        vocabularies is not None
        and kind is ProposalKind.ENRICHMENT
        and built.business_area
    ):
        try:
            vocabularies.validate("business_area", built.business_area)
        except ValueError as error:
            raise PayloadError(str(error)) from None
    return built


def content_id(proposal: Proposal) -> str:
    """A proposal's identity, derived from what it says rather than when it was said.

    Two runs of the same proposer over the same harvest produce the same ids, so the second
    run inserts nothing. Without this a nightly enrichment pass would re-raise every proposal
    a person already rejected, and the rejection would mean nothing.

    The proposer is **not** in the digest. Two agents independently proposing the same synonym
    for the same concept are proposing one thing, and a reviewer should see it once.
    """
    canonical = json.dumps(proposal.payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        f"{proposal.kind.value}|{proposal.target}|{canonical}".encode()
    )
    return digest.hexdigest()[:32]


_DDL = (
    f"CREATE SCHEMA IF NOT EXISTS {PROPOSAL_SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {PROPOSAL_SCHEMA}.proposal (
        id            text PRIMARY KEY,
        kind          text        NOT NULL,
        target        text        NOT NULL,
        payload       jsonb       NOT NULL,
        proposer      text        NOT NULL,
        state         text        NOT NULL DEFAULT 'pending',
        reviewer      text,
        decided_at    timestamptz,
        decision_note text        NOT NULL DEFAULT '',
        applied_at    timestamptz,
        apply_error   text,
        created_at    timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT proposal_state_valid
            CHECK (state IN ('pending', 'accepted', 'rejected')),
        CONSTRAINT proposal_decision_attributed
            CHECK ((state = 'pending') = (reviewer IS NULL))
    )""",
    # For a table created before apply_error existed. Idempotent, like the rest.
    f"ALTER TABLE {PROPOSAL_SCHEMA}.proposal ADD COLUMN IF NOT EXISTS apply_error text",
    f"""CREATE INDEX IF NOT EXISTS proposal_state_kind
        ON {PROPOSAL_SCHEMA}.proposal (state, kind)""",
    f"""COMMENT ON TABLE {PROPOSAL_SCHEMA}.proposal IS
        'Everything an agent proposes and what a person decided (K7/G5/D10). The id is a '
        'digest of kind, target and payload, so re-running a proposer cannot resurrect a '
        'rejected row. proposal_decision_attributed makes an unattributed decision '
        'unrepresentable rather than merely discouraged.'""",
)


class ProposalStore:
    """The proposals table. The connection is injected; the caller owns its lifetime."""

    def __init__(
        self,
        connection: Any,
        schema: str = PROPOSAL_SCHEMA,
        *,
        vocabularies: Optional[VocabularySet] = None,
    ) -> None:
        self._connection = connection
        self._schema = schema
        # The shipped closed sets unless a deployment passes its overlay. Never None: a store
        # built without vocabularies would accept any business area, and nothing would say so.
        self._vocabularies = vocabularies or load_vocabulary_set()

    # --- schema ------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table if absent. Idempotent, and safe to call from several processes.

        **Deliberately self-provisioning**, not a ``MigrationManager`` migration. That
        manager tracks the framework's own tables through a fixed ``BaseDb`` attribute map;
        a product-owned table has no place in it, and teaching it one is a framework change
        that would ship in the wheel for a retail need. Like the pgvector tables, this one
        is created by the code that owns it. The cost is that schema evolution here is by
        ``ADD COLUMN IF NOT EXISTS`` rather than a versioned migration, which is acceptable
        while there are two tables and one owner and should be revisited if either grows.

        ``CREATE TABLE IF NOT EXISTS`` is not race-safe: two callers can both see no table and
        both try to create it, and one fails. A transaction-scoped advisory lock serialises
        them; it is released at commit.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("egpt_retail.semantic.ddl",),
            )
            for statement in _DDL:
                cursor.execute(statement)
        self._connection.commit()

    async def aensure_schema(self) -> None:
        """Async variant."""
        await asyncio.to_thread(self.ensure_schema)

    # --- writing proposals -------------------------------------------------------------

    def propose(self, proposal: Proposal) -> bool:
        """Record one proposal. Returns whether it was new.

        A proposal whose content matches one already stored is **not** re-raised, whatever
        state that one is in. Re-proposing a rejected thing is the failure mode this guards:
        the agent has no memory of the rejection and would raise it again every night.
        """
        return self.propose_many((proposal,)) == 1

    async def apropose(self, proposal: Proposal) -> bool:
        """Async variant."""
        return await asyncio.to_thread(self.propose, proposal)

    def propose_many(self, proposals: Sequence[Proposal]) -> int:
        """Record several proposals in one transaction. Returns how many were new."""
        for proposal in proposals:
            validate_payload(
                proposal.kind, proposal.payload, vocabularies=self._vocabularies
            )
        inserted = 0
        with self._connection.cursor() as cursor:
            for proposal in proposals:
                cursor.execute(
                    f"""INSERT INTO {self._schema}.proposal
                        (id, kind, target, payload, proposer, state)
                        VALUES (%s, %s, %s, %s, %s, 'pending')
                        ON CONFLICT (id) DO NOTHING""",
                    (
                        proposal.identifier,
                        proposal.kind.value,
                        proposal.target,
                        json.dumps(dict(proposal.payload)),
                        proposal.proposer,
                    ),
                )
                inserted += cursor.rowcount or 0
        self._connection.commit()
        return inserted

    async def apropose_many(self, proposals: Sequence[Proposal]) -> int:
        """Async variant."""
        return await asyncio.to_thread(self.propose_many, proposals)

    # --- reading ------------------------------------------------------------------------

    def get(self, identifier: str) -> Optional[Proposal]:
        """One proposal by id."""
        rows = self._select("WHERE id = %s", (identifier,))
        return rows[0] if rows else None

    async def aget(self, identifier: str) -> Optional[Proposal]:
        """Async variant."""
        return await asyncio.to_thread(self.get, identifier)

    def get_many(self, identifiers: Sequence[str]) -> Tuple[Proposal, ...]:
        """Several proposals by id, in one round trip rather than one per id.

        Mirrors ``_decide``'s own ``WHERE id = ANY(%s)``. Order is not guaranteed to match
        ``identifiers`` -- ``_select`` always orders by ``created_at, id`` -- so a caller that
        cares about input order re-keys the result itself.
        """
        if not identifiers:
            return ()
        return tuple(self._select("WHERE id = ANY(%s)", (list(identifiers),)))

    async def aget_many(self, identifiers: Sequence[str]) -> Tuple[Proposal, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.get_many, identifiers)

    def list(
        self,
        *,
        state: Optional[ReviewState] = None,
        kind: Optional[ProposalKind] = None,
    ) -> Tuple[Proposal, ...]:
        """Proposals, optionally narrowed to a state or a kind, oldest first.

        Oldest first because this is a queue. A review screen that showed newest first would
        let the oldest proposal -- the one most likely to be about a schema that has since
        moved on -- sink quietly out of view.
        """
        clauses: List[str] = []
        params: List[Any] = []
        if state is not None:
            clauses.append("state = %s")
            params.append(state.value)
        if kind is not None:
            clauses.append("kind = %s")
            params.append(kind.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return tuple(self._select(where, tuple(params)))

    async def alist(
        self,
        *,
        state: Optional[ReviewState] = None,
        kind: Optional[ProposalKind] = None,
    ) -> Tuple[Proposal, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.list, state=state, kind=kind)

    def pending(self) -> Tuple[Proposal, ...]:
        """Everything still awaiting a decision."""
        return self.list(state=ReviewState.PENDING)

    async def apending(self) -> Tuple[Proposal, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.pending)

    # --- deciding -----------------------------------------------------------------------

    def accept(
        self, identifiers: Sequence[str], reviewer: str, note: str = ""
    ) -> ReviewOutcome:
        """Accept proposals in bulk. Only pending ones move."""
        return self._decide(identifiers, ReviewState.ACCEPTED, reviewer, note)

    async def aaccept(
        self, identifiers: Sequence[str], reviewer: str, note: str = ""
    ) -> ReviewOutcome:
        """Async variant."""
        return await asyncio.to_thread(self.accept, identifiers, reviewer, note)

    def reject(
        self, identifiers: Sequence[str], reviewer: str, note: str = ""
    ) -> ReviewOutcome:
        """Reject proposals in bulk. Only pending ones move."""
        return self._decide(identifiers, ReviewState.REJECTED, reviewer, note)

    async def areject(
        self, identifiers: Sequence[str], reviewer: str, note: str = ""
    ) -> ReviewOutcome:
        """Async variant."""
        return await asyncio.to_thread(self.reject, identifiers, reviewer, note)

    def _decide(
        self,
        identifiers: Sequence[str],
        state: ReviewState,
        reviewer: str,
        note: str,
    ) -> ReviewOutcome:
        """The one place a proposal's state changes.

        ``WHERE state = 'pending'`` is the whole enforcement of the one-way rule: a decided
        proposal simply does not match, so it is not updated and lands in ``refused``. Doing
        it in the predicate rather than in a read-then-write means two reviewers pressing
        Accept and Reject at the same moment cannot both succeed.
        """
        if not reviewer.strip():
            raise PayloadError(
                "a decision must be attributed; reviewer was blank. An unattributed "
                "acceptance is indistinguishable from no review at all (D10)"
            )
        wanted = list(dict.fromkeys(identifiers))
        if not wanted:
            return ReviewOutcome()

        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.proposal
                    SET state = %s, reviewer = %s, decided_at = now(), decision_note = %s
                    WHERE id = ANY(%s) AND state = 'pending'
                    RETURNING id""",
                (state.value, reviewer, note, wanted),
            )
            decided = [row[0] for row in cursor.fetchall()]
            untouched = [i for i in wanted if i not in set(decided)]
            refusals: List[Refusal] = []
            if untouched:
                cursor.execute(
                    f"""SELECT id, state, reviewer, decided_at
                        FROM {self._schema}.proposal WHERE id = ANY(%s)""",
                    (untouched,),
                )
                known = {row[0]: row for row in cursor.fetchall()}
                for identifier in untouched:
                    row = known.get(identifier)
                    if row is None:
                        refusals.append(Refusal(identifier, "no such proposal"))
                    else:
                        refusals.append(
                            Refusal(
                                identifier=identifier,
                                reason=f"already {row[1]}",
                                current_state=ReviewState(row[1]),
                                decided_by=row[2],
                                decided_at=row[3],
                            )
                        )
        self._connection.commit()
        return ReviewOutcome(decided=tuple(decided), refused=tuple(refusals))

    # --- applying -----------------------------------------------------------------------

    def apply_accepted(self, store: Any) -> ApplyOutcome:
        """Write every accepted, unapplied, graph-bound proposal into the semantic graph.

        This is the only path from the proposals table to the graph, and it reads
        ``state = 'accepted'`` -- so a rejected or pending row cannot reach it however it is
        called.

        ``applied_at`` is set only after the graph write returns. A crash between the two
        leaves the proposal accepted-and-unapplied, and the next run applies it again; the
        graph writes are all ``MERGE``, so applying twice is the same as applying once. The
        opposite ordering would lose the write silently, which is the failure you cannot see.

        A write that fails is recorded on the proposal and does not stop the run. It stays
        accepted, so the next run retries it -- a mapping onto a concept that enrichment has
        not yet landed will succeed once it has.

        One commit per proposal, deliberately. A crash mid-run then leaves every proposal
        before it recorded as applied and every one after it untouched, with nothing in
        between; batching would trade that for one round trip fewer per row, which does not
        matter at review-queue sizes.
        """
        applied: List[str] = []
        failed: List[Refusal] = []
        for proposal in self.list(state=ReviewState.ACCEPTED):
            if proposal.applied_at is not None or proposal.kind not in GRAPH_KINDS:
                continue
            try:
                self._apply_one(store, proposal)
            except Exception as error:  # noqa: BLE001 -- recorded, not swallowed
                message = f"{type(error).__name__}: {error}"
                with self._connection.cursor() as cursor:
                    cursor.execute(
                        f"UPDATE {self._schema}.proposal SET apply_error = %s WHERE id = %s",
                        (message, proposal.identifier),
                    )
                self._connection.commit()
                failed.append(Refusal(proposal.identifier, message))
                continue
            with self._connection.cursor() as cursor:
                cursor.execute(
                    f"""UPDATE {self._schema}.proposal
                        SET applied_at = now(), apply_error = NULL WHERE id = %s""",
                    (proposal.identifier,),
                )
            self._connection.commit()
            applied.append(proposal.identifier)
        return ApplyOutcome(applied=tuple(applied), failed=tuple(failed))

    def mark_applied(self, identifier: str) -> None:
        """Stamp a proposal as applied by a consumer other than the graph (K11's registry).

        The same rule as ``apply_accepted``: set only after the consumer's write returned.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.proposal
                    SET applied_at = now(), apply_error = NULL
                    WHERE id = %s AND state = 'accepted'""",
                (identifier,),
            )
        self._connection.commit()

    async def amark_applied(self, identifier: str) -> None:
        """Async variant."""
        await asyncio.to_thread(self.mark_applied, identifier)

    def record_apply_error(self, identifier: str, message: str) -> None:
        """Stamp a failed application attempt, without touching ``state`` or ``applied_at``.

        Mirrors :meth:`apply_accepted`'s own failure path so a consumer other than the graph
        (K11's KPI registry) can surface a failed adoption the same way: the proposal stays
        ``accepted`` and unapplied, and the next sweep of its kind retries it.

        Rolls back first: this is the recovery path a caller reaches for right after some
        other statement on this same connection raised, and Postgres refuses every further
        statement on a connection until its aborted transaction is rolled back -- without
        this, recording the very failure that got us here would itself fail. A no-op when
        the connection has no open transaction to roll back.
        """
        self._connection.rollback()
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {self._schema}.proposal SET apply_error = %s WHERE id = %s",
                (message, identifier),
            )
        self._connection.commit()

    async def arecord_apply_error(self, identifier: str, message: str) -> None:
        """Async variant."""
        await asyncio.to_thread(self.record_apply_error, identifier, message)

    async def aapply_accepted(self, store: Any) -> ApplyOutcome:
        """Async variant."""
        return await asyncio.to_thread(self.apply_accepted, store)

    @staticmethod
    def _apply_one(store: Any, proposal: Proposal) -> None:
        """Dispatch one accepted proposal to the graph write its kind implies."""
        built = validate_payload(proposal.kind, proposal.payload)
        # Every write carries the proposal that produced it, so the audit trail reaches the
        # artifact and not only the row: a node or edge can say which accepted proposal put
        # its current values there, and a later revision overwrites that stamp with its own.
        stamp = proposal.identifier
        if proposal.kind is ProposalKind.ENRICHMENT:
            store.write_concept(built, proposal_id=stamp)
        elif proposal.kind is ProposalKind.MAPPING:
            table, concept = built
            store.link_table_to_concept(table, concept, proposal_id=stamp)
        elif proposal.kind is ProposalKind.JOIN:
            store.write_join(built, proposal_id=stamp)
        elif proposal.kind is ProposalKind.AUTHORITATIVE_SOURCE:
            store.write_authoritative_source(built, proposal_id=stamp)
        elif proposal.kind is ProposalKind.COLUMN_MEANING:
            store.write_column_meaning(
                built["table"],
                built["column"],
                concept=built["concept"],
                meaning=built["means"],
                joinable=built["joinable"],
                proposal_id=stamp,
            )

    # --- internals ----------------------------------------------------------------------

    def _select(self, where: str, params: Tuple[Any, ...]) -> List[Proposal]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT id, kind, target, payload, proposer, state, reviewer,
                           decided_at, decision_note, applied_at, apply_error, created_at
                    FROM {self._schema}.proposal {where} ORDER BY created_at, id""",
                params,
            )
            rows = cursor.fetchall()
        return [
            Proposal(
                identifier=row[0],
                kind=ProposalKind(row[1]),
                target=row[2],
                payload=row[3],
                proposer=row[4],
                state=ReviewState(row[5]),
                reviewer=row[6],
                decided_at=row[7],
                decision_note=row[8],
                applied_at=row[9],
                apply_error=row[10],
                created_at=row[11],
            )
            for row in rows
        ]


def utcnow() -> datetime:
    """The clock, in one place, so a test can see what a decision time is compared against."""
    return datetime.now(timezone.utc)
