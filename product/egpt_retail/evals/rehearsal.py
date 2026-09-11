"""Rehearsal review (R15, D14): a person confirms a golden-set score before publish proceeds.

The delivery plan is explicit that automation alone does not gate activation: "Rehearsal review
by a person still gates activation." A golden pass rate is a number a deterministic executor can
compare to a threshold; whether that number is *safe to publish on* is a decision the build plan
reserves for a person, the same way K7 reserves accepting a proposal for a person rather than
letting an agent's own confidence decide.

Modelled on K7's ``ProposalStore`` (``egpt_retail/semantic/proposals.py``): a raw connection the
caller owns the lifetime of, a state machine enforced in the update predicate rather than by a
read-then-write, and an attributed decision made unrepresentable any other way. It differs from
K7 in one respect that matters: **a golden-set run that changes the score reopens the row to
pending.** K7's proposals are decided once and stay decided; a rehearsal review is a claim
about *this score*, and approving version 0.1.0's 95% must never be read as approving 0.2.0's
60% just because nobody re-ran review in between. A re-run that reproduces the *same* score
(pass_rate and total_cases both unchanged -- a CI re-run, not a re-measurement) leaves a prior
decision alone: resetting reviewer/note/decided_at on every identical re-run would destroy the
audit trail of "who approved this exact score" for no safety gained.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from egpt_retail.db.schema_utils import aensure_schema as _aensure_schema
from egpt_retail.db.schema_utils import ensure_schema as _ensure_schema

__all__ = [
    "RehearsalStatus",
    "RehearsalReview",
    "RehearsalReviewStore",
]


class RehearsalStatus(str, Enum):
    """Three states, for the same reason K7's ``ReviewState`` has three: collapsing "rejected"
    into "pending" would make a person's "no, this is not safe" indistinguishable from "nobody
    has looked yet", and the gate would treat them alike."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RehearsalReview:
    """One (skill_identifier, skill_version)'s current review state."""

    skill_identifier: str
    skill_version: str
    status: RehearsalStatus
    golden_pass_rate: float
    total_cases: int
    reviewer: Optional[str]
    note: str
    recorded_at: datetime
    decided_at: Optional[datetime]


_SCHEMA = "retail_evals"

_DDL = (
    f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {_SCHEMA}.rehearsal_review (
        skill_identifier text             NOT NULL,
        skill_version    text             NOT NULL,
        status           text             NOT NULL DEFAULT 'pending',
        golden_pass_rate double precision NOT NULL,
        total_cases      integer          NOT NULL,
        reviewer         text,
        note             text             NOT NULL DEFAULT '',
        recorded_at      timestamptz      NOT NULL DEFAULT now(),
        decided_at       timestamptz,
        PRIMARY KEY (skill_identifier, skill_version),
        CONSTRAINT rehearsal_status_valid
            CHECK (status IN ('pending', 'approved', 'rejected')),
        CONSTRAINT rehearsal_decision_attributed
            CHECK ((status = 'pending') = (reviewer IS NULL))
    )""",
    f"""COMMENT ON TABLE {_SCHEMA}.rehearsal_review IS
        'A person''s confirmation that a golden-set score is safe to publish on (R15, D14). '
        'record_run() reopens the row to pending when the score actually changes: an approval '
        'of a previous score is never read as approval of a new one. An identical re-run '
        '(same pass_rate and total_cases) leaves a prior decision -- and its audit trail -- '
        'alone.'""",
)

_ROW_COLUMNS = (
    "skill_identifier, skill_version, status, golden_pass_rate, total_cases, "
    "reviewer, note, recorded_at, decided_at"
)


class RehearsalReviewStore:
    """One row per (skill_identifier, skill_version). The connection is injected; the caller
    owns its lifetime -- the same convention as K7's ``ProposalStore``."""

    def __init__(self, connection: Any, schema: str = _SCHEMA) -> None:
        self._connection = connection
        self._schema = schema

    # --- schema --------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table if absent. Idempotent and safe from several processes at once --
        see ``ProposalStore.ensure_schema`` for why this is self-provisioning rather than a
        ``MigrationManager`` migration, and why the advisory lock is needed at all.

        The lock-then-DDL-then-commit sequence itself is ``egpt_retail.db.schema_utils``'s
        shared helper, not reimplemented here -- see that module for why the other five stores
        that also copy this boilerplate by hand are deliberately left alone in this change."""
        _ensure_schema(self._connection, _DDL, "egpt_retail.evals.ddl")

    async def aensure_schema(self) -> None:
        """Async variant."""
        await _aensure_schema(self._connection, _DDL, "egpt_retail.evals.ddl")

    # --- recording a fresh score -----------------------------------------------------------

    def record_run(
        self,
        skill_identifier: str,
        skill_version: str,
        *,
        pass_rate: float,
        total_cases: int,
    ) -> RehearsalReview:
        """Record a golden-set score. Reopens review to pending only when the score actually
        moved; an identical re-run preserves the prior decision untouched.

        Unlike K7's ``propose`` (which never resurrects a decided row), a new *measurement* of
        the same version is a new fact a person has not seen yet -- but a re-run that reproduces
        the same ``pass_rate``/``total_cases`` (a CI re-run with no prompt change, say) is not a
        new fact. Unconditionally resetting ``reviewer``/``note``/``decided_at`` on every call
        would destroy the answer to "who approved this exact score" for no safety gained, so the
        ``DO UPDATE`` only resets those columns when the incoming score differs from the row's
        current one; otherwise every column but ``recorded_at`` (a fresh run did happen) is left
        as the previous ``UPDATE``/``INSERT`` set it.

        Uses ``RETURNING`` rather than a separate ``SELECT`` after commit -- one round trip
        instead of two, and no window between commit and read-back for a concurrent write
        (e.g. a re-triggered CI run for the same skill/version) to change the row first.

        The "did the score actually change" check below is exact equality on the
        ``double precision`` column, not the epsilon comparison ``check_golden_gate.
        _review_check`` uses for "does this review match this score" -- deliberately: both
        sides of *that* comparison are two independently derived floats (a caller-supplied
        score, a store-persisted one), where floating-point round-trip noise is a real risk.
        Here, ``EXCLUDED.golden_pass_rate`` is the exact same Python ``pass_rate`` argument as
        it went into this one ``INSERT``, compared only against whatever value Postgres
        already stored from an *earlier* call with the same argument -- no independent
        computation on either side, so no epsilon is needed. If
        ``pass_rate`` ever starts arriving via a lossy path (e.g. a JSON round trip) rather than
        a direct Python float, revisit this alongside ``check_golden_gate``'s tolerance.
        """
        table = f"{self._schema}.rehearsal_review"
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {table}
                    (skill_identifier, skill_version, status, golden_pass_rate, total_cases,
                     reviewer, note, recorded_at, decided_at)
                    VALUES (%s, %s, 'pending', %s, %s, NULL, '', now(), NULL)
                    ON CONFLICT (skill_identifier, skill_version) DO UPDATE SET
                        golden_pass_rate = EXCLUDED.golden_pass_rate,
                        total_cases = EXCLUDED.total_cases,
                        recorded_at = now(),
                        status = CASE
                            WHEN {table}.golden_pass_rate = EXCLUDED.golden_pass_rate
                             AND {table}.total_cases = EXCLUDED.total_cases
                            THEN {table}.status ELSE 'pending' END,
                        reviewer = CASE
                            WHEN {table}.golden_pass_rate = EXCLUDED.golden_pass_rate
                             AND {table}.total_cases = EXCLUDED.total_cases
                            THEN {table}.reviewer ELSE NULL END,
                        note = CASE
                            WHEN {table}.golden_pass_rate = EXCLUDED.golden_pass_rate
                             AND {table}.total_cases = EXCLUDED.total_cases
                            THEN {table}.note ELSE '' END,
                        decided_at = CASE
                            WHEN {table}.golden_pass_rate = EXCLUDED.golden_pass_rate
                             AND {table}.total_cases = EXCLUDED.total_cases
                            THEN {table}.decided_at ELSE NULL END
                    RETURNING {_ROW_COLUMNS}""",
                (skill_identifier, skill_version, pass_rate, total_cases),
            )
            row = cursor.fetchone()
        self._connection.commit()
        assert (
            row is not None
        )  # INSERT ... ON CONFLICT DO UPDATE always returns exactly one row
        return self._row_from_tuple(row)

    async def arecord_run(
        self,
        skill_identifier: str,
        skill_version: str,
        *,
        pass_rate: float,
        total_cases: int,
    ) -> RehearsalReview:
        """Async variant."""
        return await asyncio.to_thread(
            self.record_run,
            skill_identifier,
            skill_version,
            pass_rate=pass_rate,
            total_cases=total_cases,
        )

    # --- reading -----------------------------------------------------------------------

    def get(
        self, skill_identifier: str, skill_version: str
    ) -> Optional[RehearsalReview]:
        """The current row, or ``None`` if no golden run has ever been recorded for this
        (skill, version)."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT {_ROW_COLUMNS}
                    FROM {self._schema}.rehearsal_review
                    WHERE skill_identifier = %s AND skill_version = %s""",
                (skill_identifier, skill_version),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_from_tuple(row)

    @staticmethod
    def _row_from_tuple(row: Any) -> RehearsalReview:
        """Builds a :class:`RehearsalReview` from a row shaped like ``_ROW_COLUMNS`` -- shared
        by ``get``, ``record_run`` and ``_decide`` so the column order is written once."""
        return RehearsalReview(
            skill_identifier=row[0],
            skill_version=row[1],
            status=RehearsalStatus(row[2]),
            golden_pass_rate=row[3],
            total_cases=row[4],
            reviewer=row[5],
            note=row[6] or "",
            recorded_at=row[7],
            decided_at=row[8],
        )

    async def aget(
        self, skill_identifier: str, skill_version: str
    ) -> Optional[RehearsalReview]:
        """Async variant."""
        return await asyncio.to_thread(self.get, skill_identifier, skill_version)

    # --- deciding -----------------------------------------------------------------------

    def approve(
        self, skill_identifier: str, skill_version: str, reviewer: str, note: str = ""
    ) -> RehearsalReview:
        """A person confirms this score is safe to publish on. Only a pending row moves."""
        return self._decide(
            skill_identifier, skill_version, RehearsalStatus.APPROVED, reviewer, note
        )

    async def aapprove(
        self, skill_identifier: str, skill_version: str, reviewer: str, note: str = ""
    ) -> RehearsalReview:
        """Async variant."""
        return await asyncio.to_thread(
            self.approve, skill_identifier, skill_version, reviewer, note
        )

    def reject(
        self, skill_identifier: str, skill_version: str, reviewer: str, note: str = ""
    ) -> RehearsalReview:
        """A person confirms this score is *not* safe to publish on. Only a pending row moves."""
        return self._decide(
            skill_identifier, skill_version, RehearsalStatus.REJECTED, reviewer, note
        )

    async def areject(
        self, skill_identifier: str, skill_version: str, reviewer: str, note: str = ""
    ) -> RehearsalReview:
        """Async variant."""
        return await asyncio.to_thread(
            self.reject, skill_identifier, skill_version, reviewer, note
        )

    def _decide(
        self,
        skill_identifier: str,
        skill_version: str,
        status: RehearsalStatus,
        reviewer: str,
        note: str,
    ) -> RehearsalReview:
        """The one place a review's status changes while it is pending.

        ``WHERE status = 'pending'`` is the whole enforcement, mirroring
        ``ProposalStore._decide``: a decided row simply does not match, so two reviewers
        cannot both win a race.

        **Any** re-decide of an already-decided row is refused, including one that asks for
        the same status it already holds -- deciding a chosen precedent among the two the
        review named (id=3988596555 / id=3988655156): re-deciding to the same status silently
        no-op-succeeding would discard the second caller's ``reviewer``/``note`` with no error,
        which is worse than raising, and it deviates from K7's own ``ProposalStore._decide``
        (this module's own stated model), which surfaces every untouched id rather than
        treating a same-outcome re-decide as a success. A reviewer who wants to re-affirm a
        decision with a new note should record a fresh run (reopening review) rather than
        re-decide a row that is already decided.

        Uses ``RETURNING`` rather than a separate ``SELECT`` after commit, for the same reason
        ``record_run`` does: one round trip instead of two, and no window for a concurrent
        write to land between the commit and the read-back.
        """
        if not reviewer.strip():
            raise ValueError(
                "a rehearsal decision must be attributed; reviewer was blank. An unattributed "
                "approval is indistinguishable from no review at all (D14, mirroring D10)."
            )
        table = f"{self._schema}.rehearsal_review"
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {table}
                    SET status = %s, reviewer = %s, note = %s, decided_at = now()
                    WHERE skill_identifier = %s AND skill_version = %s AND status = 'pending'
                    RETURNING {_ROW_COLUMNS}""",
                (status.value, reviewer, note, skill_identifier, skill_version),
            )
            updated = cursor.fetchone()
            if updated is not None:
                self._connection.commit()
                return self._row_from_tuple(updated)

            # Nothing matched WHERE status = 'pending': either no such row, or it is already
            # decided. Read the current row (if any) to say which, in the same transaction.
            cursor.execute(
                f"""SELECT {_ROW_COLUMNS} FROM {table}
                    WHERE skill_identifier = %s AND skill_version = %s""",
                (skill_identifier, skill_version),
            )
            current_row = cursor.fetchone()
        self._connection.commit()

        if current_row is None:
            raise ValueError(
                f"no golden run has been recorded for {skill_identifier} {skill_version}; "
                "call record_run() first."
            )
        current = self._row_from_tuple(current_row)
        raise ValueError(
            f"{skill_identifier} {skill_version} is already {current.status.value} "
            f"(by {current.reviewer}, {current.note!r}); a decided review is not "
            "re-decided -- record a fresh run to reopen it."
        )
