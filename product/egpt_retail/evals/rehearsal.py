"""Rehearsal review (R15, D14): a person confirms a golden-set score before publish proceeds.

The delivery plan is explicit that automation alone does not gate activation: "Rehearsal review
by a person still gates activation." A golden pass rate is a number a deterministic executor can
compare to a threshold; whether that number is *safe to publish on* is a decision the build plan
reserves for a person, the same way K7 reserves accepting a proposal for a person rather than
letting an agent's own confidence decide.

Modelled on K7's ``ProposalStore`` (``egpt_retail/semantic/proposals.py``): a raw connection the
caller owns the lifetime of, a state machine enforced in the update predicate rather than by a
read-then-write, and an attributed decision made unrepresentable any other way. It differs from
K7 in one respect that matters: **a fresh golden-set run always reopens the row to pending.**
K7's proposals are decided once and stay decided; a rehearsal review is a claim about *this
score*, and approving version 0.1.0's 95% must never be read as approving 0.2.0's 60% just
because nobody re-ran review in between.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

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
        'record_run() always reopens the row to pending: an approval of a previous score is '
        'never read as approval of a new one.'""",
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
        ``MigrationManager`` migration, and why the advisory lock is needed at all."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("egpt_retail.evals.ddl",),
            )
            for statement in _DDL:
                cursor.execute(statement)
        self._connection.commit()

    async def aensure_schema(self) -> None:
        """Async variant."""
        await asyncio.to_thread(self.ensure_schema)

    # --- recording a fresh score -----------------------------------------------------------

    def record_run(
        self,
        skill_identifier: str,
        skill_version: str,
        *,
        pass_rate: float,
        total_cases: int,
    ) -> RehearsalReview:
        """Record a fresh golden-set score and (re)open review as pending.

        Unconditional, unlike K7's ``propose`` (which never resurrects a decided row): a new
        measurement of the *same* version is a new fact a person has not seen yet, however that
        version was reviewed last time.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.rehearsal_review
                    (skill_identifier, skill_version, status, golden_pass_rate, total_cases,
                     reviewer, note, recorded_at, decided_at)
                    VALUES (%s, %s, 'pending', %s, %s, NULL, '', now(), NULL)
                    ON CONFLICT (skill_identifier, skill_version) DO UPDATE SET
                        status = 'pending',
                        golden_pass_rate = EXCLUDED.golden_pass_rate,
                        total_cases = EXCLUDED.total_cases,
                        reviewer = NULL,
                        note = '',
                        recorded_at = now(),
                        decided_at = NULL""",
                (skill_identifier, skill_version, pass_rate, total_cases),
            )
        self._connection.commit()
        review = self.get(skill_identifier, skill_version)
        assert review is not None  # the insert/update above just wrote this exact row
        return review

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
                f"""SELECT skill_identifier, skill_version, status, golden_pass_rate,
                           total_cases, reviewer, note, recorded_at, decided_at
                    FROM {self._schema}.rehearsal_review
                    WHERE skill_identifier = %s AND skill_version = %s""",
                (skill_identifier, skill_version),
            )
            row = cursor.fetchone()
        if row is None:
            return None
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
        cannot both win a race, and re-deciding an already-decided review for *this exact
        score* is refused rather than silently overwritten.
        """
        if not reviewer.strip():
            raise ValueError(
                "a rehearsal decision must be attributed; reviewer was blank. An unattributed "
                "approval is indistinguishable from no review at all (D14, mirroring D10)."
            )
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.rehearsal_review
                    SET status = %s, reviewer = %s, note = %s, decided_at = now()
                    WHERE skill_identifier = %s AND skill_version = %s AND status = 'pending'""",
                (status.value, reviewer, note, skill_identifier, skill_version),
            )
            changed = cursor.rowcount or 0
        self._connection.commit()
        current = self.get(skill_identifier, skill_version)
        if current is None:
            raise ValueError(
                f"no golden run has been recorded for {skill_identifier} {skill_version}; "
                "call record_run() first."
            )
        if changed == 0 and current.status is not status:
            raise ValueError(
                f"{skill_identifier} {skill_version} is already {current.status.value} "
                f"(by {current.reviewer}, {current.note!r}); a decided review is not "
                "re-decided -- record a fresh run to reopen it."
            )
        return current
