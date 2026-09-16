"""The Act shape's four write-side executors (K15, K-15): guardrails, the per-run cap, the
write, the rollback point. Named in ``SHAPE_STAGES[UseCaseShape.ACT]`` since K15 landed and
owed since; the document-to-record template reuses all four unchanged, which is the reason
they are generic verbs and not "check the order".

Two of them are branches that guard the write (``guards_write=True`` in the stage table):
``apply_hard_limits`` and ``apply_per_run_cap``. Both hold rather than stop: a document (or
a proposed change) that fails a guardrail moves to the exceptions list and the run goes on
for the rest, because a batch of twelve orders with one bad due date should create eleven
orders and one exception, not nothing. The compiler's "no gate, no steps" rule is what makes
their absence fatal; their presence never is.

Both input shapes are handled here, because the shape declared them once:

* a **document batch** (``executors/batch.py``) from the document-to-record template --
  record guardrails from ``documents/guardrails.py``, the cap in records, the write through
  the target mapping's record tree;
* a **proposal** (``{"changes": [{...}]}``) from the KPI-change template's proposal model
  step -- an amount guardrail (``guardrail_amount``: a percentage or absolute bound per
  change), the cap in changes, the write deferred to a target the platform has not
  connected yet (reported as ``NOT_CONNECTED``, never silently skipped).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import date
from typing import Any, Dict, List, Mapping, Optional

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.documents.guardrails import check_document, rules_from
from egpt_retail.executors.batch import (
    add_exception,
    batch_from,
    empty_batch,
    first_int,
    is_dry_run,
    slot,
)

__all__ = [
    "ACT_EXECUTORS",
    "apply_hard_limits",
    "apply_per_run_cap",
    "write_to_target",
    "record_rollback_point",
]

log = logging.getLogger(__name__)

_DOCUMENT_STEPS = (
    "Resolve",
    "resolve_entities",
    "Build record",
    "build_record",
    "Write",
    "write_to_target",
)


def _proposal_of(step_input: StepInput) -> Optional[Dict[str, Any]]:
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping) and "changes" in previous:
        return dict(previous)
    return None


def _parse_amount(value: Any) -> Optional[Dict[str, float]]:
    """``"10%"`` -> {"percent": 10}; ``"5"``/``5`` -> {"absolute": 5}; None when unusable."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"absolute": float(value)}
    text = str(value).strip()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)?", text)
    if not match:
        return None
    number = float(match.group(1))
    return {"percent": number} if match.group(2) else {"absolute": number}


async def apply_hard_limits(step_input: StepInput) -> StepOutput:
    """Record-level checks before anything is written. Reads the ``guardrails`` slot.

    Document batch: the enabled record guardrails run over every resolved document
    (``documents/guardrails.py``); any failure moves the document to the exceptions list with
    the guardrail's own exception class. A guardrail whose master-data role is not loaded
    is reported as skipped on every document, never counted as passed.

    Proposal: each change's ``proposed`` against its ``current`` under the amount bound;
    changes over the bound are held with ``OVER_GUARDRAIL``.

    Stops the run only when nothing at all is left.
    """
    proposal = _proposal_of(step_input)
    if proposal is not None:
        return _limit_proposal(proposal, slot(step_input, "guardrails"))

    batch = batch_from(step_input, step_names=_DOCUMENT_STEPS)
    if batch is None:
        return StepOutput(
            content={**empty_batch(), "notes": "no batch to check"},
            success=True,
            stop=True,
        )
    enabled = rules_from(slot(step_input, "guardrails", "all"))
    master = _master_rows(step_input, batch)
    survivors: List[Dict[str, Any]] = []
    skipped: set = set()
    seen: List[tuple] = []
    for document in batch["documents"]:
        document = dict(document)
        verdict = check_document(
            document, master, enabled, today=date.today(), seen_this_run=seen
        )
        document["guardrails"] = verdict
        skipped.update(verdict["skipped"])
        customer = (document.get("resolved") or {}).get("customer") or {}
        seen.append(
            (
                customer.get("id"),
                str(
                    document.get("reference")
                    or (document.get("extracted") or {}).get("po_number")
                    or ""
                ),
            )
        )
        if verdict["exceptions"]:
            first = verdict["exceptions"][0]
            detail = "; ".join(
                f"{e['guardrail']}: {e['detail']}" for e in verdict["exceptions"]
            )
            add_exception(
                batch,
                document,
                cls=str(first["class"]),
                detail=detail[:400],
                stage="guardrails",
            )
            continue
        survivors.append(document)
    batch["documents"] = survivors
    batch.setdefault("stats", {})["passed_guardrails"] = len(survivors)
    batch["guardrails_enabled"] = [rule.id for rule in enabled]
    batch["guardrails_skipped"] = sorted(skipped)
    nothing_left = not survivors and not batch["exceptions"]
    return StepOutput(content=batch, success=True, stop=nothing_left)


def _master_rows(step_input: StepInput, batch: Mapping[str, Any]) -> Dict[str, Any]:
    """The rows the guardrails need, from the same slot the resolver read (injected rows)
    or the rows the resolver loaded and left on the batch."""
    value = slot(step_input, "master data")
    if isinstance(value, Mapping) and any(
        k in value for k in ("customer", "product", "order_history")
    ):
        return dict(value)
    options = step_input.additional_data or {}
    if isinstance(options.get("master_data_rows"), Mapping):
        return dict(options["master_data_rows"])
    if isinstance(batch.get("master_data"), Mapping):
        return dict(batch["master_data"])
    # The resolver loaded from a source; it leaves the roles it saw, not the rows. History
    # is what the guardrails need and the resolver did not keep, so reload it: the Order's
    # history from its items, any other target's from the target itself (K21).
    roles = batch.get("master_data_roles") or []
    if roles and "order_history" in roles:
        from egpt_retail.documents.mapping import mapping_from
        from egpt_retail.documents.salesforce import (
            client_from,
            load_master_data,
            load_target_history,
        )

        objects = slot(step_input, "target objects", "order_orderitem")
        try:
            mapping = mapping_from(
                objects if isinstance(objects, Mapping) else str(objects)
            )
        except (KeyError, ValueError):
            mapping = None
        client = client_from(slot(step_input, "write target"))
        try:
            if mapping is None or mapping.object == "Order":
                return load_master_data(
                    client, roles=("customer", "product", "order_history")
                )
            rows = load_master_data(client, roles=("customer", "product"))
            rows["order_history"] = load_target_history(
                client, mapping, batch.get("reference_field")
            )
            return rows
        finally:
            client.close()
    return {}


def _limit_proposal(proposal: Dict[str, Any], guardrail: Any) -> StepOutput:
    bound = _parse_amount(guardrail)
    kept: List[Dict[str, Any]] = []
    held: List[Dict[str, Any]] = list(proposal.get("held") or [])
    for change in proposal.get("changes") or []:
        current, proposed = change.get("current"), change.get("proposed")
        over = False
        if bound and current is not None and proposed is not None:
            try:
                delta = abs(float(proposed) - float(current))
                if "percent" in bound:
                    over = (
                        current != 0
                        and delta / abs(float(current)) * 100 > bound["percent"]
                    )
                else:
                    over = delta > bound["absolute"]
            except (TypeError, ValueError):
                over = True
        if over:
            held.append(
                {
                    **change,
                    "class": "OVER_GUARDRAIL",
                    "detail": f"change from {current} to {proposed} exceeds {guardrail}",
                }
            )
        else:
            kept.append(dict(change))
    content = {**proposal, "changes": kept, "held": held, "guardrail": guardrail}
    return StepOutput(content=content, success=True, stop=not kept and not held)


async def apply_per_run_cap(step_input: StepInput) -> StepOutput:
    """At most N writes per run, from the ``per-run cap`` slot (``50``, ``"50 records"``,
    ``"10 SKUs"``). Everything past the cap is held with ``PER_RUN_CAP`` and reported, not
    dropped: the person sees what the cap held back and can raise it. Order of arrival is
    kept, so the oldest documents go first.
    """
    cap = first_int(slot(step_input, "per-run cap"), default=None)
    proposal = _proposal_of(step_input)
    if proposal is not None:
        changes = list(proposal.get("changes") or [])
        held = list(proposal.get("held") or [])
        if cap is not None and len(changes) > cap:
            for change in changes[cap:]:
                held.append(
                    {
                        **change,
                        "class": "PER_RUN_CAP",
                        "detail": f"held: per-run cap of {cap} reached",
                    }
                )
            changes = changes[:cap]
        return StepOutput(
            content={**proposal, "changes": changes, "held": held, "per_run_cap": cap},
            success=True,
            stop=not changes and not held,
        )

    batch = batch_from(
        step_input, step_names=("Build record", "build_record") + _DOCUMENT_STEPS
    )
    if batch is None:
        return StepOutput(
            content={**empty_batch(), "notes": "no batch to cap"},
            success=True,
            stop=True,
        )
    documents = list(batch["documents"])
    if cap is not None and len(documents) > cap:
        for document in documents[cap:]:
            add_exception(
                batch,
                document,
                cls="PER_RUN_CAP",
                detail=f"held: per-run cap of {cap} reached",
                stage="cap",
            )
        documents = documents[:cap]
    batch["documents"] = documents
    batch["per_run_cap"] = cap
    nothing_left = not documents and not batch["exceptions"]
    return StepOutput(content=batch, success=True, stop=nothing_left)


async def write_to_target(step_input: StepInput) -> StepOutput:
    """Create each built record in the write target, or under the dry-run flag report what
    would have been created (``status: previewed``) without touching it -- the same flag
    ``deliver_in_app`` reads. A write that fails is an exception on that document
    (``WRITE_FAILED``) and the rest of the batch still goes; a partial run is reported as
    exactly that, never rolled forward or hidden.

    Reads the ``write target`` slot: ``{"type": "salesforce", login_url, client_id,
    client_secret}`` (the ``SF_*`` environment fills gaps) or a bare name. Salesforce is the
    one target v1 can write; a proposal for a target the platform has not connected is
    reported ``NOT_CONNECTED``.
    """
    dry_run = is_dry_run(step_input)
    target = slot(step_input, "write target", "salesforce")
    target_name = str(
        (target.get("type") if isinstance(target, Mapping) else target) or "salesforce"
    )
    proposal = _proposal_of(step_input)
    if proposal is not None:
        status = "previewed" if dry_run else "NOT_CONNECTED"
        return StepOutput(
            content={
                **proposal,
                "write": {
                    "target": target_name,
                    "status": status,
                    "count": len(proposal.get("changes") or []),
                },
            },
            success=True,
        )

    batch = batch_from(
        step_input,
        step_names=(
            "Apply change limit",
            "apply_per_run_cap",
            "Build record",
            "build_record",
        ),
    )
    if batch is None:
        return StepOutput(
            content={**empty_batch(), "notes": "no batch to write"}, success=True
        )
    documents = list(batch["documents"])
    if dry_run or not documents:
        for document in documents:
            document["written"] = {
                "status": "previewed",
                "target": target_name,
                "object": (document.get("record") or {}).get("object"),
            }
        batch["documents"] = documents
        batch.setdefault("stats", {})["previewed"] = len(documents)
        return StepOutput(content=batch, success=True)

    if "salesforce" not in target_name.lower():
        for document in documents:
            add_exception(
                batch,
                document,
                cls="NOT_CONNECTED",
                detail=f"{target_name} is not a target this deployment can write",
                stage="write",
            )
        batch["documents"] = []
        return StepOutput(content=batch, success=True)

    from egpt_retail.documents.salesforce import awrite_record_tree, client_from

    client = await asyncio.to_thread(client_from, target)
    survivors: List[Dict[str, Any]] = []
    try:
        for document in documents:
            document = dict(document)
            try:
                result = await awrite_record_tree(client, document["record"])
            except Exception as exc:
                add_exception(
                    batch,
                    document,
                    cls="WRITE_FAILED",
                    detail=str(exc)[:300],
                    stage="write",
                )
                continue
            document["written"] = {
                "status": "written",
                "target": target_name,
                "object": document["record"]["object"],
                "record_status": result.get("status"),
                **{k: v for k, v in result.items() if k != "status"},
            }
            survivors.append(document)
    finally:
        client.close()
    batch["documents"] = survivors
    batch.setdefault("stats", {})["written"] = len(survivors)
    return StepOutput(content=batch, success=True)


async def record_rollback_point(step_input: StepInput) -> StepOutput:
    """One ledger row per record the run created, with the reverse action the mapping
    declared (``documents/rollback.py``), so a run can be undone record by record and a
    half-finished undo is visible as one. Persisted to the control plane when
    ``EGPT_RETAIL_DB_URL`` (or ``additional_data["rollback_engine"]``) is available; kept on
    the run's own ``additional_data["rollback_points"]`` and reported ``persisted: False``
    otherwise -- never dropped. A previewed write has nothing to roll back and says so.

    Reads the ``rollback`` slot only to record the person's stated policy alongside the
    rows; the reverse action itself comes from the mapping, not from prose.
    """
    options = step_input.additional_data or {}
    policy = slot(step_input, "rollback")
    proposal = _proposal_of(step_input)
    if proposal is not None:
        return StepOutput(
            content={
                **proposal,
                "rollback": {
                    "policy": policy,
                    "points": 0,
                    "notes": "no writes to record",
                },
            },
            success=True,
        )
    batch = batch_from(step_input, step_names=("Write", "write_to_target"))
    if batch is None:
        return StepOutput(
            content={**empty_batch(), "notes": "no batch to record"}, success=True
        )
    run_id = str(options.get("run_id") or uuid.uuid4())
    use_case_id = options.get("use_case_id")
    rows: List[Dict[str, Any]] = []
    for document in batch["documents"]:
        written = document.get("written") or {}
        if written.get("status") != "written":
            continue
        record = document.get("record") or {}
        reverse = record.get("rollback") or {
            "object": record.get("object"),
            "action": "delete",
        }
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "use_case_id": use_case_id,
                "target": str(written.get("target")),
                "object": str(reverse.get("object") or record.get("object")),
                "record_id": str(written.get("id")),
                "action": str(reverse.get("action") or "delete"),
                "content": {
                    "reference": document.get("reference")
                    or (document.get("extracted") or {}).get("po_number"),
                    "po_number": (document.get("extracted") or {}).get("po_number"),
                    "written": written.get("written"),
                    "policy": policy,
                },
            }
        )
    persisted = False
    engine = options.get("rollback_engine")
    if rows and engine is None and os.environ.get("EGPT_RETAIL_DB_URL"):
        from egpt_retail.app.db import build_control_plane_engine
        from egpt_retail.app.settings import RetailSettings

        engine = build_control_plane_engine(RetailSettings())
    if rows and engine is not None:
        from egpt_retail.documents.rollback import (
            aapply_rollback_ddl,
            awrite_rollback_points,
        )

        await aapply_rollback_ddl(engine)
        await awrite_rollback_points(engine, rows)
        persisted = True
    if step_input.additional_data is None:
        step_input.additional_data = {}
    step_input.additional_data.setdefault("rollback_points", []).extend(rows)
    batch["rollback"] = {
        "policy": policy,
        "run_id": run_id,
        "points": len(rows),
        "persisted": persisted,
        "records": [
            {"object": r["object"], "id": r["record_id"], "action": r["action"]}
            for r in rows
        ],
    }
    return StepOutput(content=batch, success=True)


ACT_EXECUTORS = (
    apply_hard_limits,
    apply_per_run_cap,
    write_to_target,
    record_rollback_point,
)
