"""The document-to-record template's own executors (K15, K-15): receive, screen, resolve,
build, notify. The four write-side verbs the Act shape already named live in
``executors/act.py``; the Extract stage between Screen and Resolve is a model step.

Five generic verbs. None of them knows what a purchase order or a Salesforce Order is: the
extraction template, the master-data roles, the guardrail rows and the target mapping are
the slot values, read from ``additional_data`` by slot name (``executors/batch.py``). A
second document-driven use case -- a supplier invoice into a payable -- runs this same
sequence with different slot values, which is the test of direction the use-case definition
set: zero new executors.

The batch shape and the "stop only when nothing is left" rule are documented in
``executors/batch.py``. Every stage is deterministic; the only model call in the sequence is
the Extract stage, which is not here.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Mapping

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.documents.mailbox import (
    Message,
    fetch_messages,
    filter_from,
    mailbox_from,
    send_mail,
)
from egpt_retail.documents.mapping import build_record_tree
from egpt_retail.documents.matching import (
    resolve_customer,
    resolve_product,
    resolve_ship_to,
    thresholds_from,
)
from egpt_retail.documents.reader import PDF_MAGIC, read_document
from egpt_retail.documents.templates import template_for, validate_extraction
from egpt_retail.executors.batch import (
    add_exception,
    batch_from,
    empty_batch,
    is_dry_run,
    slot,
    strip_attachment_data,
)
from egpt_retail.executors.provenance import note_dataset_read

__all__ = [
    "DOCUMENT_EXECUTORS",
    "receive_messages",
    "screen_documents",
    "extract_documents",
    "aextract_documents",
    "resolve_entities",
    "build_record",
    "send_email",
]

log = logging.getLogger(__name__)

#: A document larger than this is an exception before anything reads it. 10 MB is well past
#: any purchase order and well short of what a mailbox will hand over as a "PDF".
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


async def receive_messages(step_input: StepInput) -> StepOutput:
    """New messages from the source mailbox that match the filter, each one carried once.

    Reads the ``source mailbox`` and ``message filter`` slots. The poll is IMAP over
    ``BODY.PEEK``; ``mark_seen`` (off in the platform's runs) is the only write this stage
    can make to the mailbox, and ``processed_ids`` -- the message ids earlier runs carried,
    remembered by the platform -- is what keeps a message from being carried twice, so the
    mailbox is only ever read. ``additional_data["messages"]`` hands the batch in directly,
    which is how the rehearsal and the offline cookbook run the same executor over the
    sample set without a socket.

    Never stops the run: an empty poll is content (``count: 0``) so a quiet hour is visibly
    quiet. The Screen stage after it is the branch.
    """
    options = step_input.additional_data or {}
    injected = options.get("messages")
    processed = {str(i) for i in (options.get("processed_ids") or [])}
    mailbox = mailbox_from(slot(step_input, "source mailbox"))
    message_filter = filter_from(slot(step_input, "message filter"))

    messages: List[Message]
    source: str
    if injected is not None:
        messages = [
            m if isinstance(m, Message) else Message.from_dict(m) for m in injected
        ]
        source = "injected"
    else:
        messages = await asyncio.to_thread(
            fetch_messages,
            mailbox,
            message_filter,
            mark_seen=bool(options.get("mark_seen", True)),
            imap_factory=options.get("imap_factory"),
        )
        source = "imap"
    fresh = [m for m in messages if m.id not in processed]
    note_dataset_read(step_input, schema="mailbox", table=mailbox.user or source)
    content = {
        "messages": [m.as_dict(with_data=True) for m in fresh],
        "count": len(fresh),
        "skipped_as_processed": len(messages) - len(fresh),
        "mailbox": mailbox.user,
        "folder": mailbox.folder,
        "filter": {
            "unseen_only": message_filter.unseen_only,
            "subject_contains": message_filter.subject_contains,
            "since_days": message_filter.since_days,
        },
        "source": source,
    }
    return StepOutput(content=content, success=True)


def _pdf_attachments(message: Mapping[str, Any]) -> List[Dict[str, Any]]:
    found = []
    for attachment in message.get("attachments") or []:
        data = attachment.get("data")
        if data is None and attachment.get("data_b64"):
            data = base64.b64decode(attachment["data_b64"])
        data = data or b""
        is_pdf = data.startswith(PDF_MAGIC) or (
            not data
            and str(attachment.get("content_type", "")).lower() == "application/pdf"
        )
        if is_pdf or str(attachment.get("filename", "")).lower().endswith(".pdf"):
            found.append(
                {**attachment, "data": data, "is_pdf": data.startswith(PDF_MAGIC)}
            )
    return found


async def screen_documents(step_input: StepInput) -> StepOutput:
    """Deterministic gate before any model is called: exactly one document of the declared
    type, under the size limit, from a sender that is not ignored, with a text layer.

    Reads the ``document type`` slot (``pdf`` is the only type v1 reads). Every message
    becomes either a document -- its text read here, once, by ``document_read`` -- or an
    exception naming why: ``NO_DOCUMENT``, ``MULTIPLE_DOCUMENTS``, ``NOT_A_PDF``,
    ``TOO_LARGE``, ``SENDER_IGNORED`` (the deployment's own domain, ``ignore_domains``),
    ``SCANNED_DOCUMENT`` (no text layer; OCR is a v1 non-goal, see ``documents/reader.py``).

    Stops the run only when there were no messages at all.
    """
    options = step_input.additional_data or {}
    previous = step_input.previous_step_content
    messages: List[Mapping[str, Any]] = (
        list((previous or {}).get("messages") or [])
        if isinstance(previous, Mapping)
        else []
    )
    document_type = str(slot(step_input, "document type", "pdf") or "pdf").lower()
    max_bytes = int(options.get("max_bytes") or DEFAULT_MAX_BYTES)
    ignore_domains = {str(d).lower() for d in (options.get("ignore_domains") or [])}
    known_senders = {str(d).lower() for d in (options.get("known_senders") or [])}

    batch = empty_batch()
    for message in messages:
        document: Dict[str, Any] = {"message": strip_attachment_data(message)}
        domain = str(
            message.get("sender_domain") or message.get("sender", "").rsplit("@", 1)[-1]
        ).lower()
        if domain in ignore_domains:
            add_exception(
                batch,
                document,
                cls="SENDER_IGNORED",
                detail=f"sent from {domain}",
                stage="screen",
            )
            continue
        if known_senders and domain not in known_senders:
            add_exception(
                batch,
                document,
                cls="UNKNOWN_SENDER",
                detail=f"{domain} is not a known sender",
                stage="screen",
            )
            continue
        if document_type != "pdf":
            add_exception(
                batch,
                document,
                cls="UNSUPPORTED_DOCUMENT_TYPE",
                detail=f"v1 reads PDF only, not {document_type}",
                stage="screen",
            )
            continue
        pdfs = _pdf_attachments(message)
        if not pdfs:
            add_exception(
                batch,
                document,
                cls="NO_DOCUMENT",
                detail="no PDF attachment",
                stage="screen",
            )
            continue
        if len(pdfs) > 1:
            add_exception(
                batch,
                document,
                cls="MULTIPLE_DOCUMENTS",
                detail=f"{len(pdfs)} PDF attachments; expected one",
                stage="screen",
            )
            continue
        attachment = pdfs[0]
        if not attachment["is_pdf"]:
            add_exception(
                batch,
                document,
                cls="NOT_A_PDF",
                detail=f"{attachment.get('filename')} has no PDF header",
                stage="screen",
            )
            continue
        if len(attachment["data"]) > max_bytes:
            add_exception(
                batch,
                document,
                cls="TOO_LARGE",
                detail=f"{len(attachment['data'])} bytes over the {max_bytes} limit",
                stage="screen",
            )
            continue
        try:
            text = read_document(
                attachment["data"],
                filename=str(attachment.get("filename") or "document.pdf"),
            )
        except (
            Exception
        ) as exc:  # a corrupt PDF is an exception on the document, not a crash
            add_exception(
                batch,
                document,
                cls="UNREADABLE_DOCUMENT",
                detail=str(exc)[:200],
                stage="screen",
            )
            continue
        if text.scanned:
            add_exception(
                batch,
                document,
                cls="SCANNED_DOCUMENT",
                detail=f"{text.filename}: {text.pages} page(s), no text layer (OCR not enabled)",
                stage="screen",
            )
            continue
        document["document"] = text.as_dict()
        batch["documents"].append(document)

    batch["stats"] = {
        "received": len(messages),
        "screened_in": len(batch["documents"]),
        "screened_out": len(batch["exceptions"]),
    }
    nothing_left = not messages
    return StepOutput(content=batch, success=True, stop=nothing_left)


Extractor = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def extract_documents(
    batch: Dict[str, Any], extractor: Extractor, *, template_id: Any = "purchase_order"
) -> Dict[str, Any]:
    """Fill ``extracted`` on every document with ``extractor(document)``, validated against
    the template. Not an executor: the Extract stage is a model step, and this is the
    deterministic half of it -- the validation -- exposed so the rehearsal can run the same
    validation over a stand-in extractor and the cookbook over a real model call."""
    template = template_for(template_id)
    survivors: List[Dict[str, Any]] = []
    for document in batch.get("documents") or []:
        try:
            raw = extractor(document)
        except Exception as exc:
            add_exception(
                batch,
                document,
                cls="EXTRACTION_FAILED",
                detail=str(exc)[:200],
                stage="extract",
            )
            continue
        clean, problems = validate_extraction(template, raw)
        document = dict(document)
        document["extracted"] = clean
        document["extraction_problems"] = problems
        if problems:
            add_exception(
                batch,
                document,
                cls="EXTRACTION_INCOMPLETE",
                detail="; ".join(problems)[:300],
                stage="extract",
            )
            continue
        survivors.append(document)
    batch["documents"] = survivors
    return batch


AsyncExtractor = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


async def aextract_documents(
    batch: Dict[str, Any],
    extractor: AsyncExtractor,
    *,
    template_id: Any = "purchase_order",
) -> Dict[str, Any]:
    """:func:`extract_documents` with an awaitable extractor -- the model step runs on the
    event loop, one document at a time, without blocking the server between calls."""
    template = template_for(template_id)
    survivors: List[Dict[str, Any]] = []
    for document in batch.get("documents") or []:
        try:
            raw = await extractor(document)
        except Exception as exc:
            add_exception(
                batch,
                document,
                cls="EXTRACTION_FAILED",
                detail=str(exc)[:200],
                stage="extract",
            )
            continue
        clean, problems = validate_extraction(template, raw)
        document = dict(document)
        document["extracted"] = clean
        document["extraction_problems"] = problems
        if problems:
            add_exception(
                batch,
                document,
                cls="EXTRACTION_INCOMPLETE",
                detail="; ".join(problems)[:300],
                stage="extract",
            )
            continue
        survivors.append(document)
    batch["documents"] = survivors
    return batch


def _merge_model_extractions(
    batch: Dict[str, Any], previous: Any, template_id: Any
) -> Dict[str, Any]:
    """When the previous step was the model, its output is ``{"extractions": [{message_id,
    ...fields}]}``; attach each to its document by message id and validate."""
    if not isinstance(previous, Mapping) or "extractions" not in previous:
        return batch
    by_id: Dict[str, Mapping[str, Any]] = {}
    for item in previous.get("extractions") or []:
        if isinstance(item, Mapping) and item.get("message_id"):
            by_id[str(item["message_id"])] = item

    def extractor(document: Mapping[str, Any]) -> Mapping[str, Any]:
        message_id = str((document.get("message") or {}).get("id"))
        if message_id not in by_id:
            raise ValueError("the model returned no extraction for this document")
        return {k: v for k, v in by_id[message_id].items() if k != "message_id"}

    return extract_documents(batch, extractor, template_id=template_id)


async def resolve_entities(step_input: StepInput) -> StepOutput:
    """Scored matching of the extracted fields against master data, in a fixed order of
    attempts per role (``documents/matching.py``): customer, then the customer's ship-to,
    then each line's product. Below threshold is an exception with the closest candidate
    (``CUSTOMER_NOT_FOUND``, ``SHIP_TO_NOT_FOUND``, ``SHIP_TO_AMBIGUOUS``,
    ``PRODUCT_NOT_FOUND``, ``PRODUCT_AMBIGUOUS``); a customer the sender does not identify
    is not an exception here -- that is the ``unknown_sender`` guardrail's call.

    Reads the ``master data`` slot: the normalised rows directly, or ``{"source":
    "salesforce", ...}`` to load them through ``documents/salesforce.py`` (the write
    target's credentials fill any gap), and ``match thresholds``. ``additional_data
    ["extractor"]`` fills ``extracted`` for documents that lack it -- the rehearsal's
    stand-in for the model step -- and a model step's own ``{"extractions": [...]}`` output
    immediately before this one is merged by message id.
    """
    options = step_input.additional_data or {}
    batch = batch_from(step_input, step_names=("Screen", "screen_documents"))
    if batch is None:
        return StepOutput(
            content={**empty_batch(), "notes": "no batch to resolve"},
            success=True,
            stop=True,
        )
    template_id = slot(step_input, "extraction template", "purchase_order")
    batch = _merge_model_extractions(
        batch, step_input.previous_step_content, template_id
    )
    extractor = options.get("extractor")
    if extractor is not None and any("extracted" not in d for d in batch["documents"]):
        batch = extract_documents(batch, extractor, template_id=template_id)

    master = await _master_data(step_input)
    thresholds = thresholds_from(slot(step_input, "match thresholds"))
    survivors: List[Dict[str, Any]] = []
    for document in batch["documents"]:
        document = dict(document)
        if "extracted" not in document:
            add_exception(
                batch,
                document,
                cls="EXTRACTION_MISSING",
                detail="nothing extracted for this document",
                stage="resolve",
            )
            continue
        extracted = document["extracted"]
        message = document.get("message") or {}
        sender = str(message.get("sender") or "")
        resolved: Dict[str, Any] = {}
        failures: List[str] = []

        customer = resolve_customer(
            master,
            sender=sender,
            customer_name=extracted.get("customer_name"),
            thresholds=thresholds,
        )
        resolved["customer"] = customer.as_dict()
        if not customer.resolved:
            # A sender whose domain is nobody's and whose name matches nobody is, to the
            # person reading the exception, an unknown sender -- the same class the
            # guardrail of that name raises, so the mailbox shows one class, not two.
            cls = "UNKNOWN_SENDER" if "@" in sender else "CUSTOMER_NOT_FOUND"
            failures.append(
                f"{cls}: {customer.reason}"
                + (
                    f" (closest: {customer.closest.get('name')})"
                    if customer.closest
                    else ""
                )
            )

        if customer.resolved and customer.row is not None:
            ship_to = resolve_ship_to(
                master,
                account_id=customer.row.get("id"),
                address=extracted.get("ship_to_address"),
                thresholds=thresholds,
            )
            resolved["ship_to"] = {**ship_to.as_dict(), "row": ship_to.row}
            if not ship_to.resolved:
                cls = (
                    "SHIP_TO_AMBIGUOUS"
                    if "within" in ship_to.reason
                    or "does not say which" in ship_to.reason
                    else "SHIP_TO_NOT_FOUND"
                )
                failures.append(f"{cls}: {ship_to.reason}")
            domain = str(
                customer.row.get("email_domain") or message.get("sender_domain") or ""
            )
        else:
            domain = str(message.get("sender_domain") or "")

        lines: List[Dict[str, Any]] = []
        for line in extracted.get("lines") or []:
            product = resolve_product(
                master,
                customer_domain=domain,
                customer_part_number=line.get("customer_part_number"),
                supplier_product_code=line.get("supplier_product_code"),
                description=line.get("description"),
                thresholds=thresholds,
            )
            entry = dict(line)
            entry["product"] = product.as_dict()
            if product.row is not None:
                entry["product"].update(
                    {
                        k: product.row.get(k)
                        for k in (
                            "code",
                            "uom",
                            "restricted",
                            "active",
                            "pricebook_entry_id",
                            "unit_price",
                        )
                    }
                )
            elif "no part number" not in product.reason:
                cls = (
                    "PRODUCT_AMBIGUOUS"
                    if "within" in product.reason
                    else "PRODUCT_NOT_FOUND"
                )
                failures.append(
                    f"{cls}: line {line.get('line_no')} {line.get('description')!r}: {product.reason}"
                    + (
                        f" (closest: {product.closest.get('name')})"
                        if product.closest
                        else ""
                    )
                )
            else:
                failures.append(
                    f"PRODUCT_NOT_FOUND: line {line.get('line_no')}: {product.reason}"
                )
            lines.append(entry)
        resolved["lines"] = lines
        document["resolved"] = resolved
        if failures:
            add_exception(
                batch,
                document,
                cls=failures[0].split(":", 1)[0],
                detail="; ".join(failures)[:400],
                stage="resolve",
            )
            continue
        survivors.append(document)
    batch["documents"] = survivors
    batch.setdefault("stats", {})["resolved"] = len(survivors)
    batch["master_data_roles"] = sorted(k for k in master if k != "pricebook_id")
    batch["pricebook_id"] = master.get("pricebook_id")
    nothing_left = not survivors and not batch["exceptions"]
    return StepOutput(content=batch, success=True, stop=nothing_left)


async def _master_data(step_input: StepInput) -> Dict[str, Any]:
    """The master-data rows for this run: injected rows, or loaded from the named source."""
    value = slot(step_input, "master data")
    options = step_input.additional_data or {}
    if isinstance(value, Mapping) and any(
        k in value for k in ("customer", "product", "ship_to")
    ):
        rows = dict(value)
    elif isinstance(options.get("master_data_rows"), Mapping):
        rows = dict(options["master_data_rows"])
    else:
        source = str(
            (value or {}).get("source")
            if isinstance(value, Mapping)
            else value or "salesforce"
        ).lower()
        if "salesforce" not in source:
            raise ValueError(
                f"master data source {source!r} is not one this deployment can load"
            )
        from egpt_retail.documents.salesforce import aload_master_data, client_from

        roles = (value or {}).get("roles") if isinstance(value, Mapping) else None
        credentials = (
            value if isinstance(value, Mapping) else slot(step_input, "write target")
        )
        client = await asyncio.to_thread(client_from, credentials)
        try:
            rows = await aload_master_data(client, roles=roles)
        finally:
            client.close()
        for role in rows:
            if role != "pricebook_id":
                note_dataset_read(step_input, schema="salesforce", table=role)
    return rows


async def build_record(step_input: StepInput) -> StepOutput:
    """Maps each resolved document onto the target's objects through the mapping the
    ``target objects`` slot names (``documents/mapping.py``), created as the ``created as``
    slot says. A required field the mapping cannot fill is an exception
    (``RECORD_INCOMPLETE``) naming the field, never a blank on the record.
    """
    batch = batch_from(
        step_input,
        step_names=(
            "Apply guardrails",
            "apply_hard_limits",
            "Resolve",
            "resolve_entities",
        ),
    )
    if batch is None:
        return StepOutput(
            content={**empty_batch(), "notes": "no batch to build from"}, success=True
        )
    target = slot(step_input, "write target", "salesforce")
    target_system = str(
        (target.get("type") if isinstance(target, Mapping) else target) or "salesforce"
    )
    target_system = (
        "salesforce" if "salesforce" in target_system.lower() else target_system.lower()
    )
    objects = str(slot(step_input, "target objects", "order_orderitem"))
    created_as = slot(step_input, "created as", "draft")
    options = {
        "created_as": created_as,
        "pricebook_id": batch.get("pricebook_id")
        or (step_input.additional_data or {}).get("pricebook_id"),
    }

    survivors: List[Dict[str, Any]] = []
    for document in batch["documents"]:
        document = dict(document)
        try:
            tree, missing = build_record_tree(target_system, objects, document, options)
        except KeyError as exc:
            add_exception(
                batch, document, cls="NO_MAPPING", detail=str(exc), stage="build"
            )
            continue
        document["record"] = tree
        if missing:
            add_exception(
                batch,
                document,
                cls="RECORD_INCOMPLETE",
                detail="; ".join(missing)[:400],
                stage="build",
            )
            continue
        survivors.append(document)
    batch["documents"] = survivors
    batch.setdefault("stats", {})["built"] = len(survivors)
    return StepOutput(content=batch, success=True)


def _summary_lines(batch: Mapping[str, Any]) -> List[str]:
    documents = batch.get("documents") or []
    exceptions = batch.get("exceptions") or []
    lines = [
        f"Run summary, {date.today().isoformat()}",
        f"Received: {(batch.get('stats') or {}).get('received', len(documents) + len(exceptions))}",
        f"Created: {sum(1 for d in documents if (d.get('written') or {}).get('status') == 'written')}",
        f"Previewed (dry run): {sum(1 for d in documents if (d.get('written') or {}).get('status') == 'previewed')}",
        f"Exceptions: {len(exceptions)}",
        "",
    ]
    if documents:
        lines.append("Created records")
        for document in documents:
            written = document.get("written") or {}
            extracted = document.get("extracted") or {}
            customer = ((document.get("resolved") or {}).get("customer") or {}).get(
                "name"
            )
            lines.append(
                f"  PO {extracted.get('po_number')} for {customer}: "
                f"{written.get('status', 'built')} {written.get('url') or written.get('id') or ''}".rstrip()
            )
        lines.append("")
    if exceptions:
        lines.append("Exceptions, by class")
        by_class: Dict[str, List[Mapping[str, Any]]] = {}
        for row in exceptions:
            by_class.setdefault(str(row.get("class")), []).append(row)
        for cls, rows in sorted(by_class.items()):
            lines.append(f"  {cls} ({len(rows)})")
            for row in rows:
                lines.append(
                    f"    {row.get('subject')} from {row.get('sender')}: {row.get('detail')}"
                )
    return lines


async def send_email(step_input: StepInput) -> StepOutput:
    """The run's exceptions and created records to the ``recipient`` slot, through the
    source mailbox's own SMTP. Under the dry-run flag the message is composed and returned,
    not sent -- the one side effect this stage has, held back the same way ``deliver_in_app``
    holds its delivery. Nothing to say (no documents, no exceptions) sends nothing and says
    so in its content.
    """
    options = step_input.additional_data or {}
    batch = (
        batch_from(
            step_input,
            step_names=(
                "Record rollback point",
                "record_rollback_point",
                "Write",
                "write_to_target",
            ),
        )
        or empty_batch()
    )
    recipient = slot(step_input, "recipient")
    recipients = [
        r.strip()
        for r in str(recipient or "").replace(";", ",").split(",")
        if r.strip()
    ]
    if not recipients:
        return StepOutput(
            content={
                "status": "no_recipient",
                "batch": batch,
                "notes": "recipient slot is empty; nothing sent",
            },
            success=False,
        )
    documents = batch.get("documents") or []
    exceptions = batch.get("exceptions") or []
    subject = (
        f"[Enterprise GPT] {len(documents)} created, {len(exceptions)} exception(s)"
    )
    body = "\n".join(_summary_lines(batch))
    if not documents and not exceptions:
        return StepOutput(
            content={"status": "nothing_to_report", "to": recipients, "batch": batch},
            success=True,
        )
    if is_dry_run(step_input):
        return StepOutput(
            content={
                "status": "previewed",
                "to": recipients,
                "subject": subject,
                "body": body,
                "batch": batch,
            },
            success=True,
        )
    mailbox = mailbox_from(slot(step_input, "source mailbox"))
    sent = await asyncio.to_thread(
        send_mail,
        mailbox,
        to=recipients,
        subject=subject,
        body=body,
        smtp_factory=options.get("smtp_factory"),
    )
    return StepOutput(
        content={"status": "sent", **sent, "body": body, "batch": batch}, success=True
    )


DOCUMENT_EXECUTORS = (
    receive_messages,
    screen_documents,
    resolve_entities,
    build_record,
    send_email,
)
