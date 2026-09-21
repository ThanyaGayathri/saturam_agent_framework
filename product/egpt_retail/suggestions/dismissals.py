"""The dismissed-suggestion table: what a person said no to (contract 1.2.9).

A suggestion run is a fresh answer from a model with no memory: propose the same KPI on
Monday, have it declined, and on Tuesday's run it is proposed again -- unless something
remembers. This table is that memory, and nothing else: an identifier and when it was
dismissed. It is the same rule ``semantic/proposals.py`` applies to proposals ("re-proposing a
rejected thing is the failure mode this guards"), for the suggestions that are not
proposals -- they carry a full ``KpiSuggestion`` in a run's ``result`` (feasibility,
confidence, the whole explanation), which a proposal's payload cannot hold, and they have no
accept/reject workflow to sit in.

The dismissal is **by identifier, and deployment-wide**. Identifiers are what a KPI is called
in the registry, so dismissing ``orders__average_order_value`` hides it for every run, every
source and every viewer; it is not per-run because a later run that proposes it again is
exactly the case this exists for. Applied wherever a suggestion is read (``GET /kpis/
suggestions/current`` and every run's ``result``), not when it is generated, so a dismissal
also hides a suggestion that is already stored.

Same self-provisioning posture as ``runs.py`` (and the same schema and advisory lock):
created by the code that owns it, at boot.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, FrozenSet

__all__ = [
    "DISMISSAL_SCHEMA",
    "SuggestionDismissal",
    "SuggestionDismissalStore",
    "normalise_identifier",
]

#: The control-plane schema the suggestion runs already live in.
DISMISSAL_SCHEMA = "semantic"


def normalise_identifier(identifier: str) -> str:
    """The form an identifier is stored and compared in: trimmed and lower-cased, since the
    registry compares identifiers case-insensitively (``known_identifiers`` in K12's
    ``assemble``). Raises ``ValueError`` for one that is empty once trimmed."""
    normalised = (identifier or "").strip().lower()
    if not normalised:
        raise ValueError("A suggestion identifier must not be empty.")
    return normalised


@dataclass(frozen=True)
class SuggestionDismissal:
    identifier: str
    dismissed_at: datetime
    dismissed_by: str = ""


def _ddl(schema: str) -> tuple:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.kpi_suggestion_dismissal (
        identifier   text        PRIMARY KEY,
        dismissed_by text        NOT NULL DEFAULT '',
        dismissed_at timestamptz NOT NULL DEFAULT now()
    )""",
        f"""COMMENT ON TABLE {schema}.kpi_suggestion_dismissal IS
        'Suggestions a person removed (contract 1.2.9). By lower-cased identifier, deployment-wide; '
        'left out of GET /kpis/suggestions/current and of every suggestion run read.'""",
    )


_COLUMNS = "identifier, dismissed_at, dismissed_by"


class SuggestionDismissalStore:
    """The dismissal table. The connection is injected; the caller owns its lifetime."""

    def __init__(self, connection: Any, schema: str = DISMISSAL_SCHEMA) -> None:
        self._connection = connection
        self._schema = schema

    # --- schema ------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table if absent. Idempotent, and safe from several processes -- the
        same transaction-scoped advisory lock the other ``semantic`` stores take."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("egpt_retail.semantic.ddl",),
            )
            for statement in _ddl(self._schema):
                cursor.execute(statement)
        self._connection.commit()

    async def aensure_schema(self) -> None:
        """Async variant."""
        await asyncio.to_thread(self.ensure_schema)

    # --- writing -----------------------------------------------------------------------

    def dismiss(self, identifier: str, dismissed_by: str = "") -> SuggestionDismissal:
        """Record that ``identifier`` is not wanted, and return the dismissal.

        Idempotent: dismissing what is already dismissed changes nothing and returns the
        existing record (the original time and person, not this call's) -- a double click,
        or two people dismissing the same card, is not an error and does not rewrite history.
        Raises ``ValueError`` for an empty identifier.
        """
        key = normalise_identifier(identifier)
        with self._connection.cursor() as cursor:
            # DO UPDATE with a no-op assignment rather than DO NOTHING: only DO UPDATE
            # returns the existing row, which is what lets this stay a single statement.
            cursor.execute(
                f"""INSERT INTO {self._schema}.kpi_suggestion_dismissal
                        (identifier, dismissed_by)
                    VALUES (%s, %s)
                    ON CONFLICT (identifier) DO UPDATE
                        SET identifier = {self._schema}.kpi_suggestion_dismissal.identifier
                    RETURNING {_COLUMNS}""",
                (key, dismissed_by),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _record(row)

    async def adismiss(
        self, identifier: str, dismissed_by: str = ""
    ) -> SuggestionDismissal:
        """Async variant."""
        return await asyncio.to_thread(self.dismiss, identifier, dismissed_by)

    # --- reading -----------------------------------------------------------------------

    def identifiers(self) -> FrozenSet[str]:
        """Every dismissed identifier, lower-cased -- compare a suggestion's
        ``identifier.lower()`` against it."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT identifier FROM {self._schema}.kpi_suggestion_dismissal"
            )
            rows = cursor.fetchall()
        self._connection.commit()
        return frozenset(row[0] for row in rows)

    async def aidentifiers(self) -> FrozenSet[str]:
        """Async variant."""
        return await asyncio.to_thread(self.identifiers)


def _record(row: Any) -> SuggestionDismissal:
    identifier, dismissed_at, dismissed_by = row
    return SuggestionDismissal(
        identifier=identifier, dismissed_at=dismissed_at, dismissed_by=dismissed_by
    )
