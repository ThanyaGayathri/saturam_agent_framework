"""``PlaygroundStore`` -- saved prompts, chat filters, answer-format preferences and pinned
reports, in the control-plane database (R11).

One store, five tables under one schema, the same self-provisioning shape
``kpi/store.py``'s ``KpiRegistryStore`` uses: ``ensure_schema()`` behind an advisory lock (this
package's own tables would otherwise be invisible to ``MigrationManager``, which only tracks
the framework's curated table types -- see ``kpi/store.py``'s own note on why), a connection
injected and owned by the caller, sync methods with an ``asyncio.to_thread`` async twin.

Every resource is scoped by ``owner`` -- the caller's ``user_id`` -- not by tenant: this
deployment is single-customer (D3), so "whose saved prompt is this" is a per-user question,
not a per-tenant one.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, Optional, Sequence, Tuple

from egpt_retail.contracts.playground import (
    ChatFilters,
    ChatMode,
    Period,
    PlaygroundPreferences,
    Report,
    ReportItem,
    SavedPrompt,
)

__all__ = ["PLAYGROUND_SCHEMA", "PlaygroundStore"]

PLAYGROUND_SCHEMA = "playground"


def _ddl(schema: str) -> Tuple[str, ...]:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.saved_prompt (
            id            text PRIMARY KEY,
            owner         text        NOT NULL,
            text          text        NOT NULL,
            label         text        NOT NULL DEFAULT '',
            saved_at      timestamptz NOT NULL DEFAULT now(),
            last_used_at  timestamptz,
            usage_count   integer     NOT NULL DEFAULT 0,
            CONSTRAINT saved_prompt_owner_text_unique UNIQUE (owner, text)
        )""",
        f"""CREATE INDEX IF NOT EXISTS saved_prompt_owner_idx
            ON {schema}.saved_prompt (owner, saved_at DESC)""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.chat_filters (
            owner     text PRIMARY KEY,
            period    text NOT NULL DEFAULT 'week',
            from_date date,
            to_date   date
        )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.preferences (
            owner text PRIMARY KEY,
            mode  text NOT NULL DEFAULT 'full'
        )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.report (
            id         text PRIMARY KEY,
            owner      text        NOT NULL,
            title      text        NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE INDEX IF NOT EXISTS report_owner_idx
            ON {schema}.report (owner, created_at)""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.report_item (
            id              text PRIMARY KEY,
            report_id       text        NOT NULL REFERENCES {schema}.report(id) ON DELETE CASCADE,
            block           jsonb       NOT NULL,
            source_question text        NOT NULL DEFAULT '',
            run_id          text,
            use_case_id     text,
            pinned_at       timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE INDEX IF NOT EXISTS report_item_report_idx
            ON {schema}.report_item (report_id, pinned_at)""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.active_report (
            owner     text PRIMARY KEY,
            report_id text NOT NULL REFERENCES {schema}.report(id) ON DELETE CASCADE
        )""",
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class PlaygroundStore:
    """The playground schema in the control-plane database. The connection is injected; the
    caller owns its lifetime."""

    def __init__(self, connection: Any, schema: str = PLAYGROUND_SCHEMA) -> None:
        self._connection = connection
        self._schema = schema

    # --- schema ------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("egpt_retail.playground.ddl",),
            )
            for statement in _ddl(self._schema):
                cursor.execute(statement)
        self._connection.commit()

    async def aensure_schema(self) -> None:
        await asyncio.to_thread(self.ensure_schema)

    # --- saved prompts -------------------------------------------------------------------

    def save_prompt(self, owner: str, text: str, label: str = "") -> SavedPrompt:
        """Insert a new saved prompt, or return the existing one for ``(owner, text)``
        unchanged -- saving the same text twice is a no-op per the spec, not a second row."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.saved_prompt (id, owner, text, label)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (owner, text) DO NOTHING
                    RETURNING id, owner, text, label, saved_at, last_used_at, usage_count""",
                (_new_id("sp"), owner, text, label),
            )
            row = cursor.fetchone()
        self._connection.commit()
        if row is not None:
            return _prompt_from_row(row)
        existing = self.get_prompt_by_text(owner, text)
        assert existing is not None  # the ON CONFLICT target guarantees a row exists
        return existing

    async def asave_prompt(self, owner: str, text: str, label: str = "") -> SavedPrompt:
        return await asyncio.to_thread(self.save_prompt, owner, text, label)

    def get_prompt_by_text(self, owner: str, text: str) -> Optional[SavedPrompt]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT id, owner, text, label, saved_at, last_used_at, usage_count
                    FROM {self._schema}.saved_prompt WHERE owner = %s AND text = %s""",
                (owner, text),
            )
            row = cursor.fetchone()
        return _prompt_from_row(row) if row is not None else None

    def list_prompts(self, owner: str) -> Tuple[SavedPrompt, ...]:
        """Most recent first, per the spec's own example ordering."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT id, owner, text, label, saved_at, last_used_at, usage_count
                    FROM {self._schema}.saved_prompt
                    WHERE owner = %s ORDER BY saved_at DESC""",
                (owner,),
            )
            rows = cursor.fetchall()
        return tuple(_prompt_from_row(row) for row in rows)

    async def alist_prompts(self, owner: str) -> Tuple[SavedPrompt, ...]:
        return await asyncio.to_thread(self.list_prompts, owner)

    def update_prompt(
        self, owner: str, prompt_id: str, *, text: str, label: str = ""
    ) -> Optional[SavedPrompt]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.saved_prompt SET text = %s, label = %s
                    WHERE id = %s AND owner = %s
                    RETURNING id, owner, text, label, saved_at, last_used_at, usage_count""",
                (text, label, prompt_id, owner),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _prompt_from_row(row) if row is not None else None

    async def aupdate_prompt(
        self, owner: str, prompt_id: str, *, text: str, label: str = ""
    ) -> Optional[SavedPrompt]:
        return await asyncio.to_thread(
            self.update_prompt, owner, prompt_id, text=text, label=label
        )

    def delete_prompt(self, owner: str, prompt_id: str) -> bool:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self._schema}.saved_prompt WHERE id = %s AND owner = %s",
                (prompt_id, owner),
            )
            removed = bool(cursor.rowcount)
        self._connection.commit()
        return removed

    async def adelete_prompt(self, owner: str, prompt_id: str) -> bool:
        return await asyncio.to_thread(self.delete_prompt, owner, prompt_id)

    def mark_prompt_used(self, owner: str, prompt_id: str) -> Optional[SavedPrompt]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.saved_prompt
                    SET last_used_at = now(), usage_count = usage_count + 1
                    WHERE id = %s AND owner = %s
                    RETURNING id, owner, text, label, saved_at, last_used_at, usage_count""",
                (prompt_id, owner),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _prompt_from_row(row) if row is not None else None

    async def amark_prompt_used(
        self, owner: str, prompt_id: str
    ) -> Optional[SavedPrompt]:
        return await asyncio.to_thread(self.mark_prompt_used, owner, prompt_id)

    # --- chat filters (one row per owner) -------------------------------------------------

    def get_filters(self, owner: str) -> ChatFilters:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT owner, period, from_date, to_date FROM {self._schema}.chat_filters "
                "WHERE owner = %s",
                (owner,),
            )
            row = cursor.fetchone()
        if row is None:
            return ChatFilters(owner=owner)
        return ChatFilters(
            owner=row[0], period=Period(row[1]), from_date=row[2], to_date=row[3]
        )

    async def aget_filters(self, owner: str) -> ChatFilters:
        return await asyncio.to_thread(self.get_filters, owner)

    def set_filters(self, filters: ChatFilters) -> ChatFilters:
        """``filters`` is already validated by ``ChatFilters.__post_init__`` (the 730-day cap);
        this only persists it."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.chat_filters (owner, period, from_date, to_date)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (owner) DO UPDATE SET
                        period = EXCLUDED.period,
                        from_date = EXCLUDED.from_date,
                        to_date = EXCLUDED.to_date""",
                (
                    filters.owner,
                    filters.period.value,
                    filters.from_date,
                    filters.to_date,
                ),
            )
        self._connection.commit()
        return filters

    async def aset_filters(self, filters: ChatFilters) -> ChatFilters:
        return await asyncio.to_thread(self.set_filters, filters)

    # --- preferences (one row per owner) --------------------------------------------------

    def get_preferences(self, owner: str) -> PlaygroundPreferences:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT owner, mode FROM {self._schema}.preferences WHERE owner = %s",
                (owner,),
            )
            row = cursor.fetchone()
        if row is None:
            return PlaygroundPreferences(owner=owner)
        return PlaygroundPreferences(owner=row[0], mode=ChatMode(row[1]))

    async def aget_preferences(self, owner: str) -> PlaygroundPreferences:
        return await asyncio.to_thread(self.get_preferences, owner)

    def set_preferences(
        self, preferences: PlaygroundPreferences
    ) -> PlaygroundPreferences:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.preferences (owner, mode) VALUES (%s, %s)
                    ON CONFLICT (owner) DO UPDATE SET mode = EXCLUDED.mode""",
                (preferences.owner, preferences.mode.value),
            )
        self._connection.commit()
        return preferences

    async def aset_preferences(
        self, preferences: PlaygroundPreferences
    ) -> PlaygroundPreferences:
        return await asyncio.to_thread(self.set_preferences, preferences)

    # --- reports ---------------------------------------------------------------------------

    def list_reports(self, owner: str) -> Tuple[Optional[str], Tuple[Report, ...]]:
        """``(active_report_id, reports)`` -- creates one empty, active report for a fresh
        owner first, per the spec's own "a fresh user" example: the pin action needs a
        target, so an owner is never handed an empty list."""
        self._ensure_default_report(owner)
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM {self._schema}.report WHERE owner = %s ORDER BY created_at",
                (owner,),
            )
            report_ids = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                f"SELECT report_id FROM {self._schema}.active_report WHERE owner = %s",
                (owner,),
            )
            active_row = cursor.fetchone()
        active_report_id = active_row[0] if active_row else None
        reports = tuple(self.get_report(owner, rid) for rid in report_ids)
        return active_report_id, tuple(r for r in reports if r is not None)

    async def alist_reports(
        self, owner: str
    ) -> Tuple[Optional[str], Tuple[Report, ...]]:
        return await asyncio.to_thread(self.list_reports, owner)

    def _ensure_default_report(self, owner: str) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT 1 FROM {self._schema}.report WHERE owner = %s LIMIT 1",
                (owner,),
            )
            if cursor.fetchone() is not None:
                return
        self.create_report(owner, title="My first report", make_active=True)

    def create_report(self, owner: str, title: str, make_active: bool = True) -> Report:
        report_id = _new_id("rp")
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.report (id, owner, title)
                    VALUES (%s, %s, %s) RETURNING created_at, updated_at""",
                (report_id, owner, title),
            )
            created_at, updated_at = cursor.fetchone()
            if make_active:
                cursor.execute(
                    f"""INSERT INTO {self._schema}.active_report (owner, report_id)
                        VALUES (%s, %s)
                        ON CONFLICT (owner) DO UPDATE SET report_id = EXCLUDED.report_id""",
                    (owner, report_id),
                )
        self._connection.commit()
        return Report(
            id=report_id,
            owner=owner,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
        )

    async def acreate_report(
        self, owner: str, title: str, make_active: bool = True
    ) -> Report:
        return await asyncio.to_thread(self.create_report, owner, title, make_active)

    def get_report(self, owner: str, report_id: str) -> Optional[Report]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT id, owner, title, created_at, updated_at
                    FROM {self._schema}.report WHERE id = %s AND owner = %s""",
                (report_id, owner),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                f"""SELECT id, report_id, block, source_question, run_id, use_case_id, pinned_at
                    FROM {self._schema}.report_item
                    WHERE report_id = %s ORDER BY pinned_at""",
                (report_id,),
            )
            item_rows = cursor.fetchall()
        items = tuple(_item_from_row(item_row) for item_row in item_rows)
        return Report(
            id=row[0],
            owner=row[1],
            title=row[2],
            created_at=row[3],
            updated_at=row[4],
            items=items,
        )

    async def aget_report(self, owner: str, report_id: str) -> Optional[Report]:
        return await asyncio.to_thread(self.get_report, owner, report_id)

    def rename_report(self, owner: str, report_id: str, title: str) -> Optional[Report]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.report SET title = %s, updated_at = now()
                    WHERE id = %s AND owner = %s""",
                (title, report_id, owner),
            )
            updated = cursor.rowcount > 0
        self._connection.commit()
        return self.get_report(owner, report_id) if updated else None

    async def arename_report(
        self, owner: str, report_id: str, title: str
    ) -> Optional[Report]:
        return await asyncio.to_thread(self.rename_report, owner, report_id, title)

    def delete_report(self, owner: str, report_id: str) -> bool:
        """Deletes the report. Does **not** replace it -- :meth:`list_reports` creates a
        fresh empty one on its next call if the owner is left with none, which is the same
        "fresh user" path a brand-new owner takes, so the spec's "leaves an empty one in its
        place" holds without this method needing to know it was the last one."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self._schema}.report WHERE id = %s AND owner = %s",
                (report_id, owner),
            )
            removed = bool(cursor.rowcount)
            if removed:
                cursor.execute(
                    f"""DELETE FROM {self._schema}.active_report
                        WHERE owner = %s AND report_id = %s""",
                    (owner, report_id),
                )
        self._connection.commit()
        return removed

    async def adelete_report(self, owner: str, report_id: str) -> bool:
        return await asyncio.to_thread(self.delete_report, owner, report_id)

    def set_active_report(self, owner: str, report_id: str) -> bool:
        """``False`` if ``report_id`` does not belong to ``owner`` -- the router turns that
        into the spec's 404, same as every other id-addressed operation here."""
        if self.get_report(owner, report_id) is None:
            return False
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.active_report (owner, report_id)
                    VALUES (%s, %s)
                    ON CONFLICT (owner) DO UPDATE SET report_id = EXCLUDED.report_id""",
                (owner, report_id),
            )
        self._connection.commit()
        return True

    async def aset_active_report(self, owner: str, report_id: str) -> bool:
        return await asyncio.to_thread(self.set_active_report, owner, report_id)

    def pin_block(
        self,
        owner: str,
        report_id: str,
        *,
        block: Dict[str, Any],
        source_question: str = "",
        run_id: Optional[str] = None,
        use_case_id: Optional[str] = None,
    ) -> Optional[ReportItem]:
        """``None`` if the report does not belong to ``owner``. Pinning a block already in
        the report is a no-op that returns the existing item, per the spec."""
        if self.get_report(owner, report_id) is None:
            return None
        block_id = block.get("id")
        with self._connection.cursor() as cursor:
            if block_id:
                cursor.execute(
                    f"""SELECT id, report_id, block, source_question, run_id, use_case_id, pinned_at
                        FROM {self._schema}.report_item
                        WHERE report_id = %s AND block->>'id' = %s""",
                    (report_id, block_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    return _item_from_row(existing)
            item_id = _new_id("it")
            cursor.execute(
                f"""INSERT INTO {self._schema}.report_item
                    (id, report_id, block, source_question, run_id, use_case_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, report_id, block, source_question, run_id, use_case_id, pinned_at""",
                (
                    item_id,
                    report_id,
                    json.dumps(block),
                    source_question,
                    run_id,
                    use_case_id,
                ),
            )
            row = cursor.fetchone()
            cursor.execute(
                f"UPDATE {self._schema}.report SET updated_at = now() WHERE id = %s",
                (report_id,),
            )
        self._connection.commit()
        return _item_from_row(row)

    async def apin_block(
        self,
        owner: str,
        report_id: str,
        *,
        block: Dict[str, Any],
        source_question: str = "",
        run_id: Optional[str] = None,
        use_case_id: Optional[str] = None,
    ) -> Optional[ReportItem]:
        return await asyncio.to_thread(
            self.pin_block,
            owner,
            report_id,
            block=block,
            source_question=source_question,
            run_id=run_id,
            use_case_id=use_case_id,
        )

    def unpin_block(self, owner: str, report_id: str, block_id: str) -> bool:
        if self.get_report(owner, report_id) is None:
            return False
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self._schema}.report_item WHERE id = %s AND report_id = %s",
                (block_id, report_id),
            )
            removed = bool(cursor.rowcount)
        self._connection.commit()
        return removed

    async def aunpin_block(self, owner: str, report_id: str, block_id: str) -> bool:
        return await asyncio.to_thread(self.unpin_block, owner, report_id, block_id)


def _prompt_from_row(row: Sequence[Any]) -> SavedPrompt:
    return SavedPrompt(
        id=row[0],
        owner=row[1],
        text=row[2],
        label=row[3] or "",
        saved_at=row[4],
        last_used_at=row[5],
        usage_count=row[6],
    )


def _item_from_row(row: Sequence[Any]) -> ReportItem:
    block = row[2]
    if isinstance(block, str):
        block = json.loads(block)
    return ReportItem(
        id=row[0],
        report_id=row[1],
        block=block,
        source_question=row[3] or "",
        run_id=row[4],
        use_case_id=row[5],
        pinned_at=row[6],
    )
