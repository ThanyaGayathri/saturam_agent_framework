"""Unit tests for R7's deliver_in_app executor (G4) -- the dry-run-vs-real content shape.
No database: the actual write is exercised only by the cookbook.
"""

from __future__ import annotations

import asyncio
import re

from egpt.workflow.types import StepInput

from egpt_retail.delivery.ddl import _DDL as _DELIVERY_DDL
from egpt_retail.delivery.ddl import DELIVERY_TABLE
from egpt_retail.executors import delivery as delivery_module
from egpt_retail.executors.delivery import (
    DRY_RUN_KEY,
    _delivery_payload,
    deliver_in_app,
)


class _FakeEngine:
    """A stand-in for the real Engine build_control_plane_engine returns -- disposed but never
    connected to, since these tests patch the write function itself."""

    def dispose(self) -> None:
        pass


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


# ---------------------------------------------------------------------------
# deliver_in_app itself -- the dry_run gating and field wiring that actually reaches storage
# ---------------------------------------------------------------------------


def _run(step_input: StepInput, monkeypatch):
    written = {}

    async def fake_awrite_delivery(engine, **kwargs):
        written.update(kwargs)

    monkeypatch.setattr(delivery_module, "awrite_delivery", fake_awrite_delivery)
    monkeypatch.setattr(
        delivery_module, "build_control_plane_engine", lambda settings: _FakeEngine()
    )
    output = asyncio.run(deliver_in_app(step_input))
    return output, written


def test_deliver_in_app_dry_run_previews_and_leaves_delivered_at_unset(monkeypatch):
    step_input = StepInput(
        additional_data={DRY_RUN_KEY: True, "use_case_id": "uc_1"},
        previous_step_content={"provenance_ref": "run_x"},
    )
    output, written = _run(step_input, monkeypatch)

    assert written["run_id"] == "run_x"
    assert written["use_case_id"] == "uc_1"
    assert written["dry_run"] is True
    assert written["delivered_at"] is None
    assert output.content["status"] == "previewed"
    assert output.content["dry_run"] is True


def test_deliver_in_app_real_run_delivers_and_stamps_delivered_at(monkeypatch):
    step_input = StepInput(
        additional_data={}, previous_step_content={"provenance_ref": "run_x"}
    )
    output, written = _run(step_input, monkeypatch)

    assert written["dry_run"] is False
    assert written["delivered_at"] is not None
    assert output.content["status"] == "delivered"
    assert output.content["dry_run"] is False


def test_deliver_in_app_disposes_its_engine(monkeypatch):
    disposed = []

    async def fake_awrite_delivery(engine, **kwargs):
        pass

    class _TrackedEngine(_FakeEngine):
        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(delivery_module, "awrite_delivery", fake_awrite_delivery)
    monkeypatch.setattr(
        delivery_module, "build_control_plane_engine", lambda settings: _TrackedEngine()
    )

    asyncio.run(deliver_in_app(StepInput(additional_data={})))

    assert disposed == [True]


# ---------------------------------------------------------------------------
# DDL is idempotent by construction -- mirrors test_provenance_ddl.py's own checks, since
# nothing previously exercised retail_deliveries' DDL at all.
# ---------------------------------------------------------------------------


def test_delivery_table_name_is_retail_deliveries():
    assert DELIVERY_TABLE == "retail_deliveries"


def test_delivery_ddl_uses_create_table_if_not_exists():
    assert "CREATE TABLE IF NOT EXISTS" in _DELIVERY_DDL


def test_delivery_ddl_has_no_non_idempotent_create():
    bad = re.findall(r"CREATE\s+(?!TABLE\s+IF)([A-Z ]+?)\s", _DELIVERY_DDL)
    assert not bad, bad


def test_delivery_ddl_defaults_dry_run_false_and_channel_in_app():
    assert "dry_run       BOOLEAN NOT NULL DEFAULT false" in _DELIVERY_DDL
    assert "DEFAULT 'in_app'" in _DELIVERY_DDL
