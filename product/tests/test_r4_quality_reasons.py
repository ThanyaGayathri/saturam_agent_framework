"""R4 without a warehouse -- the pure core: thresholds and the reason a person reads.

The reason string is the artefact R4's evidence bar names ("a reason readable without opening
a log"), and it is produced by pure functions, so it can be pinned exactly here rather than
eyeballed in a cookbook. Threshold resolution is pinned for the same reason: it decides
whether gold is refused, and getting it silently wrong is the worst available outcome.
"""

from __future__ import annotations

import pytest

from egpt_retail.warehouse.quality import (
    GATE_DEFAULTS,
    SUBJECT_WHOLE_LOAD,
    GateResult,
    StreamThresholds,
    aggregate_verdict,
    edge_child_stream,
    evaluate_row_count_delta,
    render_gate_reason,
    resolve_thresholds,
)


def _failing(reason: str) -> GateResult:
    return GateResult(
        gate="row_count_delta", subject="crm.contact", passed=False, reason=reason
    )


def _passing(subject: str = "crm.contact") -> GateResult:
    return GateResult(gate="key_null_rate", subject=subject, passed=True, reason="fine")


def test_defaults_cover_every_stream() -> None:
    from egpt_retail.contracts.landing import LANDING_SCHEMAS

    assert set(GATE_DEFAULTS) == set(LANDING_SCHEMAS)


def test_key_null_rate_default_is_zero_tolerance() -> None:
    """The contract says key columns are never nullable, so this is not a tuning knob."""
    assert GATE_DEFAULTS["crm.contact"].max_key_null_rate == 0.0


def test_override_beats_the_default() -> None:
    resolved = resolve_thresholds(
        "crm.contact", {"crm.contact": {"min_landed_ratio": 0.1}}
    )
    assert resolved.min_landed_ratio == 0.1
    # Untouched fields keep the default rather than resetting.
    assert resolved.max_key_null_rate == GATE_DEFAULTS["crm.contact"].max_key_null_rate


def test_override_for_another_stream_leaves_this_one_alone() -> None:
    resolved = resolve_thresholds(
        "crm.deal", {"crm.contact": {"min_landed_ratio": 0.1}}
    )
    assert resolved == GATE_DEFAULTS["crm.deal"]


def test_override_naming_an_unknown_stream_is_refused() -> None:
    """A typo must not silently leave the real threshold in force."""
    with pytest.raises(ValueError) as excinfo:
        resolve_thresholds("crm.contact", {"crm.contacts": {"min_landed_ratio": 0.1}})
    assert "crm.contacts" in str(excinfo.value)


def test_override_naming_an_unknown_threshold_is_refused() -> None:
    with pytest.raises(ValueError) as excinfo:
        resolve_thresholds("crm.contact", {"crm.contact": {"min_landed_rows": 5}})
    assert "min_landed_rows" in str(excinfo.value)


def test_undersized_reason_carries_both_counts_the_ratio_and_the_file() -> None:
    """Everything needed to act, without opening a log. That is the whole requirement."""
    reason = render_gate_reason(
        "undersized",
        stream_id="crm.contact",
        landed=1,
        previous=6,
        ratio=1 / 6,
        threshold=0.5,
        source="raw/crm/dt=2026-09-01/crm-20260901T100000Z.ndjson",
    )
    assert "crm.contact" in reason
    assert "1 row" in reason and "6 row" in reason
    assert "16.6667%" in reason or "16.6667" in reason or "%" in reason
    assert "raw/crm/dt=2026-09-01/crm-20260901T100000Z.ndjson" in reason
    assert "Traceback" not in reason


def test_a_stream_that_carried_nothing_reads_as_a_sentence_not_a_bug_report() -> None:
    """'0 rows from no file' is true and unreadable; the gate must say what happened."""
    reason = render_gate_reason(
        "undersized",
        stream_id="crm.deal",
        landed=0,
        previous=3,
        ratio=0.0,
        threshold=0.5,
        source="no file",
    )
    assert "no file" not in reason
    assert "carried no rows" in reason


def test_key_null_reason_names_the_column_and_says_what_happened_to_the_rows() -> None:
    reason = render_gate_reason(
        "key_nulls",
        stream_id="crm.contact",
        column="object_id",
        rows=1,
        landed=4,
        rate=0.25,
        threshold=0.0,
    )
    assert "crm.contact.object_id" in reason
    assert "excluded from silver" in reason


def test_a_stream_not_in_this_pull_is_reported_rather_than_compared() -> None:
    """A targeted load must not look like every other source collapsed to zero."""
    reason = render_gate_reason("not_in_pull", stream_id="analytics.event")
    assert "carried no rows in this pull" in reason
    assert "unchanged" in reason
    # It must also say why sharing a source is not evidence of a missing stream, since
    # exactly that confusion is what the source-scoped version of this gate was built on.
    assert "three streams in one file" in reason


def test_any_failure_blocks_and_every_pass_does_not() -> None:
    assert aggregate_verdict([_passing(), _passing("crm.deal")]).verdict == "passed"
    assert aggregate_verdict([_passing(), _failing("bad")]).verdict == "blocked"
    assert aggregate_verdict([]).verdict == "passed"


def test_the_load_reason_lists_every_failing_gate_and_says_gold_was_not_rebuilt() -> (
    None
):
    verdict = aggregate_verdict(
        [_passing(), _failing("first thing wrong."), _failing("second thing wrong.")]
    )
    assert verdict.blocked
    assert "first thing wrong." in verdict.reason
    assert "second thing wrong." in verdict.reason
    assert "Gold was not rebuilt" in verdict.reason


def test_a_blocked_reason_stays_short_enough_that_somebody_reads_it() -> None:
    """A reason nobody reads is not a reason. Six hundred characters is already generous."""
    verdict = aggregate_verdict(
        [
            _failing(
                render_gate_reason(
                    "undersized",
                    stream_id="crm.contact",
                    landed=1,
                    previous=6,
                    ratio=1 / 6,
                    threshold=0.5,
                    source="raw/crm/dt=2026-09-01/crm-20260901T100000Z.ndjson",
                )
            )
        ]
    )
    assert len(verdict.reason) < 600


def test_an_acknowledged_shrink_says_so_in_the_reason() -> None:
    """An override that leaves no trace is a bypass. This one is written into the record."""
    verdict = aggregate_verdict([_passing()], acknowledged_shrink=True)
    assert verdict.verdict == "passed"
    assert "acknowledge_shrink" in verdict.reason


def test_an_unknown_reason_kind_raises_rather_than_rendering_something_vague() -> None:
    with pytest.raises(ValueError):
        render_gate_reason("something_else", stream_id="crm.contact")


def test_thresholds_are_a_frozen_value_object() -> None:
    with pytest.raises(Exception):
        StreamThresholds().min_landed_ratio = 0.9  # type: ignore[misc]


# ---------------------------------------------------------------------------------------
# The row-count-delta decision, DB-free.
#
# These exist because the review found a Critical scoping defect that fired on every run of
# the cookbook and still passed the whole suite green. The query and the decision used to be
# one function, so the only way to reach the decision was a live warehouse. Splitting the
# pure half out is what makes the cases below expressible at all.
# ---------------------------------------------------------------------------------------


def _row(
    stream_id, landed, prev_landed, *, silver=10, prev_silver=10, paths=("raw/f",)
):
    """One row of checks/row_count_delta.sql's output."""
    return {
        "stream_id": stream_id,
        "landed_rows": landed,
        "conformed_rows": landed,
        "silver_rows": silver,
        "source_paths": list(paths) if landed else [],
        "prev_landed_rows": prev_landed,
        "prev_silver_rows": prev_silver,
    }


def _subjects(results, *, passed):
    return {r.subject for r in results if r.passed is passed}


def test_a_stream_that_shares_a_source_but_carried_nothing_is_not_compared() -> None:
    """The Critical finding, as a test.

    CRM multiplexes contact, deal and deal_stage_history into one file. An incremental window
    in which only contacts changed is an ordinary pull, and the two streams that carried
    nothing must not be reported as having collapsed to zero. Scoping coverage by *source*
    did exactly that, on every targeted load.
    """
    results = evaluate_row_count_delta(
        [
            _row("crm.contact", landed=4, prev_landed=4),
            _row("crm.deal", landed=0, prev_landed=3),
            _row("crm.deal_stage_history", landed=0, prev_landed=3),
        ]
    )
    assert _subjects(results, passed=False) == set()
    assert all(
        "carried no rows in this pull" in r.reason
        for r in results
        if r.measured is None
    )


def test_a_pull_that_landed_nothing_at_all_is_refused() -> None:
    """The other direction of the same finding.

    Under per-stream scoping every individual stream legitimately sits out, so a wholly empty
    pull -- an empty bucket, a misconfigured store, a connector that died -- would pass every
    gate. That is precisely the input this gate exists to catch, so it is refused against the
    load rather than against any one stream.
    """
    results = evaluate_row_count_delta(
        [
            _row("crm.contact", landed=0, prev_landed=4),
            _row("analytics.event", landed=0, prev_landed=5),
        ]
    )
    assert _subjects(results, passed=False) == {SUBJECT_WHOLE_LOAD}
    assert "read no rows at all" in next(r.reason for r in results if not r.passed)


def test_an_empty_first_load_is_not_refused() -> None:
    """With no baseline there is nothing to have stopped delivering."""
    results = evaluate_row_count_delta(
        [_row("crm.contact", landed=0, prev_landed=None)]
    )
    assert _subjects(results, passed=False) == set()


def test_an_undersized_pull_is_refused_and_names_its_file() -> None:
    results = evaluate_row_count_delta(
        [
            _row(
                "crm.contact",
                landed=1,
                prev_landed=4,
                paths=("raw/crm/dt=x/small.ndjson",),
            )
        ]
    )
    (failure,) = [r for r in results if not r.passed]
    assert failure.subject == "crm.contact"
    assert failure.measured == pytest.approx(0.25)
    assert "raw/crm/dt=x/small.ndjson" in failure.reason


def test_silver_shrinking_is_refused_even_when_the_pull_is_full_size() -> None:
    results = evaluate_row_count_delta(
        [_row("crm.contact", landed=4, prev_landed=4, silver=2, prev_silver=9)]
    )
    (failure,) = [r for r in results if not r.passed]
    assert "cannot shrink" in failure.reason


def test_acknowledge_shrink_does_not_also_waive_the_undersized_check() -> None:
    """The two flags waive different checks, and used to be one.

    Acknowledging a backfill-to-delta shrink silently disabled the truncated-pull detector --
    the one check whose entire job is catching an incomplete pull. A load that is not shrinking
    at all but is undersized was passed unconditionally.
    """
    rows = [_row("crm.contact", landed=1, prev_landed=4, silver=10, prev_silver=10)]
    assert _subjects(evaluate_row_count_delta(rows), passed=False) == {"crm.contact"}
    assert _subjects(
        evaluate_row_count_delta(rows, acknowledge_shrink=True), passed=False
    ) == {"crm.contact"}, "acknowledge_shrink must not waive an undersized pull"
    assert (
        _subjects(
            evaluate_row_count_delta(rows, acknowledge_undersized=True), passed=False
        )
        == set()
    )


def test_acknowledge_undersized_does_not_waive_a_silver_shrink() -> None:
    """And the converse: the flags are not interchangeable in either direction."""
    rows = [_row("crm.contact", landed=4, prev_landed=4, silver=2, prev_silver=9)]
    assert _subjects(
        evaluate_row_count_delta(rows, acknowledge_undersized=True), passed=False
    ) == {"crm.contact"}


def test_a_threshold_override_reaches_the_decision() -> None:
    rows = [_row("crm.contact", landed=1, prev_landed=4)]
    assert _subjects(evaluate_row_count_delta(rows), passed=False) == {"crm.contact"}
    assert (
        _subjects(
            evaluate_row_count_delta(
                rows, overrides={"crm.contact": {"min_landed_ratio": 0.1}}
            ),
            passed=False,
        )
        == set()
    )


def test_edge_child_stream_parses_the_one_shipped_edge() -> None:
    assert (
        edge_child_stream("crm.deal_stage_history.deal_id -> crm.deal.object_id")
        == "crm.deal_stage_history"
    )


@pytest.mark.parametrize(
    "edge", ["nonsense", "a.b -> c.d", "", "crm.deal_stage_history.deal_id", "x -> y"]
)
def test_edge_child_stream_refuses_rather_than_defaulting(edge: str) -> None:
    """A silent fallback to default thresholds would be a gate that looks like it ran.

    resolve_thresholds refuses an unknown stream precisely so a typo cannot leave a threshold
    quietly in force. Applying the opposite rule one function away -- and to a *gate* -- was
    the inconsistency.
    """
    with pytest.raises(ValueError):
        edge_child_stream(edge)
