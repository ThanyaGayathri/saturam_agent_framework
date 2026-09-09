"""Unit tests for R7's deliver_in_app executor (G4) -- the dry-run-vs-real content shape.
No database: the actual write is exercised only by the cookbook.
"""

from __future__ import annotations

from egpt_retail.executors.delivery import DRY_RUN_KEY, _delivery_payload


def test_real_delivery_payload_is_marked_delivered():
    payload = _delivery_payload(
        dry_run=False, channel="in_app", explanation="the numbers"
    )
    assert payload["status"] == "delivered"
    assert payload["dry_run"] is False
    assert payload["explanation"] == "the numbers"


def test_dry_run_payload_is_marked_previewed():
    payload = _delivery_payload(
        dry_run=True, channel="in_app", explanation="the numbers"
    )
    assert payload["status"] == "previewed"
    assert payload["dry_run"] is True


def test_dry_run_key_is_the_string_dry_run():
    """Pins the exact convention string other callers must match to turn dry-run on."""
    assert DRY_RUN_KEY == "dry_run"
