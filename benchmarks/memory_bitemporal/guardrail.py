"""Append-only guardrail conformance check (SPEC SS6.1).

SS6.1 requires that expire-and-reinsert is the **only** mutation path: `expired_at` is the sole
updatable column and it is write-once; the identity columns and both time axes are immutable
after insert. [schema.sql](schema.sql) SS2b enforces this with the `memory_edge_append_only`
trigger.

A rule that is merely *documented* is the failure mode this whole spec is trying to avoid, so the
trigger is scored rather than asserted. Each case runs in its own transaction -- a rejected
statement aborts its transaction, so sharing one would mask every case after the first.

The `ON CONFLICT DO UPDATE SET <every column>` case is the specific shape a sibling storage layer
shipped: it rewrites `t_valid`/`t_invalid`/`created_at`/`expired_at` in place, silently destroying
the history SCD-2 exists to keep. That is the case this guardrail exists for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_EDGE_ID = "__guardrail_probe__"

_INSERT = (
    "INSERT INTO memory_edge "
    "(edge_id, subject_id, predicate, object_id, user_id, t_valid, created_at) "
    "VALUES (%s,'user','lives_in','NYC','u1', to_timestamp(1704067200), to_timestamp(1704067200))"
)

# (name, SQL, params, must_be_rejected)
_CASES: List[Tuple[str, str, tuple, bool]] = [
    (
        "in_place_t_invalid",
        "UPDATE memory_edge SET t_invalid = to_timestamp(1717200000) WHERE edge_id=%s",
        (_EDGE_ID,),
        True,
    ),
    (
        "in_place_t_valid",
        "UPDATE memory_edge SET t_valid = to_timestamp(1717200000) WHERE edge_id=%s",
        (_EDGE_ID,),
        True,
    ),
    (
        "in_place_created_at",
        "UPDATE memory_edge SET created_at = to_timestamp(1717200000) WHERE edge_id=%s",
        (_EDGE_ID,),
        True,
    ),
    (
        "in_place_object_id",
        "UPDATE memory_edge SET object_id = 'LA' WHERE edge_id=%s",
        (_EDGE_ID,),
        True,
    ),
    (
        # The sibling-defect shape: blanket upsert rewriting every column.
        "on_conflict_do_update_all_columns",
        "INSERT INTO memory_edge "
        "(edge_id, subject_id, predicate, object_id, user_id, t_valid, t_invalid, created_at) "
        "VALUES (%s,'user','lives_in','LA','u1', to_timestamp(1717200000), NULL, "
        "to_timestamp(1717200000)) "
        "ON CONFLICT (edge_id) DO UPDATE SET object_id=EXCLUDED.object_id, "
        "t_valid=EXCLUDED.t_valid, t_invalid=EXCLUDED.t_invalid, created_at=EXCLUDED.created_at",
        (_EDGE_ID,),
        True,
    ),
    (
        "legitimate_expire",
        "UPDATE memory_edge SET expired_at = to_timestamp(1717200000) WHERE edge_id=%s",
        (_EDGE_ID,),
        False,
    ),
    (
        "payload_stays_mutable",
        "UPDATE memory_edge SET fact = 'payload is editable' WHERE edge_id=%s",
        (_EDGE_ID,),
        False,
    ),
]

# expired_at is write-once: a second expire on an already-expired row must be rejected. Needs a
# two-statement setup, so it is run separately from the single-statement cases above.
_WRITE_ONCE = (
    "UPDATE memory_edge SET expired_at = to_timestamp(1717200000) WHERE edge_id=%s",
    "UPDATE memory_edge SET expired_at = to_timestamp(1725148800) WHERE edge_id=%s",
)


@dataclass
class GuardrailResult:
    passed: int = 0
    total: int = 0
    failures: List[str] = field(default_factory=list)
    skipped: Optional[str] = None

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0


def check_append_only(dsn: str) -> GuardrailResult:
    """Score the SS6.1 guardrail against a live Postgres. Never leaves rows behind."""
    res = GuardrailResult()
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment dependent
        res.skipped = f"psycopg unavailable: {exc}"
        return res

    try:
        conn = psycopg.connect(dsn, autocommit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        res.skipped = f"postgres unreachable: {exc}"
        return res

    try:
        for name, sql, params, must_reject in _CASES:
            res.total += 1
            rejected = _run_isolated(conn, [(_INSERT, (_EDGE_ID,)), (sql, params)])
            if rejected is must_reject:
                res.passed += 1
            else:
                verb = "was allowed" if must_reject else "was rejected"
                res.failures.append(f"{name}: {verb} (expected the opposite)")

        res.total += 1
        rejected = _run_isolated(
            conn,
            [(_INSERT, (_EDGE_ID,)), (_WRITE_ONCE[0], (_EDGE_ID,)), (_WRITE_ONCE[1], (_EDGE_ID,))],
        )
        if rejected:
            res.passed += 1
        else:
            res.failures.append("expired_at_write_once: second expire was allowed (expected rejection)")
    finally:
        conn.close()
    return res


def _run_isolated(conn, statements: List[Tuple[str, tuple]]) -> bool:
    """Run statements in a transaction that is always rolled back. True if any was rejected."""
    rejected = False
    try:
        with conn.cursor() as cur:
            for sql, params in statements:
                cur.execute(sql, params)  # type: ignore[arg-type]
    except Exception:
        rejected = True
    finally:
        conn.rollback()
    return rejected
