"""Append-only audit trail for component-definition changes (EGPT-2.6).

Every lifecycle mutation on a component or one of its config versions writes one row here:
who did it, to what, when, and what changed. The trail is what makes runtime-editable agents
governable -- versioning, stages and rollback already existed; the missing piece was
attribution.

Append-only by construction
---------------------------
There is no update or delete method for this table anywhere in the DB API. Rows are written
by :func:`write_audit_row` and read by ``list_component_audit``; nothing else touches them.
That is the whole enforcement -- no trigger, no rule, no grant. It holds because the writer
is a private helper and the table is not exposed through any generic mutation path.

Written inside the mutation's own transaction
---------------------------------------------
:func:`write_audit_row` takes the caller's live session and runs on the statement that is
already open, so the audit row and the change it describes commit or roll back together.
A mutation that raises (published-config update, cross-tenant delete, a draft set as current)
leaves no audit row, and a mutation that succeeds cannot leave without one. That is why the
audit insert is *not* wrapped in its own try/except: swallowing the error would turn a
tamper-evident trail into an advisory one.

Ordering
--------
``audit_id`` is a database-assigned autoincrement key, and it -- not ``created_at`` -- is what
orders the history. ``created_at`` has second resolution, so an update followed immediately by
a publish shares a timestamp and could come back in either order; the read endpoint has to
return the two in the order they happened.

It is deliberately *not* a per-component ``max(seq) + 1`` counter. That is the technique
``upsert_config`` already uses for config version numbers, but borrowing it here would put an
unguarded read-modify-write race into ``upsert_component`` and ``delete_component``, which have
none today: two concurrent changes to one component would compute the same counter and one
would fail on the primary key. Since the audit write is fail-closed, that failure would take a
legitimate mutation down with it. A sequence has no such contention.

``set_current_version`` already has a read-modify-write of its own -- it reads
``current_version`` to report what a rollback moved away from -- so a ``max(seq)+1`` counter
would have piled a second, unrelated race onto the same read rather than adding a first one.
That existing race is guarded with a compare-and-swap on the pointer update: the write only
lands if ``current_version`` still matches what was just read, and a losing attempt re-reads and
retries instead of recording a ``from_version`` that was never actually current. A sequence key
needs no equivalent guard, because nothing reads it before writing to it.

The column is ``Integer`` on both backends, and that is load-bearing rather than stylistic:
Postgres turns an ``Integer`` primary key into ``SERIAL`` and SQLite only aliases its implicit
``rowid`` for a column declared exactly ``INTEGER``. A ``BigInteger`` primary key raises
``IntegrityError: NOT NULL constraint failed`` on SQLite -- so the SQLite schema deviates from
its ``BigInteger`` house style here on purpose.

No foreign key to ``components``
--------------------------------
``delete_component(hard_delete=True)`` deletes the component row and its configs. A foreign
key would make the audit trail either block that delete or vanish with it, and surviving the
deletion of the thing it describes is the entire point of the table. The schema definitions
therefore declare no FK, unlike ``component_configs`` and ``component_links``.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from egpt.utils.dttm import now_epoch_s

try:
    from sqlalchemy import Table
    from sqlalchemy.orm import Session
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Please install it using `pip install sqlalchemy`")


class ComponentAuditAction(str, Enum):
    """What happened to a component definition.

    Namespaced ``component.*`` / ``config.*`` because the two operate on different rows and a
    reader filtering the history wants one or the other.

    ``CONFIG_PUBLISH`` and ``CONFIG_ROLLBACK`` have no dedicated DB method behind them:
    publishing is ``upsert_config(stage="published")`` and rollback is ``set_current_version``.
    Both are derived from what the transaction actually did -- a stage transition into
    ``published``, and a move of ``current_version`` -- rather than from what the caller said
    it wanted, so an audit row cannot claim an action the database did not perform.
    """

    COMPONENT_CREATE = "component.create"
    COMPONENT_REACTIVATE = "component.reactivate"
    COMPONENT_UPDATE = "component.update"
    COMPONENT_DELETE = "component.delete"
    CONFIG_CREATE = "config.create"
    CONFIG_UPDATE = "config.update"
    CONFIG_PUBLISH = "config.publish"
    CONFIG_DELETE = "config.delete"
    CONFIG_ROLLBACK = "config.rollback"


# Actions a caller may filter the history by. Anything else is rejected at the API boundary
# rather than silently returning an empty page, so a typo'd filter is not mistaken for a
# component with no history.
COMPONENT_AUDIT_ACTIONS = frozenset(action.value for action in ComponentAuditAction)


@dataclass
class ComponentAuditRecord:
    """One entry in a component's change history.

    ``actor`` is the authenticated principal that made the change -- a JWT subject, a
    ``sa:<name>`` service account, or the scheduler. ``None`` means an unattributed
    programmatic write: ``Agent.save()``, the studio toolkit, a cookbook script. It is not a
    gap in the trail (the change is still recorded), it is the honest answer for a writer that
    never carried a principal.

    ``version`` is ``None`` for component-level actions, which do not belong to any one config
    version. ``details`` carries the facts that make an entry readable on its own -- which
    version a rollback moved away from, which fields an update touched -- and is deliberately
    free-form so adding one does not need a migration.
    """

    component_id: str
    action: str
    tenant_id: Optional[str] = None
    version: Optional[int] = None
    actor: Optional[str] = None
    note: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    created_at: Optional[int] = None
    audit_id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = now_epoch_s()

    def to_insert_values(self) -> Dict[str, Any]:
        """The column values for an INSERT.

        ``audit_id`` is omitted rather than passed as ``None``: the column carries a
        server-side default (``nextval`` on Postgres, the implicit rowid on SQLite), and an
        explicit ``NULL`` overrides that default instead of falling back to it -- verified as
        a ``NotNullViolation`` on Postgres and an ``IntegrityError`` on SQLite.
        """
        return {
            "component_id": self.component_id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "action": self.action,
            "actor": self.actor,
            "note": self.note,
            "details": self.details,
            "created_at": self.created_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict, including the assigned ``audit_id``. Preserves None values."""
        return {"audit_id": self.audit_id, **self.to_insert_values()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComponentAuditRecord":
        valid_keys = {
            "component_id",
            "audit_id",
            "tenant_id",
            "version",
            "action",
            "actor",
            "note",
            "details",
            "created_at",
        }
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


def resolve_audit_note(audit_note: Optional[str], notes: Optional[str] = None) -> Optional[str]:
    """The note to record for a mutation.

    An explicit ``audit_note`` always wins. Otherwise a config mutation's own ``notes`` is
    reused, because that is already the field a caller writes the reason for the change into
    and asking them to pass it twice would mostly produce an empty audit note.
    """
    return audit_note if audit_note is not None else notes


def write_audit_row(
    sess: Session,
    audit_table: Table,
    component_id: str,
    action: ComponentAuditAction,
    tenant_id: Optional[str] = None,
    version: Optional[int] = None,
    actor: Optional[str] = None,
    note: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one audit row on the caller's open transaction.

    Shared by the Postgres and SQLite backends so the two cannot drift on what an action means
    or which columns get filled. Uses SQLAlchemy Core only, so it is dialect-agnostic.

    Deliberately has no try/except. It runs on the caller's transaction, and swallowing a
    failure here would let a component change commit with no record of who made it -- which is
    the one outcome this table exists to prevent.
    """
    record = ComponentAuditRecord(
        component_id=component_id,
        action=action.value,
        tenant_id=tenant_id,
        version=version,
        actor=actor,
        note=note,
        details=details,
    )
    sess.execute(audit_table.insert().values(**record.to_insert_values()))


def audit_config_upsert(
    sess: Session,
    audit_table: Table,
    component_id: str,
    version: int,
    is_new_version: bool,
    prior_stage: Optional[str],
    final_stage: str,
    label: Optional[str] = None,
    previous_current_version: Optional[int] = None,
    tenant_id: Optional[str] = None,
    actor: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Record what an ``upsert_config`` call actually did.

    One method in the DB API covers three distinct lifecycle events, so the decision of which
    action to record lives here rather than in each backend, where the two could drift:

    ======================  ===========  ===========  ==================================
    ``is_new_version``      prior stage  final stage  recorded
    ======================  ===========  ===========  ==================================
    True                    --           draft        ``config.create``
    True                    --           published    ``config.create`` + ``config.publish``
    False                   draft        draft        ``config.update``
    False                   draft        published    ``config.publish``
    False                   published    --           unreachable: immutability raises first
    ======================  ===========  ===========  ==================================

    A draft edited straight to published records the publish alone, not an update followed by
    a publish: one call did one thing, and splitting it would inflate the history with an
    intermediate state that never existed.
    """
    if is_new_version:
        write_audit_row(
            sess,
            audit_table,
            component_id,
            ComponentAuditAction.CONFIG_CREATE,
            tenant_id=tenant_id,
            version=version,
            actor=actor,
            note=note,
            details={"stage": final_stage, "label": label},
        )

    became_published = final_stage == "published" and prior_stage != "published"
    if became_published:
        write_audit_row(
            sess,
            audit_table,
            component_id,
            ComponentAuditAction.CONFIG_PUBLISH,
            tenant_id=tenant_id,
            version=version,
            actor=actor,
            note=note,
            details={"from_stage": prior_stage, "previous_current_version": previous_current_version},
        )
    elif not is_new_version:
        write_audit_row(
            sess,
            audit_table,
            component_id,
            ComponentAuditAction.CONFIG_UPDATE,
            tenant_id=tenant_id,
            version=version,
            actor=actor,
            note=note,
            details={"stage": final_stage, "label": label},
        )
