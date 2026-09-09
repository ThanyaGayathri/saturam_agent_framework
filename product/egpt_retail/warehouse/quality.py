"""The quality gates: measure, decide, and say why in one readable paragraph (R4, G8).

Three measurements gate gold -- row count delta against the prior load, null rates on key
columns, and referential integrity between entities. Each is a ``.sql`` file that returns
counts and rates and contains no threshold at all, so an analyst can run it in psql to see
what a night looked like without needing to know the policy. The threshold comparison, and
therefore the permit/block decision, is here: "anything that permits or blocks is an
executor, never a model", and keeping the decision in one place makes it reviewable.

The gates measure and record; they do not stop. One executor -- ``decide_load_verdict`` --
aggregates and stops. Two reasons. A single clearly-named thing doing the blocking is easier
to review than three that each might. And if each gate stopped on its own failure, an
operator with two problems on one night would fix them one at a time over two nights, because
the second gate would never have run.

:func:`render_load_reason` is pure, and it is the artefact R4's evidence bar asks for: a
reason "readable without opening a log". The same string reaches ``quality.load_run.reason``,
the workflow's run history through ``StepOutput.error``, and the operator's terminal.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.contracts.landing import LANDING_SCHEMAS
from egpt_retail.warehouse.sql import check_sql

__all__ = [
    "StreamThresholds",
    "GATE_DEFAULTS",
    "GateResult",
    "LoadVerdict",
    "resolve_thresholds",
    "edge_child_stream",
    "render_gate_reason",
    "render_load_reason",
    "evaluate_row_count_delta",
    "measure_row_count_delta",
    "SUBJECT_WHOLE_LOAD",
    "measure_key_null_rate",
    "measure_referential_integrity",
    "record_gate_results",
    "arecord_gate_results",
    "aggregate_verdict",
    "read_gate_results",
]

GATE_ROW_COUNT_DELTA = "row_count_delta"
GATE_KEY_NULL_RATE = "key_null_rate"
GATE_REFERENTIAL_INTEGRITY = "referential_integrity"

#: Subject for a finding about the load as a whole rather than one stream.
SUBJECT_WHOLE_LOAD = "<load>"


@dataclass(frozen=True)
class StreamThresholds:
    """What a stream is allowed to look like before gold is refused.

    Three numbers, not fifteen environment variables. A per-run override arrives through the
    workflow's ``additional_data``; a ``quality.gate_threshold`` table for editing without a
    deploy is the P3 answer, once somebody actually needs to change one at three in the
    morning.
    """

    #: Landed rows as a fraction of the previous successful load's, below which the pull is
    #: treated as truncated. 0.5 by default: calibrated against C1's own fixtures, where a
    #: legitimate overlapping re-pull is 4 rows against 6 (0.67, passes) and a file a tenth
    #: the size is 0.17.
    min_landed_ratio: float = 0.5
    #: Fraction of rows whose key or watermark column is missing, blank or uncoercible.
    #: Zero, because the landing contract already says these are never nullable -- a null key
    #: is a contract breach rather than a tolerance question, and those rows are silently
    #: excluded from silver, which is the exclusion this gate exists to make loud.
    max_key_null_rate: float = 0.0
    #: Fraction of child rows naming a parent that does not exist.
    max_orphan_rate: float = 0.0


#: Per-stream defaults. Uniform today; the shape exists because the streams have genuinely
#: different volumes and a single global number would eventually be wrong for one of them.
GATE_DEFAULTS: Mapping[str, StreamThresholds] = MappingProxyType(
    {stream_id: StreamThresholds() for stream_id in LANDING_SCHEMAS}
)


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict on one subject."""

    gate: str
    #: A stream_id, or an edge rendered as 'child.column -> parent.column'.
    subject: str
    passed: bool
    reason: str
    measured: Optional[float] = None
    threshold: Optional[float] = None
    observed: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class LoadVerdict:
    """The aggregate: what the load is allowed to do, and the paragraph explaining it."""

    verdict: str
    reason: str
    results: Tuple[GateResult, ...]

    @property
    def blocked(self) -> bool:
        return self.verdict == "blocked"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "gates": [
                {
                    "gate": r.gate,
                    "subject": r.subject,
                    "passed": r.passed,
                    "measured": r.measured,
                    "threshold": r.threshold,
                    "reason": r.reason,
                }
                for r in self.results
            ],
        }


def resolve_thresholds(
    stream_id: str, overrides: Optional[Mapping[str, Mapping[str, float]]] = None
) -> StreamThresholds:
    """Per-run override, then the stream's default.

    An override naming a stream that does not exist is refused rather than ignored: a typo in
    ``additional_data`` would otherwise silently leave the real threshold in force, which is
    the failure mode of every configuration system that shrugs.
    """
    base = GATE_DEFAULTS.get(stream_id)
    if base is None:
        raise ValueError(f"unknown stream_id {stream_id!r}")
    if not overrides:
        return base
    unknown = set(overrides) - set(GATE_DEFAULTS)
    if unknown:
        known = ", ".join(sorted(GATE_DEFAULTS))
        raise ValueError(
            f"threshold override names unknown stream(s) {sorted(unknown)}. Known: {known}."
        )
    values = overrides.get(stream_id)
    if not values:
        return base
    unknown_keys = set(values) - {
        "min_landed_ratio",
        "max_key_null_rate",
        "max_orphan_rate",
    }
    if unknown_keys:
        raise ValueError(
            f"unknown threshold(s) {sorted(unknown_keys)} for {stream_id!r}"
        )
    return StreamThresholds(
        min_landed_ratio=float(values.get("min_landed_ratio", base.min_landed_ratio)),
        max_key_null_rate=float(
            values.get("max_key_null_rate", base.max_key_null_rate)
        ),
        max_orphan_rate=float(values.get("max_orphan_rate", base.max_orphan_rate)),
    )


#: An edge as ``checks/referential_integrity.sql`` renders it:
#: ``<child stream>.<column> -> <parent stream>.<column>``.
_EDGE_RE = re.compile(
    r"^(?P<child>[a-z_]+\.[a-z_]+)\.(?P<child_column>\w+)"
    r"\s*->\s*(?P<parent>[a-z_]+\.[a-z_]+)\.(?P<parent_column>\w+)$"
)


def edge_child_stream(edge: str) -> str:
    """The child stream an edge string names -- the stream whose threshold governs it.

    Refuses an edge it cannot parse, rather than falling back to a default ``StreamThresholds``.
    A silent fallback here would contradict :func:`resolve_thresholds`, three lines up, which
    refuses an unknown stream precisely so that a typo cannot leave a threshold quietly in
    force; and it would apply that same silence to a *gate*, where the failure mode is a check
    that looks like it ran and did not mean anything.
    """
    match = _EDGE_RE.match(edge.strip())
    if match is None:
        raise ValueError(
            f"edge {edge!r} does not match "
            "'<child stream>.<column> -> <parent stream>.<column>'. It is produced by "
            "checks/referential_integrity.sql, so a mismatch means that file and this parser "
            "have drifted."
        )
    return match.group("child")


def _pct(value: float) -> str:
    """A rate as a percentage a person reads, without trailing noise."""
    return f"{value * 100:g}%"


def render_gate_reason(result_kind: str, **facts: Any) -> str:
    """One sentence, subject first, naming the numbers rather than gesturing at them."""
    if result_kind == "empty_pull":
        return (
            f"This load read no rows at all, across all {facts['streams']} streams, while a "
            "previous load delivered some. An empty pull is a connector, bucket or storage "
            "configuration problem rather than a data one, and it is not a quiet night."
        )
    if result_kind == "not_in_pull":
        return (
            f"{facts['stream_id']} carried no rows in this pull, so there is nothing to "
            "compare and silver is unchanged. Its source may have been read -- CRM lands "
            "three streams in one file -- but a window in which none of this kind changed "
            "is an ordinary incremental pull, not a missing stream."
        )
    if result_kind == "no_baseline":
        return (
            f"{facts['stream_id']} landed {facts['landed']} row(s). No previous successful "
            "load to compare against, so the delta check passed without a baseline."
        )
    if result_kind == "undersized":
        if facts["landed"] == 0:
            # "0 rows from no file" is technically true and reads like a bug report about the
            # gate rather than about the data. A source that was read and carried nothing for
            # one of its streams is a different, clearer statement.
            return (
                f"{facts['stream_id']} carried no rows at all, against {facts['previous']} "
                "in the previous load. Its source was read, so the pull contained no records "
                "of this kind."
            )
        return (
            f"{facts['stream_id']} landed {facts['landed']} row(s) from "
            f"{facts['source']}, {_pct(facts['ratio'])} of the {facts['previous']} row(s) "
            f"the previous load brought for this stream (threshold "
            f"{_pct(facts['threshold'])})."
        )
    if result_kind == "delta_ok":
        return (
            f"{facts['stream_id']} landed {facts['landed']} row(s) against "
            f"{facts['previous']} previously ({_pct(facts['ratio'])}), and silver holds "
            f"{facts['silver']} row(s)."
        )
    if result_kind == "silver_shrank":
        return (
            f"{facts['stream_id']} silver fell from {facts['previous_silver']} to "
            f"{facts['silver']} row(s). Silver cannot shrink under an append-only raw drop "
            "and a full recompute, so this means something other than a short pull is wrong."
        )
    if result_kind == "key_nulls":
        return (
            f"{facts['stream_id']}.{facts['column']} was missing, blank or uncoercible on "
            f"{facts['rows']} of {facts['landed']} landed row(s) "
            f"({_pct(facts['rate'])}, threshold {_pct(facts['threshold'])}). Those rows "
            "carry no usable key and were excluded from silver."
        )
    if result_kind == "keys_clean":
        return (
            f"{facts['stream_id']} had no missing or unusable key columns across "
            f"{facts['landed']} landed row(s)."
        )
    if result_kind == "orphans":
        sample = ", ".join(facts["sample"]) if facts.get("sample") else ""
        tail = f" For example: {sample}." if sample else ""
        return (
            f"{facts['edge']} has {facts['orphans']} orphan(s) of {facts['children']} child "
            f"row(s) ({_pct(facts['rate'])}, threshold {_pct(facts['threshold'])}).{tail}"
        )
    if result_kind == "edge_clean":
        return f"{facts['edge']} resolved for all {facts['children']} child row(s)."
    raise ValueError(f"unknown gate reason kind {result_kind!r}")


def render_load_reason(
    verdict: str,
    results: Sequence[GateResult],
    *,
    acknowledged_shrink: bool = False,
) -> str:
    """The load's whole story in one paragraph.

    This is the artefact the package's evidence bar names: "a reason readable without opening
    a log". It leads with the decision, then the failing gates in the order they were
    measured, then what the state of silver actually is -- because the question a person asks
    after a blocked load is not only "what broke" but "what am I looking at now".
    """
    failures = [r for r in results if not r.passed]
    if verdict == "passed":
        head = f"Passed. {len(results)} check(s) ran and none refused the load."
        if acknowledged_shrink:
            return (
                f"{head} A shrink was explicitly acknowledged for this run "
                "(acknowledge_shrink), so the row-count gate was not allowed to block."
            )
        return head
    if not failures:
        return f"{verdict.capitalize()}. No gate reported a failure."
    body = " ".join(result.reason for result in failures)
    tail = " Gold was not rebuilt; silver still holds what the last passing load conformed."
    return f"Blocked. {body}{tail}"


def _rows(
    engine: Engine, sql: str, params: Mapping[str, Any]
) -> List[Mapping[str, Any]]:
    with engine.begin() as connection:
        result = connection.execute(text(sql), dict(params))
        return [dict(row) for row in result.mappings()]


def evaluate_row_count_delta(
    rows: Sequence[Mapping[str, Any]],
    *,
    overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
    acknowledge_shrink: bool = False,
    acknowledge_undersized: bool = False,
) -> Tuple[GateResult, ...]:
    """The row-count-delta decision, as a pure function of one measurement.

    Split out of :func:`measure_row_count_delta` so it can be tested without a warehouse.
    That is not tidiness: the query and the decision used to be one function, which made this
    gate reachable only through a live database and a cookbook whose assertions were substring
    checks. A scoping defect that fired on every run of that cookbook still passed the whole
    suite green. Everything below is now decided from ``rows`` alone.

    ``rows`` are ``checks/row_count_delta.sql``'s output: one per stream, carrying this load's
    counts, the previous successful load's, and the files this load read for that stream.
    """
    results: List[GateResult] = []
    for row in rows:
        stream_id = str(row["stream_id"])
        thresholds = resolve_thresholds(stream_id, overrides)
        landed = int(row["landed_rows"])
        silver = int(row["silver_rows"])
        previous = row["prev_landed_rows"]
        previous_silver = row["prev_silver_rows"]
        paths = tuple(str(path) for path in (row.get("source_paths") or ()))

        # Was this STREAM part of the pull? Not "was its source read" -- CRM multiplexes
        # contact, deal and deal_stage_history into one file, so an incremental window in
        # which only contacts changed is a perfectly ordinary pull, and source-level scoping
        # reported the other two as having collapsed to zero on every targeted load.
        #
        # `landed_rows` is the loader's count of what the files it read actually carried, so
        # it is also right for an idempotent re-load -- which inserts nothing, and would look
        # like an empty pull to anything that asked landing.raw_row who owns its rows.
        if landed == 0:
            results.append(
                GateResult(
                    gate=GATE_ROW_COUNT_DELTA,
                    subject=stream_id,
                    passed=True,
                    reason=render_gate_reason("not_in_pull", stream_id=stream_id),
                    measured=None,
                    threshold=thresholds.min_landed_ratio,
                    observed={"in_pull": False, "silver_rows": silver},
                )
            )
            continue

        if previous is None:
            results.append(
                GateResult(
                    gate=GATE_ROW_COUNT_DELTA,
                    subject=stream_id,
                    passed=True,
                    reason=render_gate_reason(
                        "no_baseline", stream_id=stream_id, landed=landed
                    ),
                    measured=None,
                    threshold=thresholds.min_landed_ratio,
                    observed={"landed_rows": landed, "silver_rows": silver},
                )
            )
            continue

        previous = int(previous)
        ratio = 1.0 if previous == 0 else landed / previous
        undersized = ratio < thresholds.min_landed_ratio
        shrank = previous_silver is not None and silver < int(previous_silver)

        if shrank and not acknowledge_shrink:
            results.append(
                GateResult(
                    gate=GATE_ROW_COUNT_DELTA,
                    subject=stream_id,
                    passed=False,
                    reason=render_gate_reason(
                        "silver_shrank",
                        stream_id=stream_id,
                        silver=silver,
                        previous_silver=int(previous_silver),
                    ),
                    measured=float(silver),
                    threshold=float(previous_silver),
                    observed=_observed(row),
                )
            )
            continue

        results.append(
            GateResult(
                gate=GATE_ROW_COUNT_DELTA,
                subject=stream_id,
                # `acknowledge_undersized` and not `acknowledge_shrink`: the two waive
                # different checks and the flags used to be one, so acknowledging a shrink
                # also silently disabled the truncated-pull detector -- the one check whose
                # entire job is catching an incomplete pull.
                passed=not undersized or acknowledge_undersized,
                reason=render_gate_reason(
                    "undersized" if undersized else "delta_ok",
                    stream_id=stream_id,
                    landed=landed,
                    previous=previous,
                    ratio=ratio,
                    silver=silver,
                    threshold=thresholds.min_landed_ratio,
                    source=_render_paths(paths),
                ),
                measured=ratio,
                threshold=thresholds.min_landed_ratio,
                observed=_observed(row),
            )
        )

    # Every stream sitting out means the load read nothing at all -- an empty bucket, a
    # misconfigured store, a connector that stopped. Under per-stream scoping each of those
    # rows passes individually, so without this the one input the gate exists to catch is
    # exactly the input that defeats it. Recorded against the load rather than a stream,
    # because no single stream is the thing that went wrong.
    if rows and all(int(row["landed_rows"]) == 0 for row in rows):
        baselines = [row for row in rows if row["prev_landed_rows"] is not None]
        if baselines:
            results.append(
                GateResult(
                    gate=GATE_ROW_COUNT_DELTA,
                    subject=SUBJECT_WHOLE_LOAD,
                    passed=False,
                    reason=render_gate_reason("empty_pull", streams=len(rows)),
                    measured=0.0,
                    threshold=None,
                    observed={"landed_rows": 0, "streams": len(rows)},
                )
            )
    return tuple(results)


def _observed(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The measurement row, with the path list rendered for JSON storage."""
    observed = dict(row)
    paths = observed.get("source_paths")
    if paths is not None:
        observed["source_paths"] = [str(path) for path in paths]
    return observed


def _render_paths(paths: Sequence[str]) -> str:
    """The files a stream's rows came from, for the reason string.

    A reason naming a count but not a file sends the reader to a log to find out which file,
    which is the thing the evidence bar says must not be necessary.
    """
    if not paths:
        return "no file"
    if len(paths) == 1:
        return paths[0]
    return f"{paths[0]} and {len(paths) - 1} other file(s)"


def measure_row_count_delta(
    engine: Engine,
    load_id: uuid.UUID,
    previous_load_id: Optional[uuid.UUID],
    *,
    overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
    acknowledge_shrink: bool = False,
    acknowledge_undersized: bool = False,
) -> Tuple[GateResult, ...]:
    """Landed-row ratio against the previous successful load, plus a silver-shrink check.

    One query, then :func:`evaluate_row_count_delta`. Nothing is decided here.
    """
    rows = _rows(
        engine,
        check_sql(GATE_ROW_COUNT_DELTA),
        {
            "load_id": str(load_id),
            "previous_load_id": str(previous_load_id) if previous_load_id else None,
        },
    )
    return evaluate_row_count_delta(
        rows,
        overrides=overrides,
        acknowledge_shrink=acknowledge_shrink,
        acknowledge_undersized=acknowledge_undersized,
    )


def measure_key_null_rate(
    engine: Engine,
    load_id: uuid.UUID,
    *,
    overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Tuple[GateResult, ...]:
    """Missing, blank or uncoercible key and watermark columns, per stream."""
    rows = _rows(engine, check_sql(GATE_KEY_NULL_RATE), {"load_id": str(load_id)})
    structural = [row for row in rows if bool(row["structural"])]
    results: List[GateResult] = []
    seen = set()
    for row in structural:
        stream_id = str(row["stream_id"])
        thresholds = resolve_thresholds(stream_id, overrides)
        rate = float(row["defect_rate"])
        seen.add(stream_id)
        results.append(
            GateResult(
                gate=GATE_KEY_NULL_RATE,
                subject=f"{stream_id}.{row['column_name']}",
                passed=rate <= thresholds.max_key_null_rate,
                reason=render_gate_reason(
                    "key_nulls",
                    stream_id=stream_id,
                    column=row["column_name"],
                    rows=int(row["defect_rows"]),
                    landed=int(row["landed_rows"]),
                    rate=rate,
                    threshold=thresholds.max_key_null_rate,
                ),
                measured=rate,
                threshold=thresholds.max_key_null_rate,
                observed=dict(row),
            )
        )
    # A stream with no structural defects records a pass explicitly. A gate that says nothing
    # when all is well is indistinguishable from a gate that did not run.
    for stream_id, landed in _landed_by_stream(engine, load_id).items():
        if stream_id in seen:
            continue
        thresholds = resolve_thresholds(stream_id, overrides)
        results.append(
            GateResult(
                gate=GATE_KEY_NULL_RATE,
                subject=stream_id,
                passed=True,
                reason=render_gate_reason(
                    "keys_clean", stream_id=stream_id, landed=landed
                ),
                measured=0.0,
                threshold=thresholds.max_key_null_rate,
                observed={"landed_rows": landed},
            )
        )
    return tuple(results)


def _landed_by_stream(engine: Engine, load_id: uuid.UUID) -> Dict[str, int]:
    rows = _rows(
        engine,
        "SELECT stream_id, landed_rows FROM quality.load_stream_count "
        "WHERE load_id = CAST(:load_id AS uuid)",
        {"load_id": str(load_id)},
    )
    return {str(row["stream_id"]): int(row["landed_rows"]) for row in rows}


def measure_referential_integrity(
    engine: Engine,
    *,
    overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Tuple[GateResult, ...]:
    """Orphans per declared edge, over silver as it now stands.

    Not scoped to one load: an orphan is a property of the whole entity set, and the parent
    a child names may have arrived on any earlier load.
    """
    rows = _rows(engine, check_sql(GATE_REFERENTIAL_INTEGRITY), {})
    # Edges are cross-stream, so there is no single stream whose override applies. The child
    # stream owns the threshold, and the edge string names it first.
    results: List[GateResult] = []
    for row in rows:
        edge = str(row["edge"])
        thresholds = resolve_thresholds(edge_child_stream(edge), overrides)
        rate = float(row["orphan_rate"])
        orphans = int(row["orphan_rows"])
        sample = [str(v) for v in (row.get("sample_orphans") or [])]
        results.append(
            GateResult(
                gate=GATE_REFERENTIAL_INTEGRITY,
                subject=edge,
                passed=rate <= thresholds.max_orphan_rate,
                reason=render_gate_reason(
                    "orphans" if orphans else "edge_clean",
                    edge=edge,
                    orphans=orphans,
                    children=int(row["child_rows"]),
                    rate=rate,
                    threshold=thresholds.max_orphan_rate,
                    sample=sample,
                ),
                measured=rate,
                threshold=thresholds.max_orphan_rate,
                observed={k: v for k, v in row.items() if k != "sample_orphans"},
            )
        )
    return tuple(results)


def record_gate_results(
    engine: Engine, load_id: uuid.UUID, results: Sequence[GateResult]
) -> None:
    """Upsert, so a retried step replaces its verdict instead of doubling the rows."""
    if not results:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO quality.gate_result "
                "(load_id, gate, subject, measured, threshold, observed, passed, reason) "
                "VALUES (:load_id, :gate, :subject, :measured, :threshold, "
                "        CAST(:observed AS jsonb), :passed, :reason) "
                "ON CONFLICT (load_id, gate, subject) DO UPDATE "
                "SET measured = EXCLUDED.measured, threshold = EXCLUDED.threshold, "
                "    observed = EXCLUDED.observed, passed = EXCLUDED.passed, "
                "    reason = EXCLUDED.reason, checked_at = now()"
            ),
            [
                {
                    "load_id": str(load_id),
                    "gate": r.gate,
                    "subject": r.subject,
                    "measured": r.measured,
                    "threshold": r.threshold,
                    "observed": json.dumps(dict(r.observed), default=str),
                    "passed": r.passed,
                    "reason": r.reason,
                }
                for r in results
            ],
        )


async def arecord_gate_results(
    engine: Engine, load_id: uuid.UUID, results: Sequence[GateResult]
) -> None:
    await asyncio.to_thread(record_gate_results, engine, load_id, results)


def read_gate_results(engine: Engine, load_id: uuid.UUID) -> Tuple[GateResult, ...]:
    """Every gate result recorded for a load, failures first."""
    rows = _rows(
        engine,
        "SELECT gate, subject, measured, threshold, observed, passed, reason "
        "FROM quality.gate_result WHERE load_id = CAST(:load_id AS uuid) "
        "ORDER BY passed, gate, subject",
        {"load_id": str(load_id)},
    )
    return tuple(
        GateResult(
            gate=str(row["gate"]),
            subject=str(row["subject"]),
            passed=bool(row["passed"]),
            reason=str(row["reason"]),
            measured=None if row["measured"] is None else float(row["measured"]),
            threshold=None if row["threshold"] is None else float(row["threshold"]),
            observed=dict(row["observed"] or {}),
        )
        for row in rows
    )


def aggregate_verdict(
    results: Sequence[GateResult], *, acknowledged_shrink: bool = False
) -> LoadVerdict:
    """Any failure blocks. Pure, so the decision is testable without a database."""
    verdict = "blocked" if any(not r.passed for r in results) else "passed"
    return LoadVerdict(
        verdict=verdict,
        reason=render_load_reason(
            verdict, results, acknowledged_shrink=acknowledged_shrink
        ),
        results=tuple(results),
    )
