"""K16: the ``/use-cases`` router and service, DB-free.

An in-memory store stands in for ``UseCaseStore``; the executors are the real registry; the
extraction step is the sample set's stand-in (no model); every run is a rehearsal or a live
run whose write target cannot resolve. What is asserted is the flow the interface drives:
create, save spec, preflight, activate, run, poll -- and that a run's result is the batch the
last executor returned.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt_retail.api.routers import use_cases as module
from egpt_retail.app.components import build_registry
from egpt_retail.usecases.credentials import CredentialUnavailable, resolve_credentials
from egpt_retail.usecases.runtime import run_parameters, workflow_from_compiled
from egpt_retail.usecases.service import (
    UseCaseService,
    compiled_to_dict,
    normalise_schedule,
)
from egpt_retail.usecases.store import new_run_id, new_use_case_id
from egpt_retail.api.routers import extraction_templates as catalogue_module
from egpt_retail.documents.guardrails import (
    PURCHASE_ORDER_RULES,
    RULE_KINDS,
    check_document,
    rules_from,
    suggest_rules,
)
from egpt_retail.documents.template_draft import DraftedField, TemplateDraft
from egpt_retail.documents.templates import template_from


class FakeStore:
    """The store's surface over dicts. Sync methods; the async twins delegate."""

    def __init__(self) -> None:
        self.use_cases: Dict[str, Dict[str, Any]] = {}
        self.runs: Dict[str, Dict[str, Any]] = {}

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def create(
        self,
        *,
        title,
        description,
        business_area,
        autonomy="copilot",
        level=1,
        tags=None,
        source="user",
        use_case_id=None,
    ):
        identifier = use_case_id or new_use_case_id()
        self.use_cases[identifier] = {
            "id": identifier,
            "title": title,
            "description": description,
            "business_area": business_area,
            "status": "draft",
            "source": source,
            "level": level,
            "autonomy": autonomy,
            "tags": list(tags or []),
            "version": 0,
            "schedule": None,
            "workflow_component_id": None,
            "draft": None,
            "spec": None,
            "compiled": None,
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        return dict(self.use_cases[identifier])

    def get(self, use_case_id):
        row = self.use_cases.get(use_case_id)
        return dict(row) if row else None

    def list(self, *, status=None):
        return [
            dict(r)
            for r in self.use_cases.values()
            if status is None or r["status"] == status
        ]

    def update(self, use_case_id, **fields):
        row = self.use_cases.get(use_case_id)
        if row is None:
            return None
        row.update(fields)
        row["updated_at"] = self._now()
        return dict(row)

    def delete(self, use_case_id):
        return self.use_cases.pop(use_case_id, None) is not None

    def create_run(
        self,
        *,
        use_case_id,
        use_case_version,
        trigger="manual",
        mode="live",
        run_id=None,
    ):
        identifier = run_id or new_run_id()
        self.runs[identifier] = {
            "id": identifier,
            "use_case_id": use_case_id,
            "use_case_version": use_case_version,
            "status": "queued",
            "trigger": trigger,
            "mode": mode,
            "started_at": self._now(),
            "finished_at": None,
            "framework_run_id": None,
            "output_ref": None,
            "provenance_ref": None,
            "summary": "",
            "blocked_reason": None,
            "result": None,
        }
        return dict(self.runs[identifier])

    def get_run(self, run_id):
        row = self.runs.get(run_id)
        return dict(row) if row else None

    def list_runs(self, use_case_id, *, status=None, limit=50):
        rows = [
            dict(r)
            for r in self.runs.values()
            if r["use_case_id"] == use_case_id
            and (status is None or r["status"] == status)
        ]
        return sorted(rows, key=lambda r: r["started_at"], reverse=True)[:limit]

    def update_run(self, run_id, **fields):
        row = self.runs.get(run_id)
        if row is None:
            return None
        row.update(fields)
        return dict(row)

    def last_run(self, use_case_id):
        runs = self.list_runs(use_case_id, limit=1)
        return runs[0] if runs else None

    def processed_message_ids(self, use_case_id, *, limit=200):
        ids = set()
        for r in self.runs.values():
            if (
                r["use_case_id"] != use_case_id
                or r["status"] != "succeeded"
                or r["mode"] != "live"
                or not r.get("result")
            ):
                continue
            for d in r["result"].get("documents") or []:
                ids.add(d["message"]["id"])
            for e in r["result"].get("exceptions") or []:
                ids.add(e["message_id"])
        return sorted(ids)

    async def acreate(self, **kw):
        return self.create(**kw)

    async def aget(self, i):
        return self.get(i)

    async def alist(self, **kw):
        return self.list(**kw)

    async def aupdate(self, i, **kw):
        return self.update(i, **kw)

    async def adelete(self, i):
        return self.delete(i)

    async def acreate_run(self, **kw):
        return self.create_run(**kw)

    async def aget_run(self, i):
        return self.get_run(i)

    async def alist_runs(self, i, **kw):
        return self.list_runs(i, **kw)

    async def aupdate_run(self, i, **kw):
        return self.update_run(i, **kw)

    async def alast_run(self, i):
        return self.last_run(i)

    async def aprocessed_message_ids(self, i, **kw):
        return self.processed_message_ids(i, **kw)


from egpt.workflow.types import StepInput, StepOutput  # noqa: E402


async def record_provenance(step_input: StepInput) -> StepOutput:
    """R7's executor writes to the control plane; the DB-free test stands it in with the
    same content shape (``provenance_ref`` plus the forwarded content)."""
    return StepOutput(
        content={
            "provenance_ref": (step_input.additional_data or {}).get("run_id"),
            "explanation": step_input.previous_step_content,
        }
    )


EXECUTORS = {f.__name__: f for f in build_registry().functions}
EXECUTORS["record_provenance"] = record_provenance

SPEC = {
    "use_case_id": "x",
    "question": "Turn purchase-order emails into Salesforce orders.",
    "business_area": "sales",
    "entities": [{"concept": "Customer", "role": "subject"}],
    "kpi_identifiers": [],
    "grain": "overall",
    "notes": "",
    "extracted_by": {"identifier": "interface.form", "version": "1"},
    "shape": "act",
    "template": "document_to_record",
    "parameters": {
        "source mailbox": {
            "id": "seed_gmail_orders",
            "sourceType": "gmail",
            "name": "Gmail - orders",
            "credentialRef": "env:gmail",
        },
        "message filter": "",
        "document type": "pdf",
        "extraction template": "purchase_order",
        "master data": {
            "customer": "ds-sf-account",
            "product": "ds-sf-product",
            "shipTo": "ds-sf-address",
            "orderHistory": "ds-sf-order",
        },
        "match thresholds": {
            "address": 0.75,
            "product": 0.85,
            "name": 0.8,
            "ambiguity": 0.05,
        },
        "guardrails": [
            {"id": "duplicate_po"},
            {"id": "unknown_sender"},
            {"id": "first_time_product"},
            {"id": "quantity_anomaly"},
            {"id": "due_date_sanity"},
            {"id": "restricted_product"},
        ],
        "recipient": "ops@northwind.example",
        "write target": {
            "id": "seed_salesforce_orders",
            "sourceType": "salesforce",
            "name": "Salesforce",
            "credentialRef": "env:salesforce",
        },
        "target objects": "order_orderitem",
        "created as": "draft",
        "per-run cap": "50 records",
        "rollback": "Cancel the record and remove its lines",
        "schedule": "every_15_minutes",
    },
}

ENV_WITH_CREDS = {
    "GMAIL_USER": "orders@northwind.example",
    "GMAIL_APP_PASSWORD": "x",
    "GMAIL_IMAP_HOST": "imap.invalid",
    "SF_LOGIN_URL": "https://login.example",
    "SF_CLIENT_ID": "i",
    "SF_CLIENT_SECRET": "s",
}


def _service(
    environ: Optional[Dict[str, str]] = None, agent_factory: Any = None
) -> UseCaseService:
    return UseCaseService(
        FakeStore(),
        executors=EXECUTORS,
        environ=environ if environ is not None else {},
        agent_factory=agent_factory,
    )


@contextmanager
def _client(
    service: UseCaseService, *, authorization: bool = False, scopes: tuple = ()
):
    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.authorization_enabled = authorization
        request.state.scopes = list(scopes)
        request.state.admin_scope = None
        return await call_next(request)

    app.include_router(module.router)
    app.dependency_overrides[module.use_case_service] = lambda: service
    with TestClient(app) as client:
        yield client


def _create_and_spec(client: TestClient) -> str:
    created = client.post(
        "/use-cases",
        json={
            "title": "Order creation",
            "description": "PO emails to orders",
            "business_area": "sales",
            "autonomy": "autopilot",
            "level": 4,
        },
    )
    assert created.status_code == 201, created.text
    use_case_id = created.json()["id"]
    saved = client.put(f"/use-cases/{use_case_id}/spec", json=SPEC)
    assert saved.status_code == 200, saved.text
    assert saved.json()["use_case_id"] == use_case_id
    assert saved.json()["parameters"]["target objects"] == "order_orderitem"
    return use_case_id


# --- the flow ---------------------------------------------------------------------------------


def test_create_spec_preflight_activate_and_rehearse_run() -> None:
    service = _service({})
    with _client(service) as client:
        use_case_id = _create_and_spec(client)

        preflight = client.post(f"/use-cases/{use_case_id}/preflight").json()
        by_name = {c["name"]: c for c in preflight["checks"]}
        assert (
            by_name["spec_present"]["passed"] and by_name["workflow_compiles"]["passed"]
        )
        assert (
            not by_name["mailbox_credentials"]["passed"]
            and "GMAIL_USER" in by_name["mailbox_credentials"]["reason"]
        )
        assert (
            not preflight["allowed"] and "extraction_model" in preflight["blocked_by"]
        )

        refused = client.post(
            f"/use-cases/{use_case_id}/activate",
            json={"level": 4, "autonomy": "autopilot", "schedule": "every_15_minutes"},
        )
        assert refused.status_code == 409
        assert (
            refused.json()["allowed"] is False
            and "mailbox_credentials" in refused.json()["blocked_by"]
        )

        # A live run cannot start on a draft; a rehearsal can -- that is what it is for.
        assert client.post(f"/use-cases/{use_case_id}/runs").status_code == 409
        early = client.post(
            f"/use-cases/{use_case_id}/runs", params={"mode": "rehearse"}
        )
        assert early.status_code == 202
        client.portal.call(service.await_runs)
        early_run = client.get(
            f"/use-cases/{use_case_id}/runs/{early.json()['id']}"
        ).json()
        assert early_run["status"] == "succeeded" and early_run["use_case_version"] == 0
        assert service.store.get(use_case_id)["status"] == "draft"

        # With credentials in the environment the same use case activates.
        service._environ = dict(ENV_WITH_CREDS, AZURE_OPENAI_API_KEY="k")
        activated = client.post(
            f"/use-cases/{use_case_id}/activate",
            json={"level": 4, "autonomy": "autopilot", "schedule": "every_15_minutes"},
        )
        assert activated.status_code == 200, activated.text
        body = activated.json()
        assert (
            body["status"] == "activated"
            and body["version"] == 1
            and body["level"] == 4
        )
        stored = service.store.get(use_case_id)
        assert stored["schedule"] == "every_15_minutes"
        assert stored["compiled"]["publishable"] is True
        assert [
            s.get("executor_ref") or s.get("agent_id")
            for s in stored["compiled"]["config"]["steps"]
        ][:3] == ["receive_messages", "screen_documents", f"{use_case_id}__extraction"]

        plan = client.get(f"/use-cases/{use_case_id}/plan").json()
        assert plan["version"] == 1 and [s["ref"] for s in plan["steps"]][:2] == [
            "receive_messages",
            "screen_documents",
        ]
        assert any(s["kind"] == "agent" for s in plan["steps"])

        # Rehearse: no model, no Salesforce -- the sample set and its expected extraction.
        service._environ = {}
        queued = client.post(
            f"/use-cases/{use_case_id}/runs", params={"mode": "rehearse"}
        )
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["id"]
        assert (
            queued.json()["status"] == "queued" and queued.json()["mode"] == "rehearse"
        )
        client.portal.call(service.await_runs)

        run = client.get(f"/use-cases/{use_case_id}/runs/{run_id}").json()
        assert run["status"] == "succeeded", run
        result = run["result"]
        assert len(result["documents"]) == 7 and len(result["exceptions"]) == 9
        assert all(d["written"]["status"] == "previewed" for d in result["documents"])
        assert {e["class"] for e in result["exceptions"]} >= {
            "DUPLICATE_PO",
            "SCANNED_DOCUMENT",
            "SHIP_TO_AMBIGUOUS",
        }
        assert "text" not in result["documents"][0]["document"], (
            "page text is not carried on the wire"
        )
        assert run["summary"] == "16 received, 7 previewed, 9 exception(s)"
        assert result["status"] == "previewed" and result["to"] == [
            "ops@northwind.example"
        ]

        listed = client.get(f"/use-cases/{use_case_id}/runs").json()
        assert listed[0]["id"] == run_id and len(listed) == 2, (
            "newest first, the early rehearsal behind it"
        )
        catalog = client.get(f"/use-cases/{use_case_id}").json()
        assert (
            catalog["last_run"]["id"] == run_id
            and catalog["last_run"]["status"] == "succeeded"
        )


def test_live_run_records_a_failure_instead_of_raising() -> None:
    service = _service(
        dict(ENV_WITH_CREDS, AZURE_OPENAI_API_KEY="k"),
        agent_factory=lambda role, agent_id, template: None,
    )
    with _client(service) as client:
        use_case_id = _create_and_spec(client)
        assert (
            client.post(
                f"/use-cases/{use_case_id}/activate", json={"level": 4}
            ).status_code
            == 200
        )
        queued = client.post(f"/use-cases/{use_case_id}/runs")
        assert queued.status_code == 202
        client.portal.call(service.await_runs)
        run = client.get(f"/use-cases/{use_case_id}/runs/{queued.json()['id']}").json()
        # The mailbox at orders@northwind.example does not exist: the IMAP login fails, and
        # that is a failed run row, not a 500 and not a hang.
        assert run["status"] == "failed" and run["blocked_reason"]


def test_level_three_needs_the_activate_scope_when_authorization_is_on() -> None:
    service = _service(dict(ENV_WITH_CREDS, AZURE_OPENAI_API_KEY="k"))
    with _client(
        service, authorization=True, scopes=("use-cases:write", "use-cases:read")
    ) as client:
        use_case_id = _create_and_spec(client)
        forbidden = client.post(f"/use-cases/{use_case_id}/activate", json={"level": 3})
        assert (
            forbidden.status_code == 403
            and "use-cases:activate" in forbidden.json()["detail"]
        )
        assert (
            client.post(
                f"/use-cases/{use_case_id}/activate", json={"level": 2}
            ).status_code
            == 200
        )
    with _client(service, authorization=True, scopes=("use-cases:read",)) as client:
        assert (
            client.post(
                "/use-cases",
                json={"title": "t", "description": "d", "business_area": "sales"},
            ).status_code
            == 403
        )


def test_not_found_paths() -> None:
    with _client(_service()) as client:
        assert client.get("/use-cases/uc_nope").status_code == 404
        assert client.get("/use-cases/uc_nope/spec").status_code == 404
        assert client.post("/use-cases/uc_nope/preflight").status_code == 404
        assert client.post("/use-cases/uc_nope/runs").status_code == 404
        created = client.post(
            "/use-cases",
            json={"title": "t", "description": "d", "business_area": "sales"},
        ).json()
        assert client.get(f"/use-cases/{created['id']}/spec").status_code == 404
        assert (
            client.get(f"/use-cases/{created['id']}/runs/run_nope").status_code == 404
        )
        assert client.delete(f"/use-cases/{created['id']}").status_code == 204
        assert client.get(f"/use-cases/{created['id']}").status_code == 404


def test_draft_round_trip_and_pause() -> None:
    service = _service()
    with _client(service) as client:
        use_case_id = _create_and_spec(client)
        draft = {
            "version": 1,
            "step1": {"title": "t", "description": "d", "category": "sales"},
            "step2": {"primary_question": "q"},
            "step3": {},
            "step8": {
                "schedule": "every_15_minutes",
                "recipient": "ops@northwind.example",
            },
        }
        assert (
            client.put(f"/use-cases/{use_case_id}/draft", json=draft).status_code == 200
        )
        assert (
            client.get(f"/use-cases/{use_case_id}/draft").json()["step8"]["recipient"]
            == "ops@northwind.example"
        )
        assert (
            client.post(f"/use-cases/{use_case_id}/pause").json()["status"] == "paused"
        )
        assert (
            client.get("/use-cases", params={"status": "paused"}).json()[0]["id"]
            == use_case_id
        )


# --- the pieces ---------------------------------------------------------------------------------


def test_run_parameters_resolve_sources_and_master_data_roles() -> None:
    params = run_parameters(SPEC["parameters"], environ=ENV_WITH_CREDS)
    assert (
        params["source mailbox"]["user"] == "orders@northwind.example"
        and params["source mailbox"]["password"] == "x"
    )
    assert "credentialRef" not in params["source mailbox"]
    assert params["write target"] == {
        "id": "seed_salesforce_orders",
        "sourceType": "salesforce",
        "name": "Salesforce",
        "login_url": "https://login.example",
        "client_id": "i",
        "client_secret": "s",
        "type": "salesforce",
    }
    assert params["master data"] == {
        "source": "salesforce",
        "roles": ["customer", "order_history", "product", "ship_to"],
    }
    assert params["guardrails"] == SPEC["parameters"]["guardrails"]
    with pytest.raises(CredentialUnavailable):
        resolve_credentials(
            {"credentialRef": "vault:gmail"}, kind="mailbox", environ={}
        )
    with pytest.raises(CredentialUnavailable):
        resolve_credentials(
            {"credentialRef": "env:gmail"}, kind="mailbox", environ={"GMAIL_USER": "u"}
        )


def test_workflow_from_compiled_refuses_unknown_refs() -> None:
    from egpt_retail.usecases.runtime import UnresolvableStep

    with pytest.raises(UnresolvableStep):
        workflow_from_compiled(
            {
                "config": {
                    "steps": [
                        {
                            "type": "Step",
                            "name": "x",
                            "executor_ref": "no_such_executor",
                        }
                    ]
                }
            },
            executors=EXECUTORS,
        )
    with pytest.raises(UnresolvableStep):
        workflow_from_compiled(
            {
                "config": {"steps": [{"type": "Step", "name": "m", "agent_id": "a"}]},
                "model_steps": [{"agent_id": "a", "role": "proposal"}],
            },
            executors=EXECUTORS,
        )


def test_compiled_to_dict_is_json_and_schedule_aliases() -> None:
    from egpt_retail.orchestration.compiler import compile_workflow

    compiled = compile_workflow(
        SPEC,
        registered_executors=set(EXECUTORS),
        shape="act",
        template="document_to_record",
        use_case_id="uc_x",
    )
    as_dict = compiled_to_dict(compiled)
    assert (
        as_dict["shape"] == "act"
        and as_dict["template"] == "document_to_record"
        and as_dict["publishable"] is True
    )
    assert as_dict["parameter_origins"]["guardrails"] == "spec"
    assert (
        normalise_schedule("15m") == "every_15_minutes"
        and normalise_schedule("hourly") == "hourly"
        and normalise_schedule("") is None
    )


# --- K17: templates and guardrails as data -------------------------------------------------------


CUSTOM_TEMPLATE = {
    "label": "Delivery note",
    "record": "receipt",
    "header": [
        {"name": "note_number", "required": True},
        {"name": "supplier_name", "required": True},
        {"name": "expected_delivery_date"},
        {"name": "total"},
    ],
    "lines": [
        {"name": "line_no", "required": True},
        {"name": "description", "required": True},
        {"name": "quantity", "required": True},
        {"name": "line_total"},
    ],
}


@contextmanager
def _catalogue_client(agent: Any = None):
    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.authorization_enabled = False
        return await call_next(request)

    app.include_router(catalogue_module.router)
    app.dependency_overrides[catalogue_module.drafting_agent] = lambda: agent
    with TestClient(app) as client:
        yield client


def test_shipped_templates_and_rule_kinds_are_served_as_data() -> None:
    with _catalogue_client() as client:
        templates = client.get("/extraction-templates").json()
        assert [t["id"] for t in templates] == [
            "purchase_order",
            "supplier_invoice",
            "return_request",
        ]
        assert templates[0]["source"] == "shipped" and templates[0]["header"][0] == {
            "name": "po_number",
            "required": True,
            "hint": "",
        }
        kinds = client.get("/guardrail-rules/kinds").json()
        assert {k["id"] for k in kinds} == set(RULE_KINDS)
        suggested = client.post("/guardrail-rules/suggest", json=CUSTOM_TEMPLATE).json()
        assert [r["kind"] for r in suggested] == [
            "sender_known",
            "duplicate_reference",
            "date_window",
            "flagged_item",
            "first_time_item",
            "quantity_outlier",
            "total_matches_lines",
        ]
        assert (
            suggested[1]["field"] == "note_number"
            and suggested[-1]["line_field"] == "line_total"
        )
        assert (
            client.post(
                "/guardrail-rules/suggest",
                json={"label": "Empty", "header": [], "lines": []},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/extraction-templates/draft", json={"description": "x"}
            ).status_code
            == 503
        )


def test_drafting_returns_the_template_shape_from_the_model_s_draft() -> None:
    class _Response:
        content = TemplateDraft(
            label="Delivery Note",
            record="receipt",
            header=[
                DraftedField(name="Note Number", required=True, hint="top right"),
                DraftedField(name="supplier_name", required=True),
            ],
            lines=[
                DraftedField(name="line_no", required=True),
                DraftedField(name="description", required=True),
                DraftedField(name="quantity"),
            ],
            notes="No currency on the example.",
        )

    class _Agent:
        prompts: List[str] = []

        async def arun(self, prompt: str) -> Any:
            self.prompts.append(prompt)
            return _Response()

    agent = _Agent()
    from egpt_retail.documents.pdf import text_pdf
    import base64

    pdf_b64 = base64.b64encode(
        text_pdf(["DELIVERY NOTE 771", "Supplier: Acme"])
    ).decode()
    with _catalogue_client(agent) as client:
        drafted = client.post(
            "/extraction-templates/draft",
            json={
                "description": "Suppliers email delivery notes as PDFs.",
                "label": "Delivery note",
                "example_document_b64": pdf_b64,
            },
        ).json()
    template = drafted["template"]
    assert (
        template["id"] == "delivery_note"
        and template["source"] == "drafted"
        and template["record"] == "receipt"
    )
    assert template["header"][0] == {
        "name": "note_number",
        "required": True,
        "hint": "top right",
    }
    assert drafted["notes"] == "No currency on the example."
    assert (
        "DELIVERY NOTE 771" in agent.prompts[0] and "Delivery note" in agent.prompts[0]
    )


def test_custom_template_runs_the_same_extractor_and_rules() -> None:
    template = template_from(CUSTOM_TEMPLATE)
    assert template.id == "delivery_note" and template.required_header == (
        "note_number",
        "supplier_name",
    )
    from egpt_retail.documents.extraction import (
        extraction_instructions,
        extraction_output_model,
    )

    assert "note_number*" in extraction_instructions(template)
    model = extraction_output_model(template)
    assert set(model.model_fields) == {
        "note_number",
        "supplier_name",
        "expected_delivery_date",
        "total",
        "lines",
    }

    rules = rules_from([r.as_dict() for r in suggest_rules(template)])
    doc = {
        "message": {"sender": "x@bristolcoatings.example"},
        "extracted": {
            "note_number": "DN-1",
            "expected_delivery_date": "2020-01-01",
            "total": 500.0,
            "lines": [{"line_no": 1, "quantity": 2, "line_total": 100.0}],
        },
        "resolved": {
            "customer": {"resolved": True, "id": "A-001", "method": "sender_domain"},
            "lines": [
                {
                    "line_no": 1,
                    "quantity": 2,
                    "line_total": 100.0,
                    "product": {"resolved": False},
                }
            ],
        },
    }
    verdict = check_document(
        doc,
        {"customer": [], "product": [], "order_history": []},
        rules,
        today=date(2026, 9, 15),
    )
    assert [e["class"] for e in verdict["exceptions"]] == [
        "DATE_INVALID",
        "TOTAL_MISMATCH",
    ]
    assert (
        "expected delivery date 2020-01-01 is in the past"
        in verdict["exceptions"][0]["detail"]
    )


def test_legacy_rows_and_the_preset_still_read_the_same() -> None:
    legacy = rules_from(
        [
            {"id": "quantity_anomaly", "param": 5},
            {"id": "due_date_sanity", "params": {"maxDays": 30}},
            "restricted_product",
        ]
    )
    assert [(r.id, r.kind, dict(r.params)) for r in legacy] == [
        ("quantity_anomaly", "quantity_outlier", {"multiplier": 5.0}),
        ("due_date_sanity", "date_window", {"max_days": 30.0}),
        ("restricted_product", "flagged_item", {}),
    ]
    assert legacy[0].exception_class == "QUANTITY_ANOMALY"
    assert [r.id for r in rules_from("all")] == [r.id for r in PURCHASE_ORDER_RULES]
    assert rules_from([{"kind": "no_such_kind", "field": "x"}]) == []
    custom = rules_from(
        [{"kind": "numeric_range", "field": "total", "params": {"min": 0, "max": 100}}]
    )[0]
    assert (
        custom.id == "numeric_range_total"
        and custom.label == "Numeric range: total"
        and custom.exception_class == "OUT_OF_RANGE"
    )
