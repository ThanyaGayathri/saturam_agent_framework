"""Tests for EventsBuffer — monotonic indexing and trim correctness."""

from egpt.os.managers import EventsBuffer
from egpt.run.agent import RunContentEvent
from egpt.run.base import RunStatus


def _make_event(content: str) -> RunContentEvent:
    return RunContentEvent(content=content)


class TestMonotonicIndex:
    """event_index must increase monotonically even after buffer trims."""

    def test_indices_monotonic_no_trim(self):
        buf = EventsBuffer(max_events_per_run=100)
        indices = [buf.add_event("r1", _make_event(f"e{i}")) for i in range(10)]
        assert indices == list(range(10))

    def test_indices_monotonic_after_trim(self):
        buf = EventsBuffer(max_events_per_run=3)
        indices = [buf.add_event("r1", _make_event(f"e{i}")) for i in range(7)]
        # Must be 0,1,2,3,4,5,6 — NOT 0,1,2,3,3,3,3
        assert indices == [0, 1, 2, 3, 4, 5, 6]

    def test_buffer_size_capped(self):
        buf = EventsBuffer(max_events_per_run=3)
        for i in range(7):
            buf.add_event("r1", _make_event(f"e{i}"))
        # Only last 3 events kept in memory
        assert len(buf.events["r1"]) == 3
        assert buf.events["r1"][0].content == "e4"
        assert buf.events["r1"][2].content == "e6"


class TestGetEventsAfterTrim:
    """get_events must return correct (index, event) tuples using monotonic indices after trims."""

    def test_get_all_events_no_trim(self):
        buf = EventsBuffer(max_events_per_run=100)
        for i in range(5):
            buf.add_event("r1", _make_event(f"e{i}"))
        events = buf.get_events("r1")
        assert len(events) == 5
        # Should return tuples with correct monotonic indices
        assert events[0] == (0, events[0][1])
        assert events[4] == (4, events[4][1])

    def test_get_events_since_index_no_trim(self):
        buf = EventsBuffer(max_events_per_run=100)
        for i in range(5):
            buf.add_event("r1", _make_event(f"e{i}"))
        # Client has 0,1,2 — wants 3,4
        events = buf.get_events("r1", last_event_index=2)
        assert len(events) == 2
        assert events[0][0] == 3
        assert events[0][1].content == "e3"
        assert events[1][0] == 4
        assert events[1][1].content == "e4"

    def test_get_events_since_index_after_trim(self):
        buf = EventsBuffer(max_events_per_run=5)
        for i in range(10):
            buf.add_event("r1", _make_event(f"e{i}"))
        # Buffer holds e5..e9 (indices 5-9). Client has up to index 7.
        events = buf.get_events("r1", last_event_index=7)
        assert len(events) == 2
        assert events[0][0] == 8
        assert events[0][1].content == "e8"
        assert events[1][0] == 9
        assert events[1][1].content == "e9"

    def test_get_events_client_behind_buffer(self):
        """Client's last index is older than what the buffer still holds."""
        buf = EventsBuffer(max_events_per_run=3)
        for i in range(10):
            buf.add_event("r1", _make_event(f"e{i}"))
        # Buffer holds e7,e8,e9. Client has index 2 — way behind.
        # Should return everything in the buffer with correct indices.
        events = buf.get_events("r1", last_event_index=2)
        assert len(events) == 3
        assert events[0][0] == 7
        assert events[0][1].content == "e7"
        assert events[2][0] == 9
        assert events[2][1].content == "e9"

    def test_get_events_client_caught_up(self):
        buf = EventsBuffer(max_events_per_run=5)
        for i in range(10):
            buf.add_event("r1", _make_event(f"e{i}"))
        # Client has index 9 (the latest)
        events = buf.get_events("r1", last_event_index=9)
        assert events == []

    def test_get_events_none_index_returns_all(self):
        buf = EventsBuffer(max_events_per_run=3)
        for i in range(7):
            buf.add_event("r1", _make_event(f"e{i}"))
        events = buf.get_events("r1", last_event_index=None)
        assert len(events) == 3
        # After trim, buffer holds e4,e5,e6 with indices 4,5,6
        assert events[0][0] == 4
        assert events[0][1].content == "e4"

    def test_get_events_unknown_run(self):
        buf = EventsBuffer(max_events_per_run=10)
        assert buf.get_events("nonexistent") == []
        assert buf.get_events("nonexistent", last_event_index=5) == []


class TestCleanup:
    """Cleanup should remove index counters alongside event data."""

    def test_cleanup_removes_index_counter(self):
        buf = EventsBuffer(max_events_per_run=10)
        buf.add_event("r1", _make_event("e0"))
        assert "r1" in buf._next_index
        buf.cleanup_run("r1")
        assert "r1" not in buf._next_index
        assert "r1" not in buf.events

    def test_new_run_after_cleanup_starts_at_zero(self):
        buf = EventsBuffer(max_events_per_run=10)
        for i in range(5):
            buf.add_event("r1", _make_event(f"e{i}"))
        buf.cleanup_run("r1")
        # New run with same ID starts fresh
        idx = buf.add_event("r1", _make_event("fresh"))
        assert idx == 0


class TestMultipleRuns:
    """Indices are independent per run_id."""

    def test_independent_counters(self):
        buf = EventsBuffer(max_events_per_run=3)
        for i in range(5):
            idx_a = buf.add_event("a", _make_event(f"a{i}"))
            idx_b = buf.add_event("b", _make_event(f"b{i}"))
            assert idx_a == i
            assert idx_b == i

    def test_get_events_isolated(self):
        buf = EventsBuffer(max_events_per_run=3)
        for i in range(5):
            buf.add_event("a", _make_event(f"a{i}"))
        for i in range(8):
            buf.add_event("b", _make_event(f"b{i}"))
        # Run "a": buffer has a2,a3,a4 (indices 2-4)
        events_a = buf.get_events("a", last_event_index=3)
        assert len(events_a) == 1
        assert events_a[0][0] == 4
        assert events_a[0][1].content == "a4"
        # Run "b": buffer has b5,b6,b7 (indices 5-7)
        events_b = buf.get_events("b", last_event_index=5)
        assert len(events_b) == 2
        assert events_b[0][0] == 6
        assert events_b[0][1].content == "b6"


class TestRunTenantRegistration:
    """register_run_tenant / get_run_tenant (aicoe-tracker#196)."""

    def test_unregistered_run_is_unknown(self):
        buf = EventsBuffer()
        assert buf.get_run_tenant("never-registered") == (False, None)

    def test_registered_run_is_known_even_with_no_tenant(self):
        """A run_id present as a key -- even mapped to None -- is 'known': the None means an
        untenanted/exempt caller registered it, not that nothing was ever recorded.
        """
        buf = EventsBuffer()
        buf.register_run_tenant("r1", None)
        assert buf.get_run_tenant("r1") == (True, None)

    def test_registered_run_returns_its_tenant(self):
        buf = EventsBuffer()
        buf.register_run_tenant("r1", "acme")
        assert buf.get_run_tenant("r1") == (True, "acme")

    def test_cleanup_run_removes_the_tenant_registration(self):
        buf = EventsBuffer()
        buf.register_run_tenant("r1", "acme")
        buf.cleanup_run("r1")
        assert buf.get_run_tenant("r1") == (False, None)
        assert "r1" not in buf._run_tenants_registered_at


class TestCleanupSweepsOrphanedAndPausedTenants:
    """aicoe-tracker#196 QC round 2, finding 4: run_tenants entries that cleanup_runs()'s
    run_metadata-keyed sweep alone would never discover.
    """

    def test_orphaned_registration_with_no_buffered_event_is_swept_after_cleanup_interval(self, monkeypatch):
        """A run registered at start time whose background task errored before its first
        add_event has no run_metadata entry -- cleanup_runs() must still find and remove it
        once it's aged past cleanup_interval, or it leaks for the process lifetime.
        """
        buf = EventsBuffer(cleanup_interval=100)
        fake_time = [1000.0]
        monkeypatch.setattr("egpt.os.managers.time", lambda: fake_time[0])

        buf.register_run_tenant("orphan", "acme")
        assert "orphan" not in buf.run_metadata  # no event was ever added for it

        fake_time[0] += 50  # still within cleanup_interval
        buf.cleanup_runs()
        assert buf.get_run_tenant("orphan") == (True, "acme")

        fake_time[0] += 100  # now past cleanup_interval since registration
        buf.cleanup_runs()
        assert buf.get_run_tenant("orphan") == (False, None)

    def test_orphaned_registration_with_a_live_run_is_not_swept(self, monkeypatch):
        """A run that DID produce an event (so run_metadata exists and is still running) must
        not be treated as orphaned just because it's aged -- only the run_metadata-less case is
        an orphan.
        """
        buf = EventsBuffer(cleanup_interval=100)
        fake_time = [1000.0]
        monkeypatch.setattr("egpt.os.managers.time", lambda: fake_time[0])

        buf.register_run_tenant("live", "acme")
        buf.add_event("live", _make_event("hello"))

        fake_time[0] += 200  # well past cleanup_interval
        buf.cleanup_runs()
        assert buf.get_run_tenant("live") == (True, "acme")

    def test_a_paused_run_is_swept_after_paused_cleanup_interval(self, monkeypatch):
        """cleanup_runs() previously only swept completed/error/cancelled -- a paused run
        that's never resumed pinned its events/metadata/tenant entries indefinitely.
        """
        buf = EventsBuffer(cleanup_interval=100, paused_cleanup_interval=1000)
        fake_time = [1000.0]
        monkeypatch.setattr("egpt.os.managers.time", lambda: fake_time[0])

        buf.register_run_tenant("paused-run", "acme")
        buf.add_event("paused-run", _make_event("hello"))
        buf.set_run_completed("paused-run", RunStatus.paused)

        fake_time[0] += 500  # past cleanup_interval (100) but not paused_cleanup_interval (1000)
        buf.cleanup_runs()
        assert buf.get_run_tenant("paused-run") == (True, "acme")
        assert "paused-run" in buf.run_metadata

        fake_time[0] += 600  # now past paused_cleanup_interval since it paused
        buf.cleanup_runs()
        assert buf.get_run_tenant("paused-run") == (False, None)
        assert "paused-run" not in buf.run_metadata

    def test_a_completed_run_still_uses_the_shorter_cleanup_interval(self, monkeypatch):
        """The separate, longer paused retention must not accidentally widen the retention for
        ordinary completed/error/cancelled runs.
        """
        buf = EventsBuffer(cleanup_interval=100, paused_cleanup_interval=100000)
        fake_time = [1000.0]
        monkeypatch.setattr("egpt.os.managers.time", lambda: fake_time[0])

        buf.add_event("done", _make_event("hello"))
        buf.set_run_completed("done", RunStatus.completed)

        fake_time[0] += 200  # past cleanup_interval
        buf.cleanup_runs()
        assert "done" not in buf.run_metadata
