"""``get_request_kwargs`` parses ``additional_data`` the way it parses ``metadata``.

Found by R5's nightly schedule: ``ScheduleExecutor._to_form_value`` JSON-encodes a dict payload
value, ``get_request_kwargs`` parsed only five named fields back, and ``additional_data`` --
the workflow run's per-step bag -- reached the workflow as a string. Every executor that read
it as a mapping failed on its first key, from a scheduled run only, never from a test client
that passed a dict.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from egpt.os.utils import get_request_kwargs


class _Request:
    def __init__(self, form: Dict[str, Any]) -> None:
        self._form = form

    async def form(self) -> Dict[str, Any]:
        return dict(self._form)


async def _endpoint(message: str, stream: bool = True) -> None:  # the "known fields"
    pass


@pytest.mark.asyncio
async def test_a_json_encoded_additional_data_arrives_as_a_dict():
    bag = {"trigger": "schedule", "as_of": "2026-09-09", "thresholds": {"row_count_delta": 0.5}}
    request = _Request({"message": "nightly", "additional_data": json.dumps(bag)})

    kwargs = await get_request_kwargs(request, _endpoint)

    assert kwargs["additional_data"] == bag
    assert "message" not in kwargs, "known endpoint fields are not swept into kwargs"


@pytest.mark.asyncio
async def test_a_dict_passed_directly_is_left_alone():
    bag = {"trigger": "manual"}
    request = _Request({"message": "x", "additional_data": bag})

    kwargs = await get_request_kwargs(request, _endpoint)

    assert kwargs["additional_data"] is bag


@pytest.mark.asyncio
async def test_a_string_that_is_not_json_is_dropped_with_a_warning(caplog):
    request = _Request({"message": "x", "additional_data": "{not json"})

    kwargs = await get_request_kwargs(request, _endpoint)

    assert "additional_data" not in kwargs
    assert any("additional_data" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_a_json_value_that_is_not_an_object_is_dropped():
    """A bag has to be a mapping: `StepInput.additional_data` is typed as one and every
    executor indexes it. A JSON list or scalar is not silently handed on."""
    request = _Request({"message": "x", "additional_data": json.dumps(["not", "a", "bag"])})

    kwargs = await get_request_kwargs(request, _endpoint)

    assert "additional_data" not in kwargs
