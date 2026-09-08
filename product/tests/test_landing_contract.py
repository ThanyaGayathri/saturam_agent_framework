"""Unit tests for the landing contract (C1) -- egpt_retail.contracts.landing.

Written in response to round-1 automated review on PR #190. Every construction-time guard
in `LandingSchema.__post_init__` was correct but unexercised anywhere in the repository
(review finding #7); the fixes for the three "Major" findings (naive-datetime handling,
non-scalar key values, non-object rows) had nothing pinning them against regression either.
This file is that pin, plus adversarial coverage for `parse_landing_path` (finding #11) that
the cookbook script's own fixtures -- which only ever produce well-formed paths -- cannot
provide.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from egpt_retail.contracts.landing import (
    LANDING_SCHEMAS,
    LandingSchema,
    SourceName,
    landing_path,
    parse_landing_path,
    resolve_stream_id,
    validate_row,
)

# --- LandingSchema.__post_init__'s self-validation guards (finding #7) ---------------------


def test_schema_rejects_empty_stream_id():
    with pytest.raises(ValueError, match="stream_id must not be empty"):
        LandingSchema(
            stream_id="",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a",),
            watermark_column="a",
            required_columns=("a",),
        )


def test_schema_rejects_empty_key_columns():
    with pytest.raises(ValueError, match="key_columns must not be empty"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=(),
            watermark_column="a",
            required_columns=("a",),
        )


def test_schema_rejects_key_column_not_in_required():
    with pytest.raises(ValueError, match="not listed in"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a", "b"),
            watermark_column="a",
            required_columns=("a",),
        )


def test_schema_rejects_watermark_not_in_required():
    with pytest.raises(ValueError, match="watermark_column"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a",),
            watermark_column="b",
            required_columns=("a",),
        )


def test_schema_rejects_nullable_key_or_watermark():
    with pytest.raises(ValueError, match="must never be null"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a",),
            watermark_column="a",
            required_columns=("a",),
            nullable_columns=frozenset({"a"}),
        )


def test_schema_rejects_nullable_column_not_required():
    with pytest.raises(ValueError, match="not required_columns"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a",),
            watermark_column="a",
            required_columns=("a",),
            nullable_columns=frozenset({"b"}),
        )


def test_schema_rejects_timestamp_column_not_required():
    with pytest.raises(ValueError, match="timestamp_columns"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a",),
            watermark_column="a",
            required_columns=("a",),
            timestamp_columns=frozenset({"b"}),
        )


def test_schema_rejects_watermark_in_timestamp_columns():
    with pytest.raises(ValueError, match="already format-checked"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a",),
            watermark_column="a",
            required_columns=("a", "b"),
            timestamp_columns=frozenset({"a"}),
        )


def test_schema_rejects_superseded_watermark_equal_to_watermark():
    with pytest.raises(ValueError, match="must differ from watermark_column"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a",),
            watermark_column="a",
            required_columns=("a",),
            superseded_watermark_column="a",
        )


def test_schema_rejects_superseded_watermark_not_required():
    with pytest.raises(ValueError, match="not listed in required_columns"):
        LandingSchema(
            stream_id="x",
            source=SourceName.ANALYTICS,
            grain="x",
            key_columns=("a",),
            watermark_column="a",
            required_columns=("a",),
            superseded_watermark_column="b",
        )


def test_landing_schemas_is_immutable():
    """Finding #8: the "frozen at import time" claim is now a runtime guarantee."""
    with pytest.raises(TypeError):
        LANDING_SCHEMAS["evil"] = None  # type: ignore[index]


def test_session_replay_watermark_is_last_updated_not_session_start():
    """Post-merge Lane R finding: `session_start_at` never moves once a session begins, so
    using it as the watermark means a re-pull carrying corrected click counts ties forever
    and the tie-break silently discards the correction. `last_updated_at` is the watermark
    instead -- `session_start_at` stays required, just no longer structural."""
    schema = LANDING_SCHEMAS["session_replay.session"]
    assert schema.watermark_column == "last_updated_at"
    assert "session_start_at" in schema.required_columns
    assert "session_start_at" not in schema.key_columns


def test_validate_row_accepts_both_pulls_of_a_corrected_session_replay_row():
    """Schema-level pin for the case the watermark change fixes, not a dedup demonstration.

    Round-1 review [major]: naming this test "...wins on last_updated_at" overclaimed --
    it only ever called `validate_row` and compared two Python string literals, never any
    winner-selection logic. No last-write-wins implementation exists for `session_replay`
    anywhere in this repo (the cookbook's dedup hardcodes `last_modified_at` and never
    touches `session_replay`); that end-to-end demonstration belongs to Lane R's dedup
    consumer (#193, R4), which is what actually reads `schema.watermark_column`.

    What this test does pin, and what a dedup implementation built against this contract can
    rely on: two pulls of the same session with the same `session_start_at` (it never moves)
    and different `last_updated_at` both validate cleanly, and the corrected pull's
    `last_updated_at` sorts later -- the two preconditions last-write-wins-by-watermark needs
    to work at all once it exists.
    """
    first_pull = {
        "session_id": "sess_1",
        "session_start_at": "2026-09-01T08:00:00Z",
        "last_updated_at": "2026-09-01T08:05:00Z",
        "rage_click_count": 0,
        "dead_click_count": 0,
        "script_error_count": 0,
        "session_url": "https://shop.example.com/quote",
        "identity_at_login": None,
        "identity_at_submit": None,
        "identity_key": None,
    }
    second_pull = {
        **first_pull,
        "last_updated_at": "2026-09-01T08:20:00Z",
        "rage_click_count": 3,
    }
    assert validate_row("session_replay.session", first_pull) == ()
    assert validate_row("session_replay.session", second_pull) == ()
    assert second_pull["last_updated_at"] > first_pull["last_updated_at"]


@pytest.mark.parametrize(
    "stream_id, row, timestamp_column",
    [
        (
            "session_replay.session",
            {
                "session_id": "sess_1",
                "session_start_at": "not-a-real-timestamp",
                "last_updated_at": "2026-09-01T08:05:00Z",
                "rage_click_count": 0,
                "dead_click_count": 0,
                "script_error_count": 0,
                "session_url": "https://shop.example.com/quote",
                "identity_at_login": None,
                "identity_at_submit": None,
                "identity_key": None,
            },
            "session_start_at",
        ),
        (
            "crm.deal_stage_history",
            {
                "record_type": "deal_stage_history",
                "object_id": "h_1",
                "last_modified_at": "2026-09-01T07:00:00Z",
                "deal_id": "d_1",
                "stage": "quote_requested",
                "entered_at": "not-a-real-timestamp",
            },
            "entered_at",
        ),
    ],
)
def test_validate_row_rejects_malformed_non_watermark_timestamp_column(
    stream_id, row, timestamp_column
):
    """Round-1 review [major]: a required column stops being format-checked the moment it
    stops being the watermark -- `_parses_as_timestamp` was only ever called on
    `schema.watermark_column`. Before this PR, `session_start_at` was the watermark and so
    was checked; after the 1.1.0 switch to `last_updated_at` it silently lost that check.
    `LandingSchema.timestamp_columns` restores it for any required column still known to
    hold a timestamp, watermark or not."""
    defects = validate_row(stream_id, row)
    assert any(
        timestamp_column in d and "not a parseable UTC ISO-8601 timestamp" in d
        for d in defects
    )


def test_validate_row_distinguishes_pre_1_1_0_session_replay_row_from_malformed():
    """Round-1 review [major]: re-validating a row landed under the pre-1.1.0 contract (has
    `session_start_at`, genuinely lacks `last_updated_at` since that column didn't exist yet)
    reported the same generic defect as truly malformed 1.1.0 data -- indistinguishable.
    `LandingSchema.superseded_watermark_column` lets `validate_row` tell them apart: a blank
    current watermark alongside a well-formed old one reads as "predates the switch," not
    "malformed."""
    pre_1_1_0_row = {
        "session_id": "sess_1",
        "session_start_at": "2026-09-01T08:00:00Z",
        # no last_updated_at at all -- this column didn't exist before 1.1.0
        "rage_click_count": 0,
        "dead_click_count": 0,
        "script_error_count": 0,
        "session_url": "https://shop.example.com/quote",
        "identity_at_login": None,
        "identity_at_submit": None,
        "identity_key": None,
    }
    defects = validate_row("session_replay.session", pre_1_1_0_row)
    assert any(
        "last_updated_at" in d and "may predate the switch" in d for d in defects
    )
    assert not any("is missing or null" in d for d in defects)


def test_validate_row_reports_generic_missing_watermark_when_no_superseded_value():
    """The distinguishing message from the test above must not fire when the old watermark
    is *also* missing or malformed -- that really is just a missing watermark, not a
    recognizable pre-1.1.0 shape."""
    truly_malformed_row = {
        "session_id": "sess_1",
        "session_start_at": None,
        "rage_click_count": 0,
        "dead_click_count": 0,
        "script_error_count": 0,
        "session_url": "https://shop.example.com/quote",
        "identity_at_login": None,
        "identity_at_submit": None,
        "identity_key": None,
    }
    defects = validate_row("session_replay.session", truly_malformed_row)
    assert any(
        d == "watermark column 'last_updated_at' is missing or null" for d in defects
    )


# --- validate_row: the three Major findings, plus the blank-string Minor -------------------


def test_validate_row_rejects_non_mapping_row():
    """Finding #3: a legal-NDJSON, illegal-per-contract row is reported, not crashed on."""
    for bad_row in ("not-an-object", ["a", "b"], 12345, None):
        defects = validate_row("analytics.event", bad_row)  # type: ignore[arg-type]
        assert len(defects) == 1
        assert "not a JSON object" in defects[0]


@pytest.mark.parametrize("bad_value", [["c_500", "dup"], {"nested": "dict"}])
def test_validate_row_rejects_non_scalar_key_value(bad_value):
    """Finding #2: a list/dict key value is reported instead of passing clean and later
    crashing a caller's dedup (which groups rows by a tuple of key values)."""
    row = {
        "record_type": "contact",
        "object_id": bad_value,
        "last_modified_at": "2026-09-01T07:00:00Z",
        "email": "a@example.com",
        "brand": "Blodgett",
        "identity_key": None,
    }
    defects = validate_row("crm.contact", row)
    assert any("must be a string" in d for d in defects)
    # And the reported value really would be unhashable, confirming the finding's premise.
    with pytest.raises(TypeError):
        hash(bad_value)


@pytest.mark.parametrize("bad_value", [True, 12345, 3.14])
def test_validate_row_rejects_non_string_key_value(bad_value):
    """Round-2 finding: `isinstance(value, (str, int, float, bool))` was looser than the
    code comment claimed ("every current key column is string-typed") -- a bool, int or
    float key value passed clean. These remain hashable, so this isn't a crash risk like the
    list/dict case above; it's a data-shape gap against the contract's own stated intent."""
    row = {
        "record_type": "contact",
        "object_id": bad_value,
        "last_modified_at": "2026-09-01T07:00:00Z",
        "email": "a@example.com",
        "brand": "Blodgett",
        "identity_key": None,
    }
    defects = validate_row("crm.contact", row)
    assert any("must be a string" in d for d in defects)


def test_validate_row_treats_blank_string_like_null():
    """Finding #6: only checking `is None` let an empty string paper over known-dirty data."""
    row = {
        "record_type": "contact",
        "object_id": "c_999",
        "last_modified_at": "2026-09-01T07:00:00Z",
        "email": "a@example.com",
        "brand": "   ",
        "identity_key": None,
    }
    defects = validate_row("crm.contact", row)
    assert any("brand" in d and "null or blank" in d for d in defects)


def test_validate_row_rejects_blank_key_value():
    """Round-2 finding: the key-column loop only checked `is None`, so a whitespace-only
    key value passed clean even though `_is_blank` already caught this for non-key required
    columns -- and a key value is the one thing the docstring calls unconditional."""
    row = {
        "record_type": "contact",
        "object_id": "   ",
        "last_modified_at": "2026-09-01T07:00:00Z",
        "email": "a@example.com",
        "brand": "Blodgett",
        "identity_key": None,
    }
    defects = validate_row("crm.contact", row)
    assert any("object_id" in d and "missing or null" in d for d in defects)


def test_validate_row_does_not_double_report_blank_key_and_watermark_column():
    """A blank (not just null) value in a combined key+watermark column must not double-
    report either -- the same guard that covers `None` has to cover `_is_blank`."""
    row = {
        "user_id": "u_1",
        "session_id": "s_1",
        "event_name": "product_view",
        "occurred_at": "   ",
        "brand": "Blodgett",
        "product_id": "p_1",
        "identity_key": None,
    }
    defects = validate_row("analytics.event", row)
    occurred_at_defects = [d for d in defects if "occurred_at" in d]
    assert len(occurred_at_defects) == 1


def test_validate_row_does_not_double_report_key_and_watermark_column():
    """Finding #4: analytics.event's occurred_at is both a key and the watermark."""
    row = {
        "user_id": "u_1",
        "session_id": "s_1",
        "event_name": "product_view",
        "occurred_at": None,
        "brand": "Blodgett",
        "product_id": "p_1",
        "identity_key": None,
    }
    defects = validate_row("analytics.event", row)
    occurred_at_defects = [d for d in defects if "occurred_at" in d]
    assert len(occurred_at_defects) == 1


def test_validate_row_does_not_double_report_non_scalar_key_and_watermark_column():
    """Round-2 finding: the double-report guard above only special-cased blank, not the
    non-scalar case the key-column loop also flags -- a list/dict value in a combined
    key+watermark column still produced two defects for one root cause."""
    row = {
        "user_id": "u_1",
        "session_id": "s_1",
        "event_name": "product_view",
        "occurred_at": ["x"],
        "brand": "Blodgett",
        "product_id": "p_1",
        "identity_key": None,
    }
    defects = validate_row("analytics.event", row)
    occurred_at_defects = [d for d in defects if "occurred_at" in d]
    assert len(occurred_at_defects) == 1
    assert "must be a string" in occurred_at_defects[0]


def test_validate_row_still_checks_watermark_parseability_when_also_a_key():
    """The fix for finding #4 must not swallow a genuine parse failure on a combined
    key+watermark column -- only the redundant *null* report is suppressed."""
    row = {
        "user_id": "u_1",
        "session_id": "s_1",
        "event_name": "product_view",
        "occurred_at": "not-a-timestamp",
        "brand": "Blodgett",
        "product_id": "p_1",
        "identity_key": None,
    }
    defects = validate_row("analytics.event", row)
    assert any("not a parseable UTC ISO-8601 timestamp" in d for d in defects)


def test_validate_row_rejects_naive_watermark_timestamp():
    """Round-2 [high] finding: a naive timestamp sorts lexically wrong against a UTC one,
    silently breaking last-write-wins ordering -- the row-watermark counterpart to the naive
    `ingested_at` fix already applied to `landing_path`."""
    row = {
        "record_type": "contact",
        "object_id": "c_1",
        "last_modified_at": "2026-09-01T07:00:00",  # no trailing Z
        "email": "a@example.com",
        "brand": "Blodgett",
        "identity_key": None,
    }
    defects = validate_row("crm.contact", row)
    assert any("last_modified_at" in d and "UTC ISO-8601" in d for d in defects)


def test_validate_row_rejects_non_utc_offset_watermark_timestamp():
    """Same finding: a non-UTC offset is a real instant but sorts lexically wrong against a
    `Z`-suffixed one -- `"09:00:00+02:00" > "07:30:00Z"` as strings even though 09:00+02:00
    is 07:00Z, chronologically earlier."""
    row = {
        "record_type": "contact",
        "object_id": "c_1",
        "last_modified_at": "2026-09-01T09:00:00+02:00",
        "email": "a@example.com",
        "brand": "Blodgett",
        "identity_key": None,
    }
    defects = validate_row("crm.contact", row)
    assert any("last_modified_at" in d and "UTC ISO-8601" in d for d in defects)


def test_non_utc_offset_timestamp_sorts_lexically_wrong_against_a_z_suffixed_one():
    """Round-1 review [nit]: the two tests above pin that a defect is reported, but not the
    actual lexical-mis-sort fact that motivates the fix. This demonstrates it directly: a
    `+02:00` timestamp can be chronologically earlier than a `Z` one yet string-sort later,
    which is exactly what would silently corrupt last-write-wins ordering if `validate_row`
    accepted both formats side by side."""
    earlier_non_utc = "2026-09-01T09:00:00+02:00"  # = 2026-09-01T07:00:00Z
    later_utc = "2026-09-01T07:30:00Z"
    assert earlier_non_utc > later_utc  # wrong as strings
    assert datetime.fromisoformat(earlier_non_utc) < datetime.fromisoformat(
        later_utc.removesuffix("Z") + "+00:00"
    )  # correct as instants


def test_validate_row_accepts_a_conforming_row():
    row = {
        "record_type": "contact",
        "object_id": "c_500",
        "last_modified_at": "2026-09-01T07:00:00Z",
        "email": "a@example.com",
        "brand": "Blodgett",
        "identity_key": None,
    }
    assert validate_row("crm.contact", row) == ()


def test_validate_row_reports_missing_required_column():
    """Round-2 finding: the "missing entirely" branch (`column not in row`) had no
    dedicated unit test -- only exercised indirectly via the cookbook's `d_901` fixture."""
    row = {
        "record_type": "deal",
        "object_id": "d_1",
        "last_modified_at": "2026-09-01T07:00:00Z",
        "stage": "quote_requested",
        "brand": "Blodgett",
        # quote_request_source_url omitted entirely
    }
    defects = validate_row("crm.deal", row)
    assert any(
        "quote_request_source_url" in d and "missing required column" in d
        for d in defects
    )


def test_validate_row_rejects_non_scalar_required_column_value():
    """Round-3 finding: the required-columns loop checked "missing" and "blank" but never
    "wrong shape" the way the key-column loop does -- a list/dict in a non-key required
    column (e.g. `brand`) was neither missing nor blank, so it passed clean."""
    row = {
        "record_type": "contact",
        "object_id": "c_1",
        "last_modified_at": "2026-09-01T07:00:00Z",
        "email": "a@example.com",
        "brand": ["Blodgett", "Follett"],
        "identity_key": None,
    }
    defects = validate_row("crm.contact", row)
    assert any("brand" in d and "not a scalar value" in d for d in defects)


def test_validate_row_rejects_non_scalar_required_column_value_on_session_replay():
    """Round-1 review [nit]: the check above is shared across every stream, but the
    motivating comment in `validate_row` cites session replay's click counts specifically
    ("a required column may legitimately be numeric") -- only `crm.contact.brand` was ever
    tested for this branch. Pinning it on the comment's own example."""
    row = {
        "session_id": "sess_1",
        "session_start_at": "2026-09-01T08:00:00Z",
        "last_updated_at": "2026-09-01T08:05:00Z",
        "rage_click_count": [1, 2, 3],
        "dead_click_count": 0,
        "script_error_count": 0,
        "session_url": "https://shop.example.com/quote",
        "identity_at_login": None,
        "identity_at_submit": None,
        "identity_key": None,
    }
    defects = validate_row("session_replay.session", row)
    assert any("rage_click_count" in d and "not a scalar value" in d for d in defects)


def test_validate_row_requires_brand_on_deal():
    """Round-2 [high] finding: the plan's C3 verify step queries "deals with empty or
    free-text brand", so `brand` must be a required column on `crm.deal`, not only on
    `crm.contact` -- a contact can request quotes across more than one brand."""
    row = {
        "record_type": "deal",
        "object_id": "d_1",
        "last_modified_at": "2026-09-01T07:00:00Z",
        "stage": "quote_requested",
        "quote_request_source_url": "https://shop.example.com/p/1",
        # brand omitted entirely
    }
    defects = validate_row("crm.deal", row)
    assert any("brand" in d and "missing required column" in d for d in defects)


def test_validate_row_rejects_null_brand_on_deal():
    """Round-1 review [minor]: the test above only proves `brand` rejects being *missing*
    entirely -- a different code path from `_is_blank`. Nothing proved a present-but-`None`
    `brand` on `crm.deal` is also rejected; `brand` is deliberately absent from
    `nullable_columns`, but nothing pinned that. Reproduced: adding `"brand"` to
    `crm.deal`'s `nullable_columns` would silently defeat the round-2 fix and still pass
    every other existing test."""
    row = {
        "record_type": "deal",
        "object_id": "d_1",
        "last_modified_at": "2026-09-01T07:00:00Z",
        "stage": "quote_requested",
        "quote_request_source_url": "https://shop.example.com/p/1",
        "brand": None,
    }
    defects = validate_row("crm.deal", row)
    assert any("brand" in d and "null or blank" in d for d in defects)


def test_validate_row_accepts_null_quote_request_source_url_on_deal():
    """Round-1 review [minor]: `quote_request_source_url`'s nullable-suppression was only
    ever exercised via "column entirely absent" (`test_validate_row_reports_missing_...`
    with the column omitted) -- a different code path from an explicit `null`. Reproduced:
    reverting `crm.deal`'s `nullable_columns` to `frozenset()` still passed every existing
    test, since none of them ever set this field to `None` rather than omitting it."""
    row = {
        "record_type": "deal",
        "object_id": "d_1",
        "last_modified_at": "2026-09-01T07:00:00Z",
        "stage": "renewal",
        "brand": "Blodgett",
        "quote_request_source_url": None,
    }
    assert validate_row("crm.deal", row) == ()


def test_validate_row_suppresses_defect_for_nullable_column():
    """Round-2 finding: `nullable_columns`'s runtime suppression effect in `validate_row`
    had no dedicated unit test -- only `LandingSchema.__post_init__`'s construction-time
    guards were pinned. A non-product-scoped analytics event (a homepage page_view) has no
    brand or product -- that's not dirty data, so it must not be reported."""
    row = {
        "user_id": "u_1",
        "session_id": "s_1",
        "event_name": "page_view",
        "occurred_at": "2026-09-01T08:00:00Z",
        "brand": None,
        "product_id": None,
        "identity_key": None,
    }
    assert validate_row("analytics.event", row) == ()


def test_validate_row_reports_missing_identity_key():
    """Round-1 review [minor]: `identity_key` being *required* (not just nullable) was
    untested on any of the three schemas that carry it -- the only guard against silently
    dropping it from `required_columns` was `LandingSchema.__post_init__`'s construction-time
    "nullable_columns subset of required_columns" check, which a fixture happening to still
    include the field would never trip. Reproduced: removing `identity_key` from both
    `required_columns` and `nullable_columns` on all three schemas left every existing test
    passing."""
    row = {
        "user_id": "u_1",
        "session_id": "s_1",
        "event_name": "product_view",
        "occurred_at": "2026-09-01T08:00:00Z",
        "brand": "Blodgett",
        "product_id": "p_1",
        # identity_key omitted entirely
    }
    defects = validate_row("analytics.event", row)
    assert any("identity_key" in d and "missing required column" in d for d in defects)


# --- resolve_stream_id: the Major finding's other unguarded path ---------------------------


def test_resolve_stream_id_rejects_non_mapping_row():
    with pytest.raises(ValueError, match="not a JSON object"):
        resolve_stream_id(SourceName.CRM, "not-an-object")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not a JSON object"):
        resolve_stream_id(SourceName.ANALYTICS, [1, 2, 3])  # type: ignore[arg-type]


def test_resolve_stream_id_rejects_missing_or_unknown_record_type():
    with pytest.raises(ValueError, match="record_type"):
        resolve_stream_id(SourceName.CRM, {})
    with pytest.raises(ValueError, match="unknown record_type"):
        resolve_stream_id(SourceName.CRM, {"record_type": "not-a-real-type"})


def test_resolve_stream_id_single_stream_sources_ignore_row_content():
    assert resolve_stream_id(SourceName.ANALYTICS, {}) == "analytics.event"
    assert resolve_stream_id(SourceName.SESSION_REPLAY, {}) == "session_replay.session"


# --- landing_path: the naive-datetime Major finding -----------------------------------------


def test_landing_path_rejects_naive_datetime():
    naive = datetime(2026, 9, 1, 23, 30, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        landing_path(SourceName.CRM, date(2026, 9, 1), naive)


def test_landing_path_is_stable_across_input_timezones():
    """The bug the finding reproduced: the same instant, expressed in two timezones, must
    produce the same filename -- only true once astimezone(UTC) always runs on an
    already-aware value."""
    from datetime import timedelta
    from datetime import timezone as tz

    utc_value = datetime(2026, 9, 1, 18, 0, 0, tzinfo=timezone.utc)
    ist_value = utc_value.astimezone(tz(timedelta(hours=5, minutes=30)))
    assert (
        ist_value.tzinfo != timezone.utc
    )  # genuinely a different tzinfo, same instant

    path_from_utc = landing_path(SourceName.CRM, date(2026, 9, 1), utc_value)
    path_from_ist = landing_path(SourceName.CRM, date(2026, 9, 1), ist_value)
    assert (
        path_from_utc
        == path_from_ist
        == "raw/crm/dt=2026-09-01/crm-20260901T180000Z.ndjson"
    )


# --- parse_landing_path: adversarial inputs (finding #11) ----------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "raw/crm/dt=2026-09-01/analytics-20260901T060000Z.ndjson",  # mismatched source
        "raw/../../etc/passwd",  # traversal
        # uppercase source: `_PATH_RE`'s `[a-z_]+` already excludes this, but even if it
        # didn't, `SourceName("CRM")` would still refuse it -- this case pins that second,
        # independent layer, not the regex's own case restriction.
        "raw/CRM/dt=2026-09-01/CRM-20260901T060000Z.ndjson",
        "raw/quantum/dt=2026-09-01/quantum-20260901T060000Z.ndjson",  # unknown source
        "raw/crm/dt=2026-09-01/crm-20260901T060000Z.csv",  # wrong extension
        "raw/crm/dt=2026-09-01/crm-2026-09-01T06:00:00Z.ndjson",  # malformed timestamp
        "raw/crm/dt=2026-13-40/crm-20260901T060000Z.ndjson",  # calendar-invalid date
        "not/even/the/right/shape.ndjson",
        "",
    ],
)
def test_parse_landing_path_rejects_adversarial_input(path):
    with pytest.raises(ValueError, match="does not conform to the landing convention"):
        parse_landing_path(path)


def test_parse_landing_path_round_trips_landing_path():
    ingested_at = datetime(2026, 9, 1, 6, 0, 0, tzinfo=timezone.utc)
    path = landing_path(SourceName.SESSION_REPLAY, date(2026, 9, 1), ingested_at)
    parts = parse_landing_path(path)
    assert parts.source is SourceName.SESSION_REPLAY
    assert parts.dt == date(2026, 9, 1)
    assert parts.ingested_at == ingested_at


# --- packaging: the landing fixtures must ship in a non-editable install -------------------


def test_landing_fixtures_are_covered_by_package_data_glob():
    """Lane R finding (post-merge, from building R4 against this contract): pyproject.toml's
    package-data only listed `contracts/_fixtures/*.json`, which does not match
    `contracts/_fixtures/landing/raw/**/*.ndjson` -- a non-editable install of `egpt-retail`
    would ship the landing contract without the fixtures that demonstrate it. Invisible in
    an editable checkout, which is where all the development happens."""
    product_root = Path(__file__).resolve().parents[1]
    package_root = product_root / "egpt_retail"
    fixture_files = sorted(
        (package_root / "contracts" / "_fixtures" / "landing" / "raw").rglob("*.ndjson")
    )
    assert fixture_files, "fixture files must exist to test coverage against"

    pyproject_text = (product_root / "pyproject.toml").read_text()
    match = re.search(r"egpt_retail\s*=\s*\[(.*?)\]", pyproject_text, re.DOTALL)
    assert match, "package-data's egpt_retail glob list not found in pyproject.toml"
    globs = re.findall(r'"([^"]+)"', match.group(1))

    for fixture_file in fixture_files:
        relative = fixture_file.relative_to(package_root)
        assert any(relative.match(glob) for glob in globs), (
            f"{relative} is not covered by any package-data glob in pyproject.toml"
        )
