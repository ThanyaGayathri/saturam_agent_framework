"""K26: the recommend stage -- the catalogue's rules, the records a scoring becomes, the
executor's writes (held under dry run) and how the result rides the chain."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.executors import recommend as recommend_module
from egpt_retail.executors.recommend import recommend, recommendations_of, records_for
from egpt_retail.recommendations import PLAYS, play_for, play_of


def _subject(**over: Any) -> Dict[str, Any]:
    base = {
        "quote_id": 486,
        "account_id": 12,
        "account_name": "Sanne Hendriks",
        "category": "Griddles",
        "brand": "Pitco",
        "territory": "India East",
        "rep": "M. Nambiar",
        "segment": "SMB",
        "source": "Live Chat",
        "stage": "Proposal Sent",
        "quote_date": "2026-09-03",
        "value": 9256.0,
        "age_days": 17,
        "usual_days": 26,
        "stalled": False,
        "p_convert": 0.02,
        "risk": 0.98,
        "level": "high",
        "value_at_risk": 9070.9,
        "drivers": [
            {
                "feature": "last_lost_reason",
                "text": "lost: Decision Delayed",
                "contribution": -1.2,
            }
        ],
        "last_lost_reason": None,
        "account": {
            "recency_days": 20,
            "frequency": 3,
            "monetary": 12000.0,
            "segment": "Champions",
            "at_risk": False,
            "won": 2,
            "lost": 1,
        },
    }
    base.update(over)
    return base


class TestCatalogue:
    def test_ten_plays_with_owners(self):
        assert len(PLAYS) == 10
        assert {p.code for p in PLAYS} >= {
            "RE_ENGAGE",
            "ESCALATE",
            "RELEASE",
            "ACCOUNT_REVIEW",
        }
        assert all(p.owner and p.action and p.when for p in PLAYS)
        assert play_of("ESCALATE").owner == "Territory manager"
        assert play_of("nope") is None

    def test_the_last_loss_decides_first_whatever_the_word_order(self):
        for reason, code in (
            ("Decision Delayed", "DECISION_SUPPORT"),
            ("Response No", "DECISION_SUPPORT"),
            ("High Too Price", "COMMERCIAL_OFFER"),
            ("Constraints Budget", "COMMERCIAL_OFFER"),
            ("Chosen Competitor", "COMPETITIVE_RESPONSE"),
            ("Alternative Specification", "REQUOTE_SPEC"),
        ):
            play = play_for(
                _subject(
                    last_lost_reason=reason,
                    account={"at_risk": True, "segment": "At risk", "recency_days": 80},
                )
            )
            assert play["code"] == code, reason
            assert reason in play["because"]
            assert play["from_catalogue"] is True

    def test_account_state_next(self):
        valuable = play_for(
            _subject(
                account={
                    "at_risk": True,
                    "segment": "Cannot lose them",
                    "recency_days": 75,
                }
            )
        )
        assert valuable["code"] == "ACCOUNT_REVIEW" and "75 days" in valuable["because"]
        small = play_for(
            _subject(
                account={"at_risk": True, "segment": "Hibernating", "recency_days": 90}
            )
        )
        assert small["code"] == "REACTIVATE"

    def test_channel_release_escalate_and_the_default(self):
        channel = play_for(
            _subject(segment="Dealer / Distributor", stage="Qualification")
        )
        assert channel["code"] == "CHANNEL_PARTNER"
        release = play_for(
            _subject(stalled=True, age_days=80, usual_days=26, value=900.0),
            territory_median_value=5000.0,
        )
        assert release["code"] == "RELEASE"
        escalate = play_for(
            _subject(level="high", value=9000.0), territory_median_value=5000.0
        )
        assert escalate["code"] == "ESCALATE" and "$9,000" in escalate["because"]
        default = play_for(
            _subject(level="medium", value=4000.0, age_days=25),
            territory_median_value=5000.0,
        )
        assert (
            default["code"] == "RE_ENGAGE"
            and "1 day of the usual range left" in default["because"]
        )
        stalled = play_for(
            _subject(stalled=True, age_days=40, level="medium", value=4000.0),
            territory_median_value=5000.0,
        )
        assert (
            stalled["code"] == "RE_ENGAGE"
            and "open 40 days against a usual 26" in stalled["because"]
        )


def _risk() -> Dict[str, Any]:
    subjects = [
        _subject(
            quote_id=1, level="high", value=9000.0, last_lost_reason="Chosen Competitor"
        ),
        _subject(quote_id=2, level="medium", value=6000.0, rep="S. Mehta"),
        _subject(quote_id=3, level="low", value=8000.0),
        _subject(
            quote_id=4,
            level="high",
            value=1000.0,
            stalled=True,
            age_days=70,
            rep="S. Mehta",
        ),
    ]
    return {
        "as_of": "2026-09-20",
        "model": {"version": "abc123"},
        "subjects": subjects,
        "summary": {},
    }


class TestRecords:
    def test_records_follow_the_outcome(self):
        both = records_for(_risk(), outcome="both", top_n=50, as_of=date(2026, 9, 20))
        recs = [r for r in both if r["kind"] == "recommendation"]
        alerts = [r for r in both if r["kind"] == "alert"]
        assert [r["subject_id"] for r in recs] == [
            "1",
            "2",
            "4",
        ]  # low risk is not a recommendation
        assert [r["subject_id"] for r in alerts] == [
            "1",
            "4",
        ]  # alerts for the high ones
        assert recs[0]["action"]["code"] == "COMPETITIVE_RESPONSE"
        assert (
            recs[2]["action"]["code"] == "RELEASE"
        )  # $1,000 stalled past twice the usual
        assert alerts[0]["action"] is None
        assert all(r["model_version"] == "abc123" for r in both)
        assert recs[0]["owner"] == "M. Nambiar" and alerts[1]["owner"] == "S. Mehta"
        assert recs[0]["subject_label"] == "Quote 1 · Griddles · $9,000"
        assert recs[0]["context"]["account"]["segment"] == "Champions"
        # The horizon: the rest of the usual range for one still inside it (26 - 17 = 9 days).
        assert recs[0]["expires_at"].date() == date(2026, 9, 29)
        # A stalled one gets one more usual range.
        assert recs[2]["expires_at"].date() == date(2026, 10, 16)

    def test_only_alerts_or_only_recommendations_and_top_n(self):
        only_alerts = records_for(
            _risk(), outcome="alert", top_n=50, as_of=date(2026, 9, 20)
        )
        assert {r["kind"] for r in only_alerts} == {"alert"}
        one = records_for(
            _risk(), outcome="recommend", top_n=1, as_of=date(2026, 9, 20)
        )
        assert len(one) == 1 and one[0]["kind"] == "recommendation"


class FakeStore:
    written: List[Dict[str, Any]] = []

    def __init__(self, engine: Any) -> None:
        pass

    def upsert_open(
        self, use_case_id: str, run_id: Any, records: Any
    ) -> Dict[str, int]:
        FakeStore.written = [
            {"use_case_id": use_case_id, "run_id": run_id, **r} for r in records
        ]
        return {"created": len(records), "refreshed": 0, "kept": 0}


class TestExecutor:
    def _patch(self, monkeypatch) -> None:
        FakeStore.written = []
        monkeypatch.setattr(recommend_module, "RecommendationStore", FakeStore)
        monkeypatch.setattr(
            recommend_module, "apply_recommendations_ddl", lambda engine: ("t",)
        )

        class Engine:
            def dispose(self):
                return None

        monkeypatch.setattr(
            recommend_module, "build_control_plane_engine", lambda settings: Engine()
        )

    def test_live_run_writes_and_carries(self, monkeypatch):
        self._patch(monkeypatch)
        step = StepInput(
            previous_step_content={"kpis": {"rfq__stalled_quotes": 5}, "risk": _risk()},
            additional_data={
                "use_case_id": "uc_1",
                "run_id": "run_1",
                "as_of": "2026-09-20",
            },
        )
        out = asyncio.run(recommend(step))
        assert out.success
        result = out.content["recommendation_result"]
        assert result["written"] == {"created": 5, "refreshed": 0, "kept": 0}
        assert result["held"] is False
        assert result["summary"] == {
            "recommendations": 3,
            "alerts": 2,
            "value_at_stake": 16000.0,
            "owners": 2,
        }
        assert result["plays"] == {
            "COMPETITIVE_RESPONSE": 1,
            "RE_ENGAGE": 1,
            "RELEASE": 1,
        }
        assert (
            FakeStore.written[0]["use_case_id"] == "uc_1"
            and FakeStore.written[0]["run_id"] == "run_1"
        )
        assert (
            out.content["kpis"] == {"rfq__stalled_quotes": 5} and "risk" in out.content
        )
        assert (
            recommendations_of(StepInput(previous_step_content=out.content)) == result
        )

    def test_dry_run_holds_the_write(self, monkeypatch):
        self._patch(monkeypatch)
        step = StepInput(
            previous_step_content={"risk": _risk()},
            additional_data={
                "use_case_id": "uc_1",
                "run_id": "run_1",
                "dry_run": True,
                "outcome": "alert",
            },
        )
        out = asyncio.run(recommend(step))
        result = out.content["recommendation_result"]
        assert result["held"] is True and result["written"]["created"] == 0
        assert FakeStore.written == []
        assert (
            result["summary"]["recommendations"] == 0
            and result["summary"]["alerts"] == 2
        )

    def test_without_a_scoring_it_stops(self):
        out = asyncio.run(recommend(StepInput(previous_step_content={"kpis": {}})))
        assert not out.success and out.stop

    def test_result_found_in_earlier_outputs(self):
        step = StepInput(
            previous_step_content={"explanation": {}},
            previous_step_outputs=[
                StepOutput(content={"recommendation_result": {"plays": {}}})
            ],
        )
        assert recommendations_of(step) == {"plays": {}}


# --- the API (1.13.0) ---------------------------------------------------------------------------


class MemoryStore:
    """The recommendations store's contract in memory, for the router."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.deleted: List[str] = []

    def upsert_open(self, use_case_id, run_id, records):
        for r in records:
            row = {
                **r,
                "use_case_id": use_case_id,
                "run_id": run_id,
                "status": "open",
                "status_note": "",
                "status_by": "",
                "status_at": None,
                "outcome": None,
                "outcome_at": None,
                "created_at": "2026-09-20T06:00:00Z",
                "updated_at": "2026-09-20T06:00:00Z",
            }
            self.rows[r["id"]] = row
        return {"created": len(records), "refreshed": 0, "kept": 0}

    def get(self, recommendation_id):
        return self.rows.get(recommendation_id)

    def list(
        self,
        *,
        use_case_id=None,
        status=None,
        kind=None,
        level=None,
        owner=None,
        limit=200,
    ):
        out = [
            r
            for r in self.rows.values()
            if (not use_case_id or r["use_case_id"] == use_case_id)
            and (not status or r["status"] == status)
            and (not kind or r["kind"] == kind)
            and (not level or r["risk_level"] == level)
            and (not owner or r["owner"] == owner)
        ]
        return sorted(out, key=lambda r: -r["value_at_stake"])[:limit]

    def counts(self, use_case_id=None):
        out = {s: 0 for s in ("open", "accepted", "dismissed", "actioned", "expired")}
        for r in self.rows.values():
            if not use_case_id or r["use_case_id"] == use_case_id:
                out[r["status"]] += 1
        return out

    def set_status(self, recommendation_id, status, *, note="", by=""):
        row = self.rows[recommendation_id]
        row.update(
            {
                "status": status,
                "status_note": note,
                "status_by": by,
                "status_at": "2026-09-20T07:00:00Z",
            }
        )
        return row

    def delete_for_use_case(self, use_case_id):
        self.deleted.append(use_case_id)
        gone = [k for k, r in self.rows.items() if r["use_case_id"] == use_case_id]
        for k in gone:
            del self.rows[k]
        return len(gone)


def test_recommendation_endpoints_read_and_write_the_store():
    from contextlib import contextmanager

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from egpt_retail.api.routers import use_cases as module

    class Store:
        async def aget(self, use_case_id):
            return {"id": use_case_id} if use_case_id == "uc_1" else None

        async def adelete(self, use_case_id):
            return use_case_id == "uc_1"

    class Service:
        store = Store()

    memory = MemoryStore()
    memory.upsert_open(
        "uc_1",
        "run_1",
        records_for(_risk(), outcome="both", top_n=50, as_of=date(2026, 9, 20)),
    )

    @contextmanager
    def client():
        app = FastAPI()

        @app.middleware("http")
        async def _fake_auth(request: Request, call_next):
            request.state.authorization_enabled = False
            request.state.scopes = []
            request.state.admin_scope = None
            return await call_next(request)

        app.include_router(module.router)
        app.dependency_overrides[module.use_case_service] = lambda: Service()
        app.dependency_overrides[module.recommendation_store] = lambda: memory
        with TestClient(app) as c:
            yield c

    with client() as c:
        listed = c.get("/use-cases/uc_1/recommendations")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["counts"]["open"] == 5
        assert len(body["items"]) == 5
        assert body["items"][0]["subject_label"].startswith("Quote 1 ")
        assert body["items"][0]["action"]["code"] == "COMPETITIVE_RESPONSE"
        assert c.get("/use-cases/uc_missing/recommendations").status_code == 404

        alerts = c.get(
            "/recommendations", params={"kind": "alert", "owner": "S. Mehta"}
        ).json()
        assert len(alerts["items"]) == 1 and alerts["items"][0]["action"] is None

        rec_id = body["items"][0]["id"]
        one = c.get(f"/recommendations/{rec_id}")
        assert one.status_code == 200 and one.json()["risk_level"] == "high"
        assert c.get("/recommendations/rec_nope").status_code == 404

        decided = c.post(
            f"/recommendations/{rec_id}/status",
            json={"status": "accepted", "note": "called", "by": "kiruthiga"},
        )
        assert decided.status_code == 200, decided.text
        assert (
            decided.json()["status"] == "accepted"
            and decided.json()["status_by"] == "kiruthiga"
        )
        assert (
            c.get(
                "/use-cases/uc_1/recommendations", params={"status": "accepted"}
            ).json()["counts"]["accepted"]
            == 1
        )
        assert (
            c.post(
                "/recommendations/rec_nope/status", json={"status": "accepted"}
            ).status_code
            == 404
        )

        assert c.delete("/use-cases/uc_1").status_code == 204
        assert memory.deleted == ["uc_1"] and memory.rows == {}


# --- the kind (the contract and the compiler) ----------------------------------------------------


def test_the_at_risk_kind_compiles_to_the_inform_chain_with_its_three_stages():
    from egpt_retail.contracts.use_case import (
        TEMPLATE_PARAMETERS,
        TEMPLATE_SHAPE,
        UseCaseShape,
        UseCaseTemplate,
    )
    from egpt_retail.orchestration.compiler import compile_workflow

    assert TEMPLATE_SHAPE[UseCaseTemplate.AT_RISK] is UseCaseShape.INFORM
    assert TEMPLATE_PARAMETERS[UseCaseTemplate.AT_RISK][:3] == (
        "metrics",
        "churn window",
        "outcome",
    )
    executors = frozenset(
        {
            "read_gold",
            "compare_to_baseline",
            "track_outcomes",
            "score_risk",
            "recommend",
            "send_alerts",
            "check_report_grounding",
            "record_provenance",
            "render_report",
            "deliver_in_app",
            "send_report",
        }
    )
    spec = {
        "shape": "inform",
        "template": "at_risk",
        "question": "Which open quotes are at risk?",
        "parameters": {
            "metrics": ["rfq__stalled_quotes"],
            "schedule": "weekly",
            "recipient": "sales@example.com",
        },
    }
    report = compile_workflow(spec, use_case_id="uc_1", registered_executors=executors)
    assert report.template.value == "at_risk"
    assert report.unfilled_slots == []
    # The defaults the form does not ask for.
    assert report.parameters["churn window"] == 60
    assert report.parameters["outcome"] == "both"
    assert report.parameters["top n"] == 0  # every quote still within its usual range
    assert report.parameters["outputs"] == ["report", "dashboard"]
    names = [s["name"] for s in report.config["steps"]]
    assert names == [
        "Read gold",
        "Take the numbers as they are",
        "Track what happened",
        "Score risk",
        "Recommend",
        "Explain",
        "Check grounding",
        "Record provenance",
        "Render report",
        "Deliver",
        "Send report",
        "Send alerts",
    ]


def test_the_suggestion_reads_an_at_risk_ask_and_offers_the_context_kpis():
    from egpt_retail.usecases.analytics_suggest import suggest_analytics

    registry = [
        {
            "identifier": i,
            "name": i,
            "grain": "overall",
            "active": True,
            "business_area": "sales",
        }
        for i in (
            "rfq__stalled_quotes",
            "rfq__customer_churn_rate_60d",
            "rfq__closed_won_rate",
        )
    ]
    got = suggest_analytics(
        "Which accounts are at risk of churn, and which quotes will not convert?",
        registry,
    )
    assert got.kind == "at_risk"
    assert [k["identifier"] for k in got.kpis][:2] == [
        "rfq__stalled_quotes",
        "rfq__customer_churn_rate_60d",
    ]
    assert got.comparison is None
    assert "report" in got.outputs
    assert (
        suggest_analytics("Why did closed won rate drop?", registry).kind
        == "diagnostic"
    )


# --- track_outcomes -----------------------------------------------------------------------------


def test_outcome_of_reads_the_stage_against_the_horizon():
    from datetime import datetime, timezone

    from egpt_retail.executors.recommend import outcome_of

    end = datetime(2026, 9, 29, tzinfo=timezone.utc)
    assert outcome_of("Closed Won", end, date(2026, 9, 30)) == "converted"
    assert outcome_of("Closed Lost", end, date(2026, 9, 20)) == "lapsed"
    assert outcome_of("Proposal Sent", end, date(2026, 9, 30)) == "lapsed"
    assert outcome_of("Proposal Sent", end, date(2026, 9, 25)) == "still_open"


class OutcomeStore(MemoryStore):
    def due(self, use_case_id, *, before=None):
        return [
            r
            for r in self.rows.values()
            if r["use_case_id"] == use_case_id
            and r.get("outcome") is None
            and r.get("expires_at") is not None
            and r["expires_at"] <= before
        ]

    def set_outcome(self, recommendation_id, outcome, *, at=None):
        row = self.rows[recommendation_id]
        row["outcome"] = outcome
        row["outcome_at"] = at
        if row["status"] == "open":
            row["status"] = "expired"
        return row


def test_track_outcomes_writes_what_happened_and_reports_precision(monkeypatch):
    from datetime import datetime, timezone

    from egpt_retail.executors.recommend import track_outcomes

    store = OutcomeStore()
    records = records_for(_risk(), outcome="both", top_n=50, as_of=date(2026, 9, 1))
    store.upsert_open("uc_1", "run_0", records)
    # Every record's horizon ended a while ago; the fact says quote 1 converted, 2 is still
    # open, 4 was lost.
    for r in store.rows.values():
        r["expires_at"] = datetime(2026, 9, 10, tzinfo=timezone.utc)
    stages = {"1": "Closed Won", "2": "Proposal Sent", "4": "Closed Lost"}

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self._rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, statement, params):
            return Result(
                [
                    {"quote_id": q, "pipeline_stage": stages[q]}
                    for q in params["ids"]
                    if q in stages
                ]
            )

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            return None

    monkeypatch.setattr(recommend_module, "RecommendationStore", lambda engine: store)
    monkeypatch.setattr(
        recommend_module, "apply_recommendations_ddl", lambda engine: ("t",)
    )
    monkeypatch.setattr(
        recommend_module, "build_control_plane_engine", lambda settings: Engine()
    )
    import egpt_retail.executors.predict as predict_module

    monkeypatch.setattr(predict_module, "_engine", lambda settings: Engine())

    out = asyncio.run(
        track_outcomes(
            StepInput(
                previous_step_content={"kpis": {}},
                additional_data={"use_case_id": "uc_1", "as_of": "2026-09-20"},
            )
        )
    )
    assert out.success
    o = out.content["outcomes"]
    assert o["status"] == "written" and o["checked"] == 5 and o["written"] == 5
    # Quote 1 (high, converted), 2 (medium, lapsed past horizon), 4 (high, lost) -- as
    # recommendations and alerts both.
    assert o["this_run"]["high"] == {"converted": 2, "lapsed": 2, "still_open": 0}
    assert o["this_run"]["medium"] == {"converted": 0, "lapsed": 1, "still_open": 0}
    assert (
        o["precision"]["high"]["named"] == 4
        and o["precision"]["high"]["did_not_convert"] == 2
    )
    assert o["precision"]["high"]["rate"] == 0.5
    assert all(r["outcome"] for r in store.rows.values())
    assert {r["status"] for r in store.rows.values()} == {"expired"}
    assert out.content["kpis"] == {}

    held = asyncio.run(
        track_outcomes(
            StepInput(additional_data={"use_case_id": "uc_1", "dry_run": True})
        )
    )
    assert held.content["outcomes"]["status"] == "held"


# --- the report email carries the full report as a file --------------------------------------


def test_send_mail_attaches_the_full_report():
    from egpt_retail.documents.mailbox import MailboxConfig, send_mail

    sent = {}

    class Smtp:
        def __init__(self, host, port):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            sent["message"] = message

        def quit(self):
            pass

    config = MailboxConfig(
        user="orders@example.com",
        password="x",
        imap_host="imap.example",
        smtp_host="smtp.example",
        smtp_port=587,
    )
    send_mail(
        config,
        to=["sales@example.com"],
        subject="report",
        body="text",
        html="<p>top of the list</p>",
        attachments=[
            (
                "at-risk-pipeline-2026-09-21.html",
                b"<!doctype html><html>full</html>",
                "text/html",
            )
        ],
        smtp_factory=Smtp,
    )
    message = sent["message"]
    parts = list(message.iter_attachments())
    assert [p.get_filename() for p in parts] == ["at-risk-pipeline-2026-09-21.html"]
    assert parts[0].get_content_type() == "text/html"
    assert b"full" in parts[0].get_payload(decode=True)
    assert (
        message.get_body(preferencelist=("html",)).get_content().strip()
        == "<p>top of the list</p>"
    )
