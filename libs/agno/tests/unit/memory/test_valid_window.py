"""The valid-time window rule, in both encodings, over one case table (SM-V4 #98).

``ValidWindow.overlaps`` decides the window on the merged set; ``sql.window_predicate`` decides it
inside the vector substrate's ``WHERE`` clause. Two encodings of one rule is how a filter comes to
mean different things per leg -- a record kept by the SQL and dropped by the Python reads as a
flaky search rather than as a bug, because the discrepancy only shows when a record sits on the
boundary.

So the table below is evaluated **twice**: once through the Python predicate, once by evaluating
the emitted SQL fragment against the same values. The SQL half runs on sqlite via a synthesised
one-row table -- the fragment is ordinary three-valued boolean logic over two nullable integers,
which sqlite and Postgres agree on, and it keeps the property in the unit suite where a Postgres
requirement would push it into the integration one and out of most runs.
"""

import sqlite3

import pytest

from egpt.integrations.memory.vector.pgvector import sql
from egpt.memory.types import MemoryQuery, ValidWindow

#: ``(t_valid, t_invalid)`` for a record, covering both open ends and both boundaries.
RECORDS = [
    (None, None),  # open at both ends -- overlaps every window
    (None, 100),
    (None, 500),
    (100, None),
    (500, None),
    (100, 200),
    (200, 300),
    (299, 300),
    (300, 301),  # starts exactly where a [.., 300) window ends
    (99, 100),  # ends exactly where a [100, ..) window starts
    (0, 1000),
]

#: Windows, including both single-sided forms and the degenerate point window.
WINDOWS = [
    ValidWindow(),
    ValidWindow(valid_from=100),
    ValidWindow(valid_to=300),
    ValidWindow(valid_from=100, valid_to=300),
    ValidWindow(valid_from=299, valid_to=300),
    ValidWindow.at(200),
    ValidWindow.at(0),
]

CASES = [(record, window) for record in RECORDS for window in WINDOWS]


def sql_verdict(connection, window, t_valid, t_invalid):
    """Evaluate the emitted fragment as a boolean against one record's bounds."""
    clause, binds = sql.window_predicate(window)
    if not clause:
        return True
    # Named binds in the fragment are `:name`, which sqlite3 accepts natively.
    row = connection.execute(
        f"SELECT 1 FROM bounds WHERE {clause}",
        {**binds, "t_valid": t_valid, "t_invalid": t_invalid},
    ).fetchone()
    return row is not None


@pytest.fixture()
def connection():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE bounds (t_valid INTEGER, t_invalid INTEGER)")
    yield conn
    conn.close()


@pytest.mark.parametrize(("record", "window"), CASES)
def test_the_sql_and_python_encodings_of_the_window_agree(connection, record, window):
    """The property SM-V4 rests on: the substrate narrows in-search and the provider re-applies the
    same rule after reconciliation, so the two must never disagree about a record."""
    t_valid, t_invalid = record
    connection.execute("DELETE FROM bounds")
    connection.execute("INSERT INTO bounds VALUES (?, ?)", (t_valid, t_invalid))
    assert window.overlaps(t_valid, t_invalid) == sql_verdict(connection, window, t_valid, t_invalid), (
        record,
        window,
    )


@pytest.mark.parametrize("record", RECORDS)
@pytest.mark.parametrize("instant", [0, 99, 100, 200, 299, 300, 1000])
def test_a_point_window_is_the_point_predicate(record, instant):
    """``ValidWindow.at`` must reproduce ``t_valid <= at < t_invalid`` exactly, or the provider's
    ``as_of`` filter changed meaning when it was rewritten onto the shared rule."""
    t_valid, t_invalid = record
    expected = (t_valid is None or t_valid <= instant) and (t_invalid is None or t_invalid > instant)
    assert ValidWindow.at(instant).overlaps(t_valid, t_invalid) is expected


def test_an_open_window_is_no_filter_at_all():
    """Neither bound set means the caller asked for no narrowing, not for an empty answer."""
    assert MemoryQuery(text="x").window() is None
    for record in RECORDS:
        assert ValidWindow().overlaps(*record) is True


@pytest.mark.parametrize(
    ("valid_from", "valid_to"),
    [(100, 100), (200, 100)],
)
def test_an_empty_window_is_refused_rather_than_returning_nothing(valid_from, valid_to):
    """Half-open, so ``valid_from == valid_to`` is empty too -- a caller deriving both ends from one
    timestamp lands there, and an empty result reads as "no such memories"."""
    with pytest.raises(ValueError, match="empty"):
        MemoryQuery(text="x", valid_from=valid_from, valid_to=valid_to)


@pytest.mark.parametrize("field", ["valid_from", "valid_to"])
def test_a_negative_bound_is_refused(field):
    """Range-checked like ``as_of``: a negative instant is not a narrower question, it is one no
    record can satisfy."""
    with pytest.raises(ValueError, match=field):
        MemoryQuery(text="x", **{field: -1})


@pytest.mark.parametrize("field", ["valid_from", "valid_to"])
def test_a_naive_datetime_bound_is_refused_like_every_other_timestamp(field):
    """The window goes through ``_epoch_or_none``, so it inherits the naive-datetime refusal rather
    than being the one timestamp field that reads a local time as UTC."""
    from datetime import datetime

    with pytest.raises(TypeError):
        MemoryQuery(text="x", **{field: datetime(2026, 1, 1)})


def test_as_of_and_a_window_are_refused_together():
    """One valid-time question per read. A provider applies whichever is set as *the* filter, so
    accepting both would silently intersect them -- and an empty intersection returns a plain miss
    that looks like an empty corpus rather than a bad request."""
    with pytest.raises(ValueError, match="as_of"):
        MemoryQuery(text="x", as_of=100, valid_from=400, valid_to=600)
    with pytest.raises(ValueError, match="as_of"):
        MemoryQuery(text="x", as_of=100, valid_to=600)
    with pytest.raises(ValueError, match="as_of"):
        MemoryQuery(text="x", as_of=100, valid_from=400)


@pytest.mark.parametrize("field", ["valid_from", "valid_to"])
def test_an_aware_datetime_bound_is_normalised_to_epoch_seconds(field):
    """The refusal test alone is not enough: with the normalisation removed, a ``datetime`` survives
    into the field and the *range* check raises a ``TypeError`` comparing it to ``0`` -- so a test
    asserting only ``TypeError`` passes on the broken code. This asserts the conversion happened.
    """
    from datetime import datetime, timezone

    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    query = MemoryQuery(text="x", **{field: moment})
    value = getattr(query, field)
    assert isinstance(value, int) and not isinstance(value, bool)
    assert value == int(moment.timestamp())


@pytest.mark.parametrize(("valid_from", "valid_to"), [(200, 50), (100, 100)])
def test_an_inverted_window_is_refused_at_construction(valid_from, valid_to):
    """``ValidWindow`` is exported and constructed directly, so ``MemoryQuery``'s guard is not the
    only entry -- SM-V3's legs will assemble one without going through a query.

    An inverted window is not merely empty, which would at least be uniform. ``overlaps`` checks
    each end independently, so ``[200, 50)`` still admits a record with unset bounds while
    rejecting its neighbours: a wrong answer rather than no answer.
    """
    with pytest.raises(ValueError, match="empty or inverted"):
        ValidWindow(valid_from=valid_from, valid_to=valid_to)


def test_the_point_window_and_every_single_sided_window_still_construct():
    """The guard must not catch the shapes the code actually builds -- a point read is
    ``[at, at + 1)``, and either bound alone is a legitimate half-open window."""
    assert ValidWindow.at(0).valid_to == 1
    assert ValidWindow.at(1_700_000_000).valid_from == 1_700_000_000
    assert ValidWindow(valid_from=100).valid_to is None
    assert ValidWindow(valid_to=100).valid_from is None
    assert ValidWindow() == ValidWindow(None, None)


@pytest.mark.parametrize("field", ["valid_from", "valid_to"])
def test_a_float_bound_is_refused_at_construction(field):
    """SM-V3's legs build a ``ValidWindow`` directly, with none of ``MemoryQuery``'s coercion. A
    float bound (a ``time.time()`` result is the obvious source) binds into the SQL predicate as a
    float, and `t_invalid > 100.5` disagrees with `t_invalid > 100` only at a record stamped
    exactly 100 -- silently, and only at the one boundary this module's tests exist to get right.
    """
    with pytest.raises(TypeError, match=field):
        ValidWindow(**{field: 100.5})


@pytest.mark.parametrize("field", ["valid_from", "valid_to"])
def test_a_bool_bound_is_refused_like_every_other_epoch_field(field):
    """``bool`` is an ``int`` subclass, so an unguarded check lets ``True`` silently become ``1``
    -- the exact defect ``_epoch_or_none`` refuses elsewhere in this module."""
    with pytest.raises(TypeError, match=field):
        ValidWindow(**{field: True})
