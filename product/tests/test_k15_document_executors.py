"""K15 / K-15: the document-to-record executors and the Act shape's four, offline.

No mailbox, no Salesforce, no model, no database: the sample set (``documents/samples.py``)
is injected as ``additional_data["messages"]``, the expected extraction stands in for the
model step, master data is the sample rows, and every write is under the dry-run flag or a
recording transport. What is asserted is the behaviour the use-case definition promised:
sixteen emails, eight created, one exception per guardrail and per resolution failure, one
scanned copy refused before a model is called.
"""

from __future__ import annotations

import asyncio
import email
import json
from datetime import date, timedelta
from typing import Any, Dict, List

import httpx
import pytest
from egpt.workflow.types import StepInput

from egpt_retail.app.components import build_registry
from egpt_retail.documents.guardrails import (
    RECORD_GUARDRAILS,
    check_document,
    guardrails_from,
)
from egpt_retail.documents.mailbox import (
    Attachment,
    Message,
    MessageFilter,
    MailboxConfig,
    fetch_messages,
    parse_message,
)
from egpt_retail.documents.mapping import build_record_tree
from egpt_retail.documents.matching import (
    Thresholds,
    resolve_customer,
    resolve_product,
    resolve_ship_to,
    score_text,
)
from egpt_retail.documents.pdf import image_only_pdf, text_pdf
from egpt_retail.documents.reader import read_document
from egpt_retail.documents.salesforce import (
    SalesforceClient,
    load_master_data,
    write_record_tree,
)
from egpt_retail.documents.samples import (
    SAMPLE_EMAILS,
    expected_extraction,
    expected_outcome,
    sample_master_data,
    sample_messages,
)
from egpt_retail.documents.templates import template_for, validate_extraction
from egpt_retail.executors.act import (
    apply_hard_limits,
    apply_per_run_cap,
    record_rollback_point,
    write_to_target,
)
from egpt_retail.executors.documents import (
    build_record,
    receive_messages,
    resolve_entities,
    screen_documents,
    send_email,
)
from egpt_retail.executors.metadata import EXECUTOR_METADATA
from egpt_retail.orchestration.compiler import TEMPLATE_STAGES, capability_report
from egpt_retail.contracts.use_case import UseCaseTemplate

CHAIN = (
    receive_messages,
    screen_documents,
    resolve_entities,
    apply_hard_limits,
    build_record,
    apply_per_run_cap,
    write_to_target,
    record_rollback_point,
    send_email,
)


def _extractor(document: Dict[str, Any]) -> Dict[str, Any]:
    return expected_extraction(document["message"]["id"][1:5].upper())


def _options(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "messages": sample_messages(),
        "source mailbox": {"user": "orders@northwind.example", "password": "x"},
        "document type": "pdf",
        "extraction template": "purchase_order",
        "master data": sample_master_data(),
        "match thresholds": {
            "address": 0.75,
            "product": 0.85,
            "name": 0.8,
            "ambiguity": 0.05,
        },
        "guardrails": "all",
        "target objects": "order_orderitem",
        "created as": "draft",
        "per-run cap": "50 records",
        "write target": {"type": "salesforce"},
        "recipient": "ops@northwind.example",
        "rollback": "Cancel the record and remove its lines",
        "extractor": _extractor,
        "dry_run": True,
    }
    base.update(overrides)
    return base


def _run_chain(options: Dict[str, Any], executors=CHAIN) -> List[Any]:
    async def go() -> List[Any]:
        outputs = []
        previous = None
        for executor in executors:
            output = await executor(
                StepInput(previous_step_content=previous, additional_data=options)
            )
            outputs.append(output)
            previous = output.content
        return outputs

    return asyncio.run(go())


def _outcomes(batch: Dict[str, Any]) -> Dict[str, str]:
    got: Dict[str, str] = {}
    for document in batch["documents"]:
        got[document["message"]["id"][1:5].upper()] = "created"
    for row in batch["exceptions"]:
        got[row["message_id"][1:5].upper()] = row["class"]
    return got


# --- registry and stage table ---------------------------------------------------------


def test_every_document_stage_has_a_registered_executor_with_metadata() -> None:
    names = {f.__name__ for f in build_registry().functions}
    for stage in TEMPLATE_STAGES[UseCaseTemplate.DOCUMENT_TO_RECORD]:
        if stage.executor_ref:
            assert stage.executor_ref in names, stage.executor_ref
            assert stage.executor_ref in EXECUTOR_METADATA
    report = capability_report(names)
    assert report["act/document_to_record"]["missing"] == {}
    assert report["act"]["missing"] == {"Select candidates": "select_candidates"}


def test_permits_or_blocks_marks_exactly_the_branches() -> None:
    blocking = {n for n, m in EXECUTOR_METADATA.items() if m.permits_or_blocks}
    assert {
        "screen_documents",
        "resolve_entities",
        "apply_hard_limits",
        "apply_per_run_cap",
    } <= blocking
    assert {
        "receive_messages",
        "build_record",
        "write_to_target",
        "record_rollback_point",
        "send_email",
    }.isdisjoint(blocking)


# --- the sample set end to end ----------------------------------------------------------


def test_sixteen_samples_land_where_the_definition_says() -> None:
    outputs = _run_chain(_options())
    batch = outputs[-1].content["batch"]
    got = _outcomes(batch)
    expected = {e["id"]: expected_outcome(e["id"]) for e in SAMPLE_EMAILS}
    assert got == expected
    assert sum(1 for v in got.values() if v == "created") == 7
    assert all(not o.stop for o in outputs), (
        "a batch with exceptions never stops the run"
    )
    assert outputs[-1].content["status"] == "previewed"
    assert "DUPLICATE_PO (1)" in outputs[-1].content["body"]
    assert all(d["written"]["status"] == "previewed" for d in batch["documents"])
    assert batch["rollback"] == {
        "policy": "Cancel the record and remove its lines",
        "run_id": batch["rollback"]["run_id"],
        "points": 0,
        "persisted": False,
        "records": [],
    }


def test_scanned_copy_is_refused_before_extraction_and_the_extractor_never_sees_it() -> (
    None
):
    seen: List[str] = []

    def extractor(document: Dict[str, Any]) -> Dict[str, Any]:
        seen.append(document["message"]["id"])
        return _extractor(document)

    outputs = _run_chain(_options(extractor=extractor), CHAIN[:3])
    assert "<e-08@sample.northwind.example>" not in seen
    scanned = [
        e for e in outputs[1].content["exceptions"] if e["class"] == "SCANNED_DOCUMENT"
    ]
    assert len(scanned) == 1 and "no text layer" in scanned[0]["detail"]


def test_dropping_a_guardrail_changes_the_outcome() -> None:
    enabled = [g for g in RECORD_GUARDRAILS if g != "first_time_product"]
    got = _outcomes(_run_chain(_options(guardrails=enabled))[-1].content["batch"])
    assert got["E-11"] == "created"
    assert got["E-14"] == "RESTRICTED_PRODUCT", (
        "the restricted line is still caught on its own"
    )


def test_thresholds_are_the_form_s_and_lowering_one_resolves_more() -> None:
    got = _outcomes(
        _run_chain(_options(**{"match thresholds": {"product": 0.7}}))[-1].content[
            "batch"
        ]
    )
    assert got["E-15"] == "created", "0.81 for 'Solvent, drum' clears 0.70"


def test_per_run_cap_holds_the_overflow_in_arrival_order() -> None:
    outputs = _run_chain(_options(**{"per-run cap": "3 records"}))
    batch = outputs[-1].content["batch"]
    held = [e for e in batch["exceptions"] if e["class"] == "PER_RUN_CAP"]
    assert len(held) == 4
    assert [d["message"]["id"][1:5].upper() for d in batch["documents"]] == [
        "E-01",
        "E-02",
        "E-03",
    ]


def test_empty_poll_is_content_and_screen_stops_the_run() -> None:
    outputs = _run_chain(_options(messages=[]), CHAIN[:2])
    assert outputs[0].content["count"] == 0 and not outputs[0].stop
    assert outputs[1].stop and outputs[1].content == {
        "documents": [],
        "exceptions": [],
        "stats": {"received": 0, "screened_in": 0, "screened_out": 0},
    }


def test_processed_ids_drop_repeats() -> None:
    messages = sample_messages()
    output = asyncio.run(
        receive_messages(
            StepInput(additional_data=_options(processed_ids=[messages[0].id]))
        )
    )
    assert output.content["count"] == 15 and output.content["skipped_as_processed"] == 1


def test_screen_refuses_wrong_shapes_before_any_model() -> None:
    pdf = text_pdf(["PO 1"])
    messages = [
        Message("<none@x>", "a@bristolcoatings.example", "no attachment", "", "", []),
        Message(
            "<two@x>",
            "a@bristolcoatings.example",
            "two",
            "",
            "",
            [
                Attachment("a.pdf", "application/pdf", len(pdf), pdf),
                Attachment("b.pdf", "application/pdf", len(pdf), pdf),
            ],
        ),
        Message(
            "<fake@x>",
            "a@bristolcoatings.example",
            "fake",
            "",
            "",
            [Attachment("a.pdf", "application/pdf", 3, b"abc")],
        ),
        Message(
            "<big@x>",
            "a@bristolcoatings.example",
            "big",
            "",
            "",
            [Attachment("a.pdf", "application/pdf", len(pdf), pdf)],
        ),
        Message(
            "<own@x>",
            "a@northwind.example",
            "own",
            "",
            "",
            [Attachment("a.pdf", "application/pdf", len(pdf), pdf)],
        ),
        Message(
            "<ok@x>",
            "a@bristolcoatings.example",
            "ok",
            "",
            "",
            [Attachment("a.pdf", "application/pdf", len(pdf), pdf)],
        ),
    ]
    output = asyncio.run(
        screen_documents(
            StepInput(
                previous_step_content={"messages": [m.as_dict() for m in messages]},
                additional_data={
                    "document type": "pdf",
                    "max_bytes": 100_000,
                    "ignore_domains": ["northwind.example"],
                },
            )
        )
    )
    classes = {e["message_id"]: e["class"] for e in output.content["exceptions"]}
    assert classes == {
        "<none@x>": "NO_DOCUMENT",
        "<two@x>": "MULTIPLE_DOCUMENTS",
        "<fake@x>": "NOT_A_PDF",
        "<own@x>": "SENDER_IGNORED",
    }
    assert [d["message"]["id"] for d in output.content["documents"]] == [
        "<big@x>",
        "<ok@x>",
    ]
    assert "data_b64" not in json.dumps(output.content["documents"][0]["message"])


def test_model_extractions_are_merged_by_message_id_and_validated() -> None:
    screened = _run_chain(_options(), CHAIN[:2])[-1].content
    good = {
        "message_id": "<e-01@sample.northwind.example>",
        **expected_extraction("E-01"),
    }
    incomplete = {
        "message_id": "<e-02@sample.northwind.example>",
        **expected_extraction("E-02"),
    }
    incomplete["po_number"] = ""  # a required header field the model left blank
    incomplete["made_up"] = "x"  # a field the template does not declare
    options = _options()
    options.pop("extractor")
    output = asyncio.run(
        resolve_entities(
            StepInput(
                previous_step_content={"extractions": [good, incomplete]},
                previous_step_outputs={
                    "Screen": type("O", (), {"content": screened, "steps": None})()
                },
                additional_data=options,
            )
        )
    )
    got = {
        e["message_id"]: (e["class"], e["detail"]) for e in output.content["exceptions"]
    }
    assert got["<e-02@sample.northwind.example>"][0] == "EXTRACTION_INCOMPLETE"
    assert (
        "po_number" in got["<e-02@sample.northwind.example>"][1]
        and "made_up" in got["<e-02@sample.northwind.example>"][1]
    )
    assert got["<e-03@sample.northwind.example>"] == (
        "EXTRACTION_FAILED",
        "the model returned no extraction for this document",
    )
    assert [d["message"]["id"] for d in output.content["documents"]] == [
        "<e-01@sample.northwind.example>"
    ]


# --- the pieces --------------------------------------------------------------------------


def test_pdf_writer_round_trips_through_the_reader() -> None:
    text = read_document(
        text_pdf(["PURCHASE ORDER", "Ship To: 386 Metacom Avenue (Bristol)"] * 30),
        "po.pdf",
    )
    assert (
        text.pages == 2
        and not text.scanned
        and "386 Metacom Avenue (Bristol)" in text.text
    )
    scanned = read_document(image_only_pdf(), "scan.pdf")
    assert scanned.scanned and scanned.text == "" and scanned.pages == 1
    with pytest.raises(ValueError):
        read_document(b"not a pdf")


def test_validate_extraction_keeps_the_template_s_shape() -> None:
    template = template_for("Purchase order")
    clean, problems = validate_extraction(
        template,
        {
            "po_number": "1",
            "lines": [{"description": "x", "quantity": "-2", "unit_of_measure": "EA"}],
        },
    )
    assert clean["customer_name"] is None and clean["lines"][0]["line_no"] == 1
    assert "required header field 'customer_name' missing" in problems
    assert "line 1: quantity must be positive" in problems
    assert validate_extraction(template, "nonsense")[1] == [
        "extraction output is not an object"
    ]


def test_matching_order_of_attempts() -> None:
    master = sample_master_data()
    t = Thresholds()
    by_domain = resolve_customer(
        master,
        sender="anyone@lycomingfab.example",
        customer_name="Nobody",
        thresholds=t,
    )
    assert by_domain.method == "sender_domain" and by_domain.row["id"] == "A-002"
    by_name = resolve_customer(
        master,
        sender="x@other.example",
        customer_name="Lycoming Fabrication",
        thresholds=t,
    )
    assert (
        by_name.method == "name" and by_name.resolved and by_name.row["id"] == "A-002"
    )
    nobody = resolve_customer(
        master, sender="x@other.example", customer_name="Acme", thresholds=t
    )
    assert not nobody.resolved and nobody.closest is not None

    only = resolve_ship_to(master, account_id="A-002", address=None, thresholds=t)
    assert only.method == "only_site"
    tie = resolve_ship_to(
        master,
        account_id="A-003",
        address="9 Pier Ln, New Bedford, MA 02740",
        thresholds=t,
    )
    assert not tie.resolved and "does not say which" in tie.reason
    exact = resolve_ship_to(
        master,
        account_id="A-003",
        address="9 Pier Lane, Unit B, New Bedford, MA 02740",
        thresholds=t,
    )
    assert exact.resolved and exact.row["id"] == "S-005"

    part = resolve_product(
        master,
        customer_domain="bristolcoatings.example",
        customer_part_number="306727",
        supplier_product_code=None,
        description="whatever",
        thresholds=t,
    )
    assert part.method == "customer_part_number" and part.row["id"] == "P-110"
    other = resolve_product(
        master,
        customer_domain="lycomingfab.example",
        customer_part_number="306727",
        supplier_product_code=None,
        description="Xylene Solvent, 55 gal drum",
        thresholds=t,
    )
    assert other.method == "description", "another customer's part number is not ours"
    alias = resolve_product(
        master,
        customer_domain="",
        customer_part_number=None,
        supplier_product_code=None,
        description="MEK 55 gal drum",
        thresholds=t,
    )
    assert alias.resolved and alias.row["id"] == "P-150"
    assert score_text("Solvent, drum", "Xylene Solvent, 55 gal drum") < 0.85


def test_guardrails_priority_skipped_and_within_run_duplicate() -> None:
    master = sample_master_data()
    enabled = guardrails_from(
        [
            {"id": "first_time_product"},
            {"id": "restricted_product"},
            {"id": "duplicate_po", "param": 6},
        ]
    )
    assert [g.id for g, _ in enabled] == [
        "first_time_product",
        "restricted_product",
        "duplicate_po",
    ]
    assert enabled[2][1] == 6.0
    document = {
        "message": {"sender": "shop@neautobody.example"},
        "extracted": {"po_number": "NAB-315", "due_date": None},
        "resolved": {
            "customer": {"resolved": True, "id": "A-006", "method": "sender_domain"},
            "lines": [
                {
                    "line_no": 1,
                    "quantity": 1,
                    "product": {
                        "resolved": True,
                        "id": "P-150",
                        "name": "MEK",
                        "restricted": True,
                    },
                }
            ],
        },
    }
    verdict = check_document(document, master, enabled)
    assert [e["class"] for e in verdict["exceptions"]] == [
        "RESTRICTED_PRODUCT",
        "FIRST_TIME_PRODUCT",
    ]
    again = check_document(
        document, master, enabled, seen_this_run=[("A-006", "nab-315")]
    )
    assert "DUPLICATE_PO" in {e["class"] for e in again["exceptions"]}
    without_history = check_document(
        document, {k: v for k, v in master.items() if k != "order_history"}, enabled
    )
    assert without_history["skipped"] == ["duplicate_po", "first_time_product"]
    assert (
        guardrails_from("all")[0][0].id == "duplicate_po"
        and len(guardrails_from("all")) == 6
    )
    assert guardrails_from(["not_a_guardrail"]) == []


def test_due_date_guardrail_bounds() -> None:
    enabled = guardrails_from([{"id": "due_date_sanity", "param": 30}])
    today = date(2026, 9, 15)

    def doc(due: Any) -> Dict[str, Any]:
        return {
            "message": {},
            "extracted": {"due_date": due},
            "resolved": {"customer": {}, "lines": []},
        }

    assert (
        check_document(doc("2026-09-20"), {}, enabled, today=today)["exceptions"] == []
    )
    assert (
        check_document(doc("09/20/2026"), {}, enabled, today=today)["exceptions"] == []
    )
    assert check_document(doc("2026-09-01"), {}, enabled, today=today)["exceptions"][0][
        "detail"
    ].endswith("in the past")
    assert (
        "more than 30 days"
        in check_document(
            doc((today + timedelta(days=40)).isoformat()), {}, enabled, today=today
        )["exceptions"][0]["detail"]
    )
    assert (
        "unparseable"
        in check_document(doc("soon"), {}, enabled, today=today)["exceptions"][0][
            "detail"
        ]
    )
    assert check_document(doc(None), {}, enabled, today=today)["exceptions"] == []


def test_mapping_names_every_field_it_cannot_fill() -> None:
    document = {
        "extracted": {
            "po_number": None,
            "po_date": "2026-09-14",
            "due_date": "2026-09-28",
            "customer_name": "X",
        },
        "resolved": {
            "customer": {"resolved": False},
            "ship_to": {"resolved": False},
            "lines": [
                {
                    "line_no": 1,
                    "quantity": 2,
                    "product": {
                        "resolved": True,
                        "id": "P-1",
                        "pricebook_entry_id": None,
                        "unit_price": None,
                    },
                }
            ],
        },
    }
    tree, missing = build_record_tree(
        "salesforce",
        "order_orderitem",
        document,
        {"created_as": "active", "pricebook_id": None},
    )
    assert tree["finish_status"] == "Activated" and tree["fields"]["Status"] == "Draft"
    assert missing == [
        "Order.AccountId (customer unresolved)",
        "Order.PoNumber (po_number)",
        "Order.Pricebook2Id (standard pricebook id not loaded)",
        "Order.Shipping* (ship-to unresolved)",
        "OrderItem line 1: product has no pricebook entry",
        "OrderItem line 1: unit price (none on document or product)",
    ]
    with pytest.raises(KeyError):
        build_record_tree("salesforce", "opportunity", document)


def test_act_executors_handle_a_kpi_proposal_too() -> None:
    proposal = {
        "changes": [
            {"sku": "A", "current": 100, "proposed": 105},
            {"sku": "B", "current": 100, "proposed": 130},
            {"sku": "C", "current": 50, "proposed": 52},
        ]
    }
    options = {
        "guardrails": "10%",
        "per-run cap": "1 SKUs",
        "write target": "Pricing system",
        "rollback": "Restore the previous value",
        "dry_run": False,
    }
    limited = asyncio.run(
        apply_hard_limits(
            StepInput(previous_step_content=proposal, additional_data=options)
        )
    )
    assert [c["sku"] for c in limited.content["changes"]] == ["A", "C"]
    assert limited.content["held"][0]["class"] == "OVER_GUARDRAIL"
    capped = asyncio.run(
        apply_per_run_cap(
            StepInput(previous_step_content=limited.content, additional_data=options)
        )
    )
    assert [c["sku"] for c in capped.content["changes"]] == ["A"] and len(
        capped.content["held"]
    ) == 2
    written = asyncio.run(
        write_to_target(
            StepInput(previous_step_content=capped.content, additional_data=options)
        )
    )
    assert written.content["write"] == {
        "target": "Pricing system",
        "status": "NOT_CONNECTED",
        "count": 1,
    }
    rolled = asyncio.run(
        record_rollback_point(
            StepInput(previous_step_content=written.content, additional_data=options)
        )
    )
    assert rolled.content["rollback"]["points"] == 0


def test_send_email_without_a_recipient_is_a_visible_failure() -> None:
    output = asyncio.run(
        send_email(
            StepInput(
                previous_step_content={"documents": [], "exceptions": [{"class": "X"}]},
                additional_data={},
            )
        )
    )
    assert output.success is False and output.content["status"] == "no_recipient"


# --- the transports, recorded ------------------------------------------------------------


class _FakeImap:
    def __init__(self, host: str) -> None:
        self.host = host
        self.stored: List[Any] = []
        self.raw = sample_messages()[0]

    def login(self, user: str, password: str) -> None:
        assert (user, password) == ("orders@northwind.example", "x")

    def select(self, folder: str) -> None:
        assert folder == "INBOX"

    def search(self, charset: Any, *criteria: str) -> Any:
        assert criteria[:1] == ("UNSEEN",) and "SUBJECT" in criteria
        return "OK", [b"7"]

    def fetch(self, uid: bytes, what: str) -> Any:
        assert what == "(BODY.PEEK[])"
        msg = email.message.EmailMessage()
        msg["From"] = f"Dana <{self.raw.sender}>"
        msg["Subject"] = self.raw.subject
        msg["Date"] = "Tue, 15 Sep 2026 08:01:00 +0000"
        msg["Message-ID"] = self.raw.id
        msg.set_content("attached")
        att = self.raw.attachments[0]
        msg.add_attachment(
            att.data, maintype="application", subtype="pdf", filename=att.filename
        )
        return "OK", [(b"7 (BODY[] {n})", msg.as_bytes())]

    def store(self, uid: bytes, op: str, flags: str) -> None:
        self.stored.append((uid, op, flags))

    def logout(self) -> None:
        pass


def test_imap_fetch_peeks_flags_seen_and_never_deletes() -> None:
    created: List[_FakeImap] = []

    def factory(host: str) -> _FakeImap:
        client = _FakeImap(host)
        created.append(client)
        return client

    config = MailboxConfig(user="orders@northwind.example", password="x")
    messages = fetch_messages(
        config, MessageFilter(subject_contains="PO"), imap_factory=factory
    )
    assert (
        len(messages) == 1
        and messages[0].sender == "purchasing@bristolcoatings.example"
    )
    assert messages[0].attachments[0].data.startswith(b"%PDF")
    assert created[0].stored == [("7", "+FLAGS", "\\Seen")]
    assert not hasattr(_FakeImap, "expunge")


def test_parse_message_reads_sender_subject_and_attachment() -> None:
    msg = email.message.EmailMessage()
    msg["From"] = "Someone <A@Example.COM>"
    msg["Subject"] = "PO 1"
    msg.set_content("body")
    msg.add_attachment(
        b"%PDF-1.4 x", maintype="application", subtype="pdf", filename="po.pdf"
    )
    parsed = parse_message("9", msg.as_bytes())
    assert parsed.sender == "a@example.com" and parsed.sender_domain == "example.com"
    msg["Reply-To"] = "Buyer <buyer@customer.example>"
    replied = parse_message("9", msg.as_bytes())
    assert (
        replied.sender == "buyer@customer.example"
        and replied.from_address == "a@example.com"
    )
    assert parsed.body_text == "body" and parsed.attachments[0].filename == "po.pdf"


class _FakeSmtp:
    sent: List[Any] = []

    def __init__(self, host: str, port: int) -> None:
        assert (host, port) == ("smtp.gmail.com", 587)

    def starttls(self) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        pass

    def send_message(self, message: Any) -> None:
        _FakeSmtp.sent.append(message)

    def quit(self) -> None:
        pass


def test_send_email_goes_through_the_source_mailbox_s_smtp() -> None:
    _FakeSmtp.sent.clear()
    # A target with no mapping: the seven clean orders become NO_MAPPING at Build, and the
    # mail still goes -- with sixteen exceptions and nothing created, which is the point.
    outputs = _run_chain(
        _options(
            dry_run=False,
            smtp_factory=_FakeSmtp,
            **{"write target": "not-connected-target"},
        )
    )
    assert outputs[-1].content["status"] == "sent"
    assert (
        len(_FakeSmtp.sent) == 1 and _FakeSmtp.sent[0]["To"] == "ops@northwind.example"
    )
    body = _FakeSmtp.sent[0].get_content()
    assert "NO_MAPPING (7)" in body and "Exceptions: 16" in body


def _salesforce_transport(calls: List[Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(
                200, json={"access_token": "t", "instance_url": "https://org.example"}
            )
        if request.url.path.endswith("/query"):
            q = request.url.params["q"]
            if "FROM Account" in q:
                records = [
                    {
                        "Id": "001A",
                        "Name": "Bristol Coatings Ltd",
                        "ParentId": None,
                        "Email_Domain__c": "bristolcoatings.example",
                        "ShippingStreet": "386 Metacom Avenue",
                        "ShippingCity": "Bristol",
                        "ShippingState": "Rhode Island",
                        "ShippingPostalCode": "02809",
                        "ShippingCountry": "United States",
                    },
                    {
                        "Id": "001B",
                        "Name": "Bristol Coatings Ltd - Warren",
                        "ParentId": "001A",
                        "Email_Domain__c": "bristolcoatings.example",
                        "ShippingStreet": "12 Wharf Road",
                        "ShippingCity": "Warren",
                        "ShippingState": "Rhode Island",
                        "ShippingPostalCode": "02885",
                        "ShippingCountry": "United States",
                    },
                ]
            elif "FROM Contact" in q:
                records = [
                    {
                        "Id": "003A",
                        "Name": "Dana Purcell",
                        "Email": "Purchasing@bristolcoatings.example",
                        "AccountId": "001A",
                    }
                ]
            elif "FROM PricebookEntry" in q:
                records = [{"Id": "01uA", "Product2Id": "01tA", "UnitPrice": 640.0}]
            elif "FROM Product2" in q:
                records = [
                    {
                        "Id": "01tA",
                        "Name": "Xylene Solvent, 55 gal drum",
                        "ProductCode": "NW-110",
                        "IsActive": True,
                        "Description": "UoM: DR; Aliases: Xylene drum",
                        "Restricted__c": False,
                        "Customer_Part_Number__c": "bristolcoatings.example=306727",
                    }
                ]
            elif "FROM Pricebook2" in q:
                records = [{"Id": "01sA"}]
            else:
                records = [
                    {
                        "OrderId": "801A",
                        "Product2Id": "01tA",
                        "Quantity": 40.0,
                        "Order": {
                            "AccountId": "001A",
                            "PoNumber": "HIST-1",
                            "EffectiveDate": "2026-08-01",
                        },
                    }
                ]
            return httpx.Response(200, json={"done": True, "records": records})
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "801NEW"
                    if "/Order" in request.url.path
                    and "OrderItem" not in request.url.path
                    else "802NEW"
                },
            )
        return httpx.Response(204)

    return httpx.MockTransport(handler)


def test_salesforce_master_data_is_normalised_and_the_write_walks_the_tree() -> None:
    calls: List[Any] = []
    client = SalesforceClient(
        "https://login.example", "id", "secret", transport=_salesforce_transport(calls)
    )
    master = load_master_data(client)
    assert master["customer"] == [
        {
            "id": "001A",
            "name": "Bristol Coatings Ltd",
            "email_domain": "bristolcoatings.example",
        }
    ]
    assert [s["id"] for s in master["ship_to"]] == ["001A", "001B"] and master[
        "ship_to"
    ][1]["account_id"] == "001A"
    assert master["contact"][0]["email"] == "purchasing@bristolcoatings.example"
    product = master["product"][0]
    assert (
        product["uom"] == "DR"
        and product["aliases"] == ["Xylene drum"]
        and product["part_numbers"] == {"bristolcoatings.example": "306727"}
    )
    assert product["pricebook_entry_id"] == "01uA" and master["pricebook_id"] == "01sA"
    assert master["order_history"][0]["po_number"] == "HIST-1"

    tree = {
        "object": "Order",
        "fields": {"Status": "Draft", "AccountId": "001A"},
        "children": [
            {
                "object": "OrderItem",
                "parent_field": "OrderId",
                "fields": {"Quantity": 1},
                "line_no": 1,
            }
        ],
        "finish_status": "Activated",
    }
    result = write_record_tree(client, tree)
    assert result["id"] == "801NEW" and result["url"] == "https://org.example/801NEW"
    posts = [
        (m, p, json.loads(c))
        for m, p, c in calls
        if m in {"POST", "PATCH"} and "/sobjects/" in p
    ]
    assert (
        posts[0][1].endswith("/sobjects/Order") and posts[1][2]["OrderId"] == "801NEW"
    )
    assert posts[2] == (
        "PATCH",
        "/services/data/v67.0/sobjects/Order/801NEW",
        {"Status": "Activated"},
    )
    client.close()


def test_write_to_target_records_a_rollback_point_per_created_record() -> None:
    calls: List[Any] = []
    import egpt_retail.documents.salesforce as sf

    original = sf.client_from

    def fake_client_from(value: Any, *, transport: Any = None) -> SalesforceClient:
        return original(
            {
                "login_url": "https://login.example",
                "client_id": "i",
                "client_secret": "s",
            },
            transport=_salesforce_transport(calls),
        )

    sf.client_from = fake_client_from
    try:
        options = _options(dry_run=False, smtp_factory=_FakeSmtp)
        outputs = _run_chain(options, CHAIN[:8])
    finally:
        sf.client_from = original
    batch = outputs[-1].content
    assert all(
        d["written"]["status"] == "written" and d["written"]["record_status"] == "Draft"
        for d in batch["documents"]
    )
    assert batch["rollback"]["points"] == 7 and batch["rollback"]["persisted"] is False
    assert batch["rollback"]["records"][0] == {
        "object": "Order",
        "id": "801NEW",
        "action": "delete",
    }
    assert len(options["rollback_points"]) == 7
