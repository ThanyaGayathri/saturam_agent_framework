"""K21: master-data roles as data. The Resolve stage resolves only the roles a use case's
mapping reads and its guardrails need, the field each role reads comes from the template,
the document's reference is the template's, and a second document kind -- a return
request that becomes a Case -- runs through the purchase order's executors unchanged.
Offline: injected messages, a stand-in extractor, the sample master data, dry-run writes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from egpt.workflow.types import StepInput

from egpt_retail.documents.golden import case_to_message, generate_cases, score_case
from egpt_retail.documents.guardrails import PURCHASE_ORDER_RULES, suggest_rules
from egpt_retail.documents.mapping import (
    SHIPPED_MAPPINGS,
    apply_mapping,
    mapping_from,
    record_value,
    suggest_mapping,
)
from egpt_retail.documents.roles import (
    ROLES,
    reference_field,
    resolution_sources,
    roles_needed,
)
from egpt_retail.documents.salesforce import history_plan
from egpt_retail.documents.samples import (
    expected_extraction,
    sample_master_data,
    sample_messages,
)
from egpt_retail.documents.templates import EXTRACTION_TEMPLATES, template_from
from egpt_retail.executors.act import (
    apply_hard_limits,
    apply_per_run_cap,
    record_rollback_point,
    write_to_target,
)
from egpt_retail.executors.batch import add_exception
from egpt_retail.executors.documents import (
    build_record,
    receive_messages,
    resolve_entities,
    screen_documents,
    send_email,
)

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

CASE_MAPPING: Dict[str, Any] = {
    "label": "Case per return request",
    "object": "Case",
    "target": "salesforce",
    "fields": [
        {"target": "AccountId", "source": ["customer.id"], "required": True},
        {"target": "ContactId", "source": ["contact.id"]},
        {"target": "Subject", "source": ["rma_reference"], "required": True},
        {"target": "Description", "source": ["reason"]},
        {"target": "Origin", "source": ["=Email"]},
    ],
    "status_field": "Status",
    "status_values": {"draft": "New", "active": "Working"},
}


def _run(options: Dict[str, Any]) -> Dict[str, Any]:
    async def go() -> Any:
        previous = None
        for executor in CHAIN:
            output = await executor(
                StepInput(previous_step_content=previous, additional_data=options)
            )
            previous = output.content
        return previous

    result = asyncio.run(go())
    return {**result.get("batch", result), "_result": result}


# --- roles as data -----------------------------------------------------------------------------


class TestRolesNeeded:
    def test_the_order_mapping_with_its_preset_needs_every_role_it_did_before(self):
        needed = roles_needed(SHIPPED_MAPPINGS["order_orderitem"], PURCHASE_ORDER_RULES)
        assert needed == {"customer", "ship_to", "product", "order_history"}

    def test_a_case_mapping_needs_only_what_it_reads(self):
        assert roles_needed(mapping_from(CASE_MAPPING), []) == {"customer", "contact"}

    def test_guardrails_add_their_roles_and_dependencies(self):
        rules = [r for r in suggest_rules(template_from("return_request"))]
        needed = roles_needed(mapping_from(CASE_MAPPING), rules)
        assert "order_history" in needed and "product" in needed
        assert "ship_to" not in needed

    def test_the_pricebook_rides_with_the_product(self):
        assert roles_needed(SHIPPED_MAPPINGS["opportunity"], []) == {
            "customer",
            "product",
        }

    def test_nothing_mapped_nothing_resolved(self):
        assert roles_needed(None, []) == set()

    def test_every_role_has_a_label_and_a_concept(self):
        for role in ROLES.values():
            assert role.label and role.concept


class TestResolutionSources:
    def test_the_purchase_order_reads_the_fields_it_always_did(self):
        sources = resolution_sources(EXTRACTION_TEMPLATES["purchase_order"])
        assert sources["customer"]["name"] == "customer_name"
        assert sources["ship_to"]["address"] == "ship_to_address"
        assert sources["product"] == {
            "description": "description",
            "customer_part_number": "customer_part_number",
            "supplier_product_code": "supplier_product_code",
        }
        assert sources["reference"] == "po_number"

    def test_other_templates_derive_from_their_own_names(self):
        invoice = resolution_sources(EXTRACTION_TEMPLATES["supplier_invoice"])
        assert invoice["customer"]["name"] == "supplier_name"
        assert invoice["ship_to"]["address"] is None
        assert invoice["reference"] == "invoice_number"
        returns = resolution_sources(EXTRACTION_TEMPLATES["return_request"])
        assert returns["product"]["description"] == "product"
        assert returns["reference"] == "rma_reference"

    def test_a_template_may_declare_what_the_names_do_not_say(self):
        template = template_from(
            {
                "label": "Delivery note",
                "header": ["dn_no*", "consignee*", "site"],
                "lines": ["line_no*", "goods*", "qty*"],
                "reference": "dn_no",
                "resolution": {
                    "customer": {"name": "consignee"},
                    "product": {"description": "goods"},
                },
            }
        )
        sources = resolution_sources(template)
        assert sources["customer"]["name"] == "consignee"
        assert sources["product"]["description"] == "goods"
        assert reference_field(template) == "dn_no"
        assert template_from(template.__dict__ | {"header": ["dn_no*"]}).reference


# --- the resolve stage -------------------------------------------------------------------------


def _return_request_options(**overrides: Any) -> Dict[str, Any]:
    template = template_from("return_request")
    rules = [
        r
        for r in suggest_rules(template)
        if r.kind in {"sender_known", "duplicate_reference"}
    ]
    master = sample_master_data()
    needed = roles_needed(mapping_from(CASE_MAPPING), rules)
    cases = generate_cases(template, master, rules, roles=needed)
    for index, case in enumerate(cases):
        case["id"] = f"case_{index:02d}"
    by_id = {c["id"]: c for c in cases}

    def extractor(document: Dict[str, Any]) -> Dict[str, Any]:
        expected = by_id[document["message"]["id"][1:-7]]["expected"]["extracted"]
        return {
            **{name: expected.get(name) for name, _ in template.header},
            "reason": "Dented on arrival",
            "lines": [
                {"line_no": 1, "product": "Anything", "quantity": 1, "condition": "x"}
            ],
        }

    options = {
        "messages": [case_to_message(c) for c in cases],
        "source mailbox": {"user": "returns@northwind.example", "password": "x"},
        "document type": "pdf",
        "extraction template": template.id,
        "master data": master,
        "guardrails": [r.as_dict() for r in rules],
        "target objects": CASE_MAPPING,
        "created as": "draft",
        "per-run cap": "50 records",
        "write target": {"type": "salesforce"},
        "recipient": "returns@northwind.example",
        "rollback": "delete",
        "extractor": extractor,
        "dry_run": True,
        "_cases": cases,
    }
    options.update(overrides)
    return options


class TestReturnRequestToCase:
    def test_every_generated_case_lands_as_expected(self):
        options = _return_request_options()
        batch = _run(options)
        assert sorted(batch["roles_needed"]) == ["contact", "customer", "order_history"]
        assert batch["reference_field"] == "rma_reference"
        documents = {d["message"]["id"]: d for d in batch["documents"]}
        exceptions = {e["message_id"]: e for e in batch["exceptions"]}
        for case in options["_cases"]:
            message_id = f"<{case['id']}@cases>"
            verdict = score_case(
                case,
                document=documents.get(message_id),
                exception=exceptions.get(message_id),
            )
            assert verdict["status"] == "pass", (case["label"], verdict)
        # No ship-to and no product were asked for: the return has neither.
        for document in batch["documents"]:
            assert "ship_to" not in document["resolved"]
            assert document["resolved"]["lines"][0]["product"] == "Anything"
            assert document["resolved"]["contact"]["resolved"]
            assert document["record"]["object"] == "Case"
            assert document["record"]["fields"]["Status"] == "New"
            assert document["record"]["fields"]["ContactId"].startswith("C-")
            assert document["reference"].startswith("RR-")
            assert document["record"]["value"] is None

    def test_the_summary_email_names_the_record_not_a_po(self):
        options = _return_request_options()
        batch = _run(options)
        body = batch["_result"].get("body", "")
        assert "PO None" not in body
        assert "-> Case previewed" in body
        assert all(
            e["reference"] for e in batch["exceptions"] if e["stage"] != "screen"
        )


class TestPurchaseOrderUnchanged:
    """The order use case, exactly as before K21: same roles, same outcomes, same fields."""

    def test_the_sixteen_samples_land_where_they_did(self):
        options = {
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
            "rollback": "delete",
            "extractor": lambda d: expected_extraction(d["message"]["id"][1:5].upper()),
            "dry_run": True,
        }
        batch = _run(options)
        assert sorted(batch["roles_needed"]) == [
            "customer",
            "order_history",
            "product",
            "ship_to",
        ]
        assert batch["reference_field"] == "po_number"
        assert len(batch["documents"]) == 7 and len(batch["exceptions"]) == 9
        for document in batch["documents"]:
            assert document["resolved"]["ship_to"]["resolved"]
            assert document["resolved"]["lines"][0]["product"]["resolved"]
            assert document["reference"] == document["extracted"]["po_number"]
            assert document["record"]["value"] and document["record"]["value"] > 0
        assert {e["class"] for e in batch["exceptions"]} >= {
            "UNKNOWN_SENDER",
            "DUPLICATE_PO",
            "SHIP_TO_AMBIGUOUS",
            "PRODUCT_NOT_FOUND",
            "SCANNED_DOCUMENT",
        }
        assert all(e["reference"] == e["po_number"] for e in batch["exceptions"])


# --- the mapping side --------------------------------------------------------------------------


class TestMappingAdditions:
    def test_the_contact_is_a_source(self):
        document = {
            "extracted": {"rma_reference": "RR-1", "reason": "x", "lines": []},
            "resolved": {
                "customer": {"resolved": True, "id": "A-1"},
                "contact": {"resolved": True, "id": "C-1"},
            },
        }
        tree, missing = apply_mapping(mapping_from(CASE_MAPPING), document)
        assert not missing and tree["fields"]["ContactId"] == "C-1"

    def test_record_value_prices_lines_or_reads_an_amount(self):
        assert (
            record_value({"children": [{"fields": {"Quantity": 2, "UnitPrice": 10.5}}]})
            == 21.0
        )
        assert record_value({"fields": {"Amount": "99.5"}, "children": []}) == 99.5
        assert record_value({"fields": {"Subject": "x"}, "children": []}) is None

    def test_suggest_puts_the_reference_on_the_title_and_the_contact_by_type(self):
        case = {
            "name": "Case",
            "fields": [
                {"name": "AccountId", "type": "reference", "reference_to": ["Account"]},
                {"name": "ContactId", "type": "reference", "reference_to": ["Contact"]},
                {"name": "Subject", "type": "string", "length": 255},
                {"name": "Description", "type": "textarea", "length": 32000},
                {"name": "Reason", "type": "picklist", "picklist_values": ["a"]},
                {
                    "name": "Status",
                    "type": "picklist",
                    "picklist_values": ["New", "Working", "Closed"],
                },
            ],
        }
        out = suggest_mapping(
            {
                "header": [
                    "rma_reference",
                    "customer_name",
                    "order_reference",
                    "reason",
                ],
                "lines": ["line_no", "product", "quantity"],
            },
            case,
        )
        rows = {r["target"]: r for r in out["fields"]}
        assert rows["ContactId"]["source"] == ["contact.id"]
        assert rows["Subject"]["source"] == ["rma_reference"]
        assert rows["Description"]["source"] == ["reason"]
        assert "Reason" not in rows, "a picklist never takes free text"
        assert out["status_values"] == {"draft": "New", "active": "Working"}

    def test_suggest_leaves_an_order_name_alone_when_the_po_number_took_the_reference(
        self,
    ):
        order = {
            "name": "Order",
            "fields": [
                {"name": "Name", "type": "string", "length": 80},
                {"name": "PoNumber", "type": "string", "length": 80},
            ],
        }
        out = suggest_mapping({"header": ["po_number"], "lines": []}, order)
        assert [r["target"] for r in out["fields"]] == ["PoNumber"]


class TestTargetHistory:
    def test_the_order_history_reads_items_and_a_case_history_reads_cases(self):
        order = history_plan(
            SHIPPED_MAPPINGS["order_orderitem"],
            "po_number",
            since="2026-01-01T00:00:00Z",
        )
        assert order and order["soql"].startswith("SELECT OrderId, Order.AccountId")
        assert order["paths"]["product_id"] == "PricebookEntry.Product2Id"
        case = history_plan(
            mapping_from(CASE_MAPPING), "rma_reference", since="2026-01-01T00:00:00Z"
        )
        assert case == {
            "soql": "SELECT Id, AccountId, CreatedDate, Subject FROM Case "
            "WHERE CreatedDate >= 2026-01-01T00:00:00Z",
            "paths": {
                "record_id": "Id",
                "account_id": "AccountId",
                "date": "CreatedDate",
                "reference": "Subject",
            },
        }

    def test_no_account_no_history(self):
        mapping = mapping_from(
            {"object": "Task", "fields": [{"target": "Subject", "source": ["ref"]}]}
        )
        assert history_plan(mapping, "ref", since="x") is None


class TestExceptionRows:
    def test_the_exception_carries_the_reference_and_still_the_po_number(self):
        batch: Dict[str, List[Any]] = {"exceptions": []}
        row = add_exception(
            batch,
            {
                "message": {"id": "m"},
                "reference": "INV-9",
                "extracted": {"po_number": None},
            },
            cls="X",
            detail="d",
            stage="s",
        )
        assert row["reference"] == "INV-9" and row["po_number"] is None
        row = add_exception(
            batch,
            {"message": {"id": "m"}, "extracted": {"po_number": "PO-1"}},
            cls="X",
            detail="d",
            stage="s",
        )
        assert row["reference"] == "PO-1"


# --- a saved spec is the spec -------------------------------------------------------------------


class TestSavedSpecDrivesThePreviews:
    """A rehearsal leaves a compiled snapshot on a draft. Saving the spec afterwards must
    make the probes, the mailbox listing and the generated set read the new spec, not the
    snapshot -- a person who types a new message filter sees that filter probed."""

    def test_a_drafts_snapshot_is_dropped_on_save_and_previews_recompile(
        self, monkeypatch
    ):
        from tests.test_k16_use_cases_router import SPEC, _client, _service
        from egpt_retail.usecases import service as service_module

        service = _service(
            environ={
                "GMAIL_USER": "u@example.com",
                "GMAIL_APP_PASSWORD": "p",
                "SF_LOGIN_URL": "https://example.my.salesforce.com",
                "SF_CLIENT_ID": "id",
                "SF_CLIENT_SECRET": "secret",
            }
        )
        with _client(service) as client:
            self._check(client, service, monkeypatch, SPEC, service_module)

    @staticmethod
    def _check(client, service, monkeypatch, SPEC, service_module):
        from tests.test_k16_use_cases_router import _create_and_spec

        use_case_id = _create_and_spec(client)
        # A rehearsal (through the service, offline) leaves the snapshot behind.
        compiled = service.compile(service.store.get(use_case_id))
        service.store.update(
            use_case_id, compiled=service_module.compiled_to_dict(compiled)
        )
        assert not service.store.get(use_case_id)["compiled"]["parameters"].get(
            "message filter"
        )
        seen: Dict[str, Any] = {}

        def fake_fetch(mailbox, message_filter, **kwargs):
            seen["filter"] = message_filter
            return []

        monkeypatch.setattr(service_module, "fetch_messages", fake_fetch)
        new_spec = {
            **SPEC,
            "parameters": {
                **SPEC["parameters"],
                "message filter": {
                    "unseen_only": False,
                    "subject_contains": "e234324",
                    "since_days": 7,
                },
            },
        }
        assert (
            client.put(f"/use-cases/{use_case_id}/spec", json=new_spec).status_code
            == 200
        )
        assert service.store.get(use_case_id)["compiled"] is None
        assert (
            service.current_parameters(service.store.get(use_case_id))[
                "message filter"
            ]["subject_contains"]
            == "e234324"
        )
        service.list_mailbox_messages(use_case_id)
        assert seen["filter"].subject_contains == "e234324"
        service.probe(use_case_id, "mailbox")
        assert seen["filter"].subject_contains == "e234324"


class TestMessageFilterChips:
    """The form's three chips and its phrase all reach the mailbox reader: unread only,
    has an attachment, not from our own domain, words the subject must contain."""

    class _Imap:
        def __init__(self, host: str) -> None:
            self.calls: List[Any] = []

        def login(self, user: str, password: str) -> None:
            pass

        def select(self, folder: str) -> None:
            pass

        def search(self, charset: Any, *criteria: str) -> Any:
            self.calls.append(criteria)
            return "OK", [b"1 2 3"]

        def fetch(self, uid: str, what: str) -> Any:
            import email.message

            out = []
            for u, sender, attach in (
                ("1", "buyer@customer.example", True),
                ("2", "ops@northwind.example", True),  # our own domain
                ("3", "buyer@customer.example", False),  # no attachment
            ):
                if u not in uid.split(","):
                    continue
                msg = email.message.EmailMessage()
                msg["From"] = sender
                msg["Subject"] = f"Doc {u}"
                msg["Message-ID"] = f"<{u}@x>"
                msg.set_content("hello")
                if attach:
                    msg.add_attachment(
                        b"%PDF-1.4",
                        maintype="application",
                        subtype="pdf",
                        filename="d.pdf",
                    )
                out.append(
                    (f"{u} (RFC822.SIZE 100 BODY[] {{n}})".encode(), msg.as_bytes())
                )
            return "OK", out

        def store(self, *args: Any) -> None:
            pass

        def logout(self) -> None:
            pass

    def test_filter_from_reads_every_chip(self):
        from egpt_retail.documents.mailbox import filter_from

        f = filter_from(
            {
                "unseen_only": False,
                "subject_contains": "e234324",
                "since_days": 7,
                "has_attachment": True,
                "not_own_domain": True,
            }
        )
        assert (
            f.unseen_only,
            f.subject_contains,
            f.has_attachment,
            f.not_own_domain,
        ) == (
            False,
            "e234324",
            True,
            True,
        )

    def test_own_domain_and_no_attachment_are_skipped_and_the_phrase_is_searched(self):
        from egpt_retail.documents.mailbox import (
            MailboxConfig,
            MessageFilter,
            fetch_messages,
        )

        clients: List[Any] = []

        def factory(host: str) -> Any:
            clients.append(self._Imap(host))
            return clients[-1]

        config = MailboxConfig(user="ops@northwind.example", password="x")
        got = fetch_messages(
            config,
            MessageFilter(
                unseen_only=False,
                subject_contains="e234324",
                has_attachment=True,
                not_own_domain=True,
            ),
            imap_factory=factory,
            mark_seen=False,
        )
        assert [m.id for m in got] == ["<1@x>"]
        assert clients[0].calls[0][:1] == ("ALL",)
        assert ("SUBJECT", '"e234324"') == clients[0].calls[0][-2:]
        # A headers-only listing cannot see attachments; it still drops our own domain.
        listed = fetch_messages(
            config,
            MessageFilter(unseen_only=False, has_attachment=True, not_own_domain=True),
            imap_factory=factory,
            mark_seen=False,
            headers_only=True,
        )
        assert [m.id for m in listed] == ["<1@x>", "<3@x>"]
