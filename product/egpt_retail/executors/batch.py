"""The batch that flows through a document-to-record run, and how executors read it (K15).

Every document-driven stage after Receive reads the same shape from
``previous_step_content`` and returns it enriched::

    {"documents": [{"message": {...}, "document": {...}, "extracted": {...},
                    "resolved": {...}, "guardrails": {...}, "record": {...},
                    "written": {...}}, ...],
     "exceptions": [{"message_id", "sender", "subject", "class", "detail", "stage"}, ...],
     "stats": {...}}

A document leaves ``documents`` and lands in ``exceptions`` the moment a stage cannot carry
it further; it never goes on with a gap. The two lists travel together to the end so the
Notify stage reports both -- which is why a branch stage stops the run (``stop=True``) only
when there is nothing at all left: no documents *and* no exceptions. Stopping on "no
documents survived" would leave the exceptions unsent, and an unsent exception is the one
failure mode the person who set the guardrails cannot see.

Slot values reach executors through ``additional_data`` under the slot's own name
(``"source mailbox"``, as the compiler's ``CompiledWorkflow.parameters`` keys them) or its
slug (``source_mailbox``); ``slot`` accepts both, plus the interface's camelCase.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

from egpt.workflow.types import StepInput

__all__ = [
    "slot",
    "batch_from",
    "empty_batch",
    "add_exception",
    "is_dry_run",
    "first_int",
]

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG.sub("_", name.lower()).strip("_")


def _camel(name: str) -> str:
    parts = _slug(name).split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def slot(step_input: StepInput, name: str, default: Any = None) -> Any:
    """The slot's value from ``additional_data`` by name, slug or camelCase; ``default`` when
    absent or blank."""
    options: Mapping[str, Any] = step_input.additional_data or {}
    for key in (name, _slug(name), _camel(name), name.replace(" ", "-")):
        value = options.get(key)
        if value is not None and value != "" and value != []:
            return value
    return default


def is_dry_run(step_input: StepInput) -> bool:
    return bool((step_input.additional_data or {}).get("dry_run", False))


def empty_batch() -> Dict[str, Any]:
    return {"documents": [], "exceptions": [], "stats": {}}


def batch_from(
    step_input: StepInput, *, step_names: tuple = ()
) -> Optional[Dict[str, Any]]:
    """The batch this step should read: ``previous_step_content`` when it is one, else the
    most recent named step's output that is (a model step in between hands back its own
    output, not the batch it was shown)."""
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping) and "documents" in previous:
        return _copy(previous)
    for name in step_names:
        output = step_input.get_step_output(name)
        if (
            output is not None
            and isinstance(output.content, Mapping)
            and "documents" in output.content
        ):
            return _copy(output.content)
    outputs = step_input.previous_step_outputs or {}
    for output in reversed(list(outputs.values())):
        if isinstance(output.content, Mapping) and "documents" in output.content:
            return _copy(output.content)
    return None


def _copy(batch: Mapping[str, Any]) -> Dict[str, Any]:
    """A copy whose lists are this step's own, so a stage that moves a document to the
    exceptions list does not rewrite the previous step's recorded output."""
    out = dict(batch)
    out["documents"] = [dict(d) for d in batch.get("documents") or []]
    out["exceptions"] = [dict(e) for e in batch.get("exceptions") or []]
    out["stats"] = dict(batch.get("stats") or {})
    return out


def add_exception(
    batch: Dict[str, Any],
    document: Mapping[str, Any],
    *,
    cls: str,
    detail: str,
    stage: str,
) -> Dict[str, Any]:
    """Move one document into the exceptions list. Returns the exception row."""
    message: Mapping[str, Any] = document.get("message") or {}
    row = {
        "message_id": message.get("id"),
        "sender": message.get("sender"),
        "subject": message.get("subject"),
        "po_number": (document.get("extracted") or {}).get("po_number"),
        "class": cls,
        "detail": detail,
        "stage": stage,
    }
    batch.setdefault("exceptions", []).append(row)
    return row


def first_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """``50``, ``"50 records"``, ``"10 SKUs"`` -> 50, 50, 10."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else default


def strip_attachment_data(message: Mapping[str, Any]) -> Dict[str, Any]:
    """The message without attachment bytes: what travels past Screen."""
    out = dict(message)
    out["attachments"] = [
        {k: v for k, v in a.items() if k not in {"data", "data_b64"}}
        for a in message.get("attachments") or []
    ]
    return out


def documents_of(batch: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [dict(d) for d in batch.get("documents") or []]
