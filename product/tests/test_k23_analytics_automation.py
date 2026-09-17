"""K23: Analytics Automation -- the Inform shape run by the platform.

Offline: gold is a fake, the explanation is a stand-in, the mailbox is dry-run. What is
held: the comparison's arithmetic and its honesty (no baseline is said, not invented; a
rate is never summed across a breakdown; a series is shown, not compared), the grounding
check's rule (every figure in the narrative is one the numbers carried, dates excluded, a
mistyped KPI id repaired), the report's two renderings, the suggestion from the ask, and
the compiler's Inform sequence with its defaults.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.contracts.use_case import SHAPE_PARAMETERS, UseCaseShape
from egpt_retail.executors import analytics
from egpt_retail.executors.analytics import (
    check_report_grounding,
    compare_to_baseline,
    render_report,
    report_html,
    send_report,
)
from egpt_retail.orchestration.compiler import (
    SHAPE_STAGES,
    ParameterOrigin,
    compile_workflow,
    normalise_config,
)
from egpt_retail.usecases.analytics_suggest import suggest_analytics
from egpt_retail.usecases.runtime import make_explanation_step

AS_OF = "2026-09-17"
BASE = "2026-08-20"


def _row(value: Any, **keys: Any) -> Dict[str, Any]:
    return {
        "value": value,
        "as_of": AS_OF,
        "computed_at": "2026-09-17T06:00:00Z",
        "run_id": "r",
        "source_snapshot": "s",
        **keys,
    }


GOLD: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    AS_OF: {
        "rfq__quotes_generated": [_row(358)],
        "rfq__closed_won_rate": [_row(55.8)],
        "rfq__quotes_by_brand": [_row(20, brand="Adidas"), _row(13, brand="Apple")],
        "rfq__closed_won_rate_by_brand": [
            _row(60.0, brand="Adidas"),
            _row(50.0, brand="Apple"),
        ],
        "rfq__quotes_by_week": [
            _row(90, week="2026-09-07"),
            _row(104, week="2026-09-14"),
        ],
    },
    BASE: {
        "rfq__quotes_generated": [_row(384)],
        "rfq__quotes_by_brand": [_row(13, brand="Adidas"), _row(20, brand="Apple")],
        "rfq__closed_won_rate_by_brand": [
            _row(40.0, brand="Adidas"),
            _row(50.0, brand="Apple"),
        ],
    },
}
DEFINITIONS = {
    "rfq__quotes_generated": {
        "name": "Quotes generated",
        "unit": "quotes",
        "grain": "overall",
        "window_days": 28,
    },
    "rfq__closed_won_rate": {
        "name": "Closed-won rate",
        "unit": "%",
        "grain": "overall",
        "window_days": 28,
    },
    "rfq__quotes_by_brand": {
        "name": "Quotes by brand",
        "unit": "quotes",
        "grain": "brand",
        "window_days": 28,
    },
    "rfq__closed_won_rate_by_brand": {
        "name": "Closed-won rate by brand",
        "unit": "%",
        "grain": "brand",
        "window_days": 28,
    },
    "rfq__quotes_by_week": {
        "name": "Quotes by week",
        "unit": "quotes",
        "grain": "time_period",
        "window_days": 28,
    },
}


def _fake_gold(monkeypatch, *, backfill_writes: List[str]):
    async def read_gold(step_input: StepInput) -> StepOutput:
        options = step_input.additional_data or {}
        as_of = str(options.get("as_of") or AS_OF)
        held = GOLD.get(as_of, {})
        wanted = [str(i) for i in options.get("kpi_identifiers") or []]
        kpis = {
            i: {"as_of": as_of, "computed_at": "x", "rows": held[i]}
            for i in wanted
            if i in held
        }
        missing = [
            {"kpi_identifier": i, "reason": f"no gold row at as_of {as_of}"}
            for i in wanted
            if i not in held
        ]
        return StepOutput(content={"kpis": kpis, "missing": missing}, success=True)

    async def materialise_gold(step_input: StepInput) -> StepOutput:
        options = step_input.additional_data or {}
        as_of = str(options.get("as_of"))
        for identifier in options.get("kpi_identifiers") or []:
            backfill_writes.append(f"{identifier}@{as_of}")
            if identifier == "rfq__closed_won_rate":
                GOLD.setdefault(as_of, {})[identifier] = [_row(47.3)]
        return StepOutput(content={"results": []}, success=True)

    monkeypatch.setattr(analytics, "read_gold", read_gold)
    monkeypatch.setattr(analytics, "materialise_gold", materialise_gold)


def _run(fn, previous=None, **options):
    return asyncio.run(
        fn(StepInput(previous_step_content=previous, additional_data=options))
    )


class TestCompareToBaseline:
    def test_scalars_breakdowns_rates_and_series(self, monkeypatch):
        writes: List[str] = []
        _fake_gold(monkeypatch, backfill_writes=writes)
        out = _run(
            compare_to_baseline,
            as_of=AS_OF,
            comparison="prior_period",
            metrics=list(DEFINITIONS),
            kpi_definitions=DEFINITIONS,
        )
        assert out.success
        by = {k["identifier"]: k for k in out.content["kpis"]}
        # A scalar: current, baseline, delta and percent.
        q = by["rfq__quotes_generated"]
        assert (q["current"], q["baseline"], q["delta"], q["delta_pct"]) == (
            358,
            384,
            -26,
            -6.8,
        )
        assert q["baseline_as_of"] == BASE
        # A breakdown adds up and ranks its movers.
        b = by["rfq__quotes_by_brand"]
        assert (b["current"], b["baseline"]) == (33, 33)
        assert [(m["brand"], m["delta"]) for m in b["movers"]] == [
            ("Adidas", 7),
            ("Apple", -7),
        ]
        # A rate across a breakdown is never summed; its movers are the story.
        r = by["rfq__closed_won_rate_by_brand"]
        assert r["current"] is None and r["baseline"] is None
        assert r["movers"][0] == {
            "brand": "Adidas",
            "current": 60.0,
            "baseline": 40.0,
            "delta": 20.0,
            "delta_pct": 50.0,
        }
        # A series is shown, not compared.
        w = by["rfq__quotes_by_week"]
        assert w["delta"] is None and len(w["rows"]) == 2 and "series" in w["reason"]
        # A missing baseline was materialised for its date, once, and then read.
        assert writes == [f"rfq__closed_won_rate@{BASE}"]
        assert by["rfq__closed_won_rate"]["baseline"] == 47.3
        assert out.content["backfilled"] == ["rfq__closed_won_rate"]

    def test_no_backfill_says_so(self, monkeypatch):
        writes: List[str] = []
        GOLD[BASE].pop("rfq__closed_won_rate", None)
        _fake_gold(monkeypatch, backfill_writes=writes)
        out = _run(
            compare_to_baseline,
            as_of=AS_OF,
            metrics=["rfq__closed_won_rate"],
            kpi_definitions=DEFINITIONS,
            backfill_baseline=False,
        )
        k = out.content["kpis"][0]
        assert k["baseline"] is None and "no gold row" in k["reason"] and writes == []

    def test_prior_year_goes_a_year_back(self, monkeypatch):
        _fake_gold(monkeypatch, backfill_writes=[])
        out = _run(
            compare_to_baseline,
            as_of=AS_OF,
            comparison="prior_year",
            metrics=["rfq__quotes_generated"],
            kpi_definitions=DEFINITIONS,
            backfill_baseline=False,
        )
        assert out.content["kpis"][0]["baseline_as_of"] == "2025-09-17"


def _comparison() -> Dict[str, Any]:
    return {
        "as_of": AS_OF,
        "comparison": "prior_period",
        "comparison_label": "the period before",
        "kpis": [
            {
                "identifier": "rfq__quotes_generated",
                "name": "Quotes generated",
                "unit": "quotes",
                "grain": "overall",
                "current": 358.0,
                "baseline": 384.0,
                "delta": -26.0,
                "delta_pct": -6.8,
                "rows": [],
                "movers": [],
            },
            {
                "identifier": "rfq__quotes_by_brand",
                "name": "Quotes by brand",
                "unit": "quotes",
                "grain": "brand",
                "current": 33.0,
                "baseline": 33.0,
                "delta": 0.0,
                "delta_pct": 0.0,
                "rows": [
                    {
                        "brand": "Adidas",
                        "current": 20.0,
                        "baseline": 13.0,
                        "delta": 7.0,
                        "delta_pct": 53.8,
                    },
                    {
                        "brand": "Apple",
                        "current": 13.0,
                        "baseline": 20.0,
                        "delta": -7.0,
                        "delta_pct": -35.0,
                    },
                ],
                "movers": [
                    {
                        "brand": "Adidas",
                        "current": 20.0,
                        "baseline": 13.0,
                        "delta": 7.0,
                        "delta_pct": 53.8,
                    }
                ],
            },
        ],
        "missing": [],
    }


class TestGrounding:
    def test_a_narrative_in_the_numbers_passes_dates_excluded(self):
        explanation = {
            "headline": "Quotes generated fell to 358 from 384 (-6.8%) as of 2026-09-17.",
            "findings": [
                {
                    "kpi_identifier": "rfq__quotes_by_brand",
                    "text": "Adidas rose to 20 from 13, up 7 (53.8%); Apple fell by 7.",
                }
            ],
            "caveats": ["The baseline is 2026-08-20."],
        }
        out = _run(
            check_report_grounding,
            previous={"comparison_result": _comparison(), "explanation": explanation},
        )
        g = out.content["grounding"]
        assert g["passed"] and g["unsupported"] == [] and g["checked"] == 8
        assert out.content["explanation"]["headline"].startswith("Quotes generated")

    def test_an_invented_figure_withholds_the_narrative(self):
        explanation = {
            "headline": "Quotes generated fell to 358 from 400.",
            "findings": [],
            "caveats": [],
        }
        out = _run(
            check_report_grounding,
            previous={"comparison_result": _comparison(), "explanation": explanation},
        )
        assert out.content["grounding"]["passed"] is False
        assert out.content["grounding"]["unsupported"] == ["400"]
        assert (
            out.content["explanation"] is None
            and out.content["explanation_rejected"] == explanation
        )

    def test_a_mistyped_kpi_id_is_repaired_not_fatal(self):
        explanation = {
            "headline": "Quotes fell to 358.",
            "findings": [
                {
                    "kpi_identifier": "rfq__rfq__quotes_by_brand",
                    "text": "Adidas rose to 20.",
                }
            ],
            "caveats": [],
        }
        out = _run(
            check_report_grounding,
            previous={"comparison_result": _comparison(), "explanation": explanation},
        )
        assert out.content["grounding"]["passed"]
        assert (
            out.content["explanation"]["findings"][0]["kpi_identifier"]
            == "rfq__quotes_by_brand"
        )
        assert out.content["grounding"]["repaired_kpi_ids"] == [
            "rfq__rfq__quotes_by_brand"
        ]

    def test_no_explanation_is_numbers_only_not_a_failure(self):
        out = _run(
            check_report_grounding, previous={"comparison_result": _comparison()}
        )
        assert out.success and out.content["explanation"] is None


class TestReport:
    def test_render_and_dry_run_send(self):
        explanation = {
            "headline": "Quotes generated fell to 358 from 384.",
            "findings": [
                {"kpi_identifier": "rfq__quotes_generated", "text": "Down 26."}
            ],
            "caveats": [],
        }
        checked = _run(
            check_report_grounding,
            previous={"comparison_result": _comparison(), "explanation": explanation},
        ).content
        rendered = _run(
            render_report,
            previous=checked,
            title="RFQ weekly",
            dashboard_url="http://x/d/1",
            use_case_title="RFQ",
        ).content
        html = rendered["report"]["html"]
        assert (
            "RFQ weekly" in html
            and "Quotes by brand" in html
            and "http://x/d/1" in html
        )
        assert "Quotes generated fell to 358 from 384." in html
        assert "358" in rendered["report"]["text"] and rendered["headline"].startswith(
            "Quotes generated"
        )
        sent = _run(
            send_report,
            previous=rendered,
            recipient="a@example.com, b@example.com",
            dry_run=True,
        ).content
        assert sent["delivery"]["status"] == "held" and sent["delivery"]["to"] == [
            "a@example.com",
            "b@example.com",
        ]
        assert (
            sent["report"]["title"] == "RFQ weekly"
            and sent["comparison_result"]["as_of"] == AS_OF
        )
        none = _run(send_report, previous=rendered, recipient="", dry_run=True).content
        assert none["delivery"]["status"] == "no_recipient"

    def test_a_withheld_narrative_renders_the_numbers_alone(self):
        html = report_html(
            _comparison(),
            None,
            title="T",
            grounding={"passed": False, "unsupported": ["400"]},
        )
        assert "withheld" in html and "Quotes by brand" in html


class TestExplanationStep:
    def test_the_stand_in_and_the_no_model_case(self):
        step = make_explanation_step(None)
        out = asyncio.run(
            step.executor(
                StepInput(
                    previous_step_content=_comparison(),
                    additional_data={
                        "explainer": lambda c: {
                            "headline": f"{len(c['kpis'])} KPIs",
                            "findings": [],
                            "caveats": [],
                        }
                    },
                )
            )
        )
        assert out.content["explanation"]["headline"] == "2 KPIs"
        out = asyncio.run(
            step.executor(
                StepInput(previous_step_content=_comparison(), additional_data={})
            )
        )
        assert (
            out.success
            and out.content["explanation"] is None
            and "no explanation model" in out.content["notes"]
        )


REGISTRY = [
    {
        "identifier": "rfq__quotes_generated",
        "name": "Quotes generated",
        "description": "Quote requests submitted in the trailing 28 days.",
        "business_area": "sales",
        "grain": "overall",
        "concept": "Quote",
        "subject_area": "Sales Pipeline",
        "unit": "quotes",
        "active": True,
    },
    {
        "identifier": "rfq__quotes_by_brand",
        "name": "Quotes by brand",
        "description": "Quote requests, one row per brand.",
        "business_area": "sales",
        "grain": "brand",
        "concept": "Quote",
        "subject_area": "Sales Pipeline",
        "unit": "quotes",
        "active": True,
    },
    {
        "identifier": "rfq__closed_won_rate",
        "name": "Closed-won rate",
        "description": "Share of closed quotes that closed won.",
        "business_area": "sales",
        "grain": "overall",
        "concept": "Quote",
        "subject_area": "Sales Pipeline",
        "unit": "%",
        "active": True,
    },
    {
        "identifier": "rfq__lost_reason_breakdown",
        "name": "Lost reasons",
        "description": "Quotes lost, one row per lost reason.",
        "business_area": "sales",
        "grain": "lost_reason",
        "concept": "Quote",
        "subject_area": "Sales Pipeline",
        "unit": "quotes",
        "active": True,
    },
    {
        "identifier": "websession__bounce_rate",
        "name": "Bounce rate",
        "description": "Sessions that bounced.",
        "business_area": "customer",
        "grain": "overall",
        "concept": "WebSession",
        "subject_area": "Web",
        "unit": "%",
        "active": True,
    },
    {
        "identifier": "old__retired",
        "name": "Quotes retired",
        "description": "Quotes.",
        "business_area": "sales",
        "grain": "overall",
        "concept": "Quote",
        "subject_area": "",
        "unit": "",
        "active": False,
    },
]


class TestSuggestion:
    def test_the_rfq_ask_fills_the_form_from_what_the_data_supports(self):
        out = suggest_analytics(
            "Full visibility into the quote pipeline by brand, closed-won rate and lost reason breakdown. Every Monday to sales@example.com.",
            REGISTRY,
            gold_latest={"rfq__quotes_generated": date(2026, 9, 17)},
        )
        ids = [k["identifier"] for k in out.kpis]
        assert (
            out.kind == "monitoring"
            and out.schedule == "weekly"
            and out.recipients == ["sales@example.com"]
        )
        assert (
            "rfq__quotes_by_brand" in ids
            and "rfq__closed_won_rate" in ids
            and "rfq__lost_reason_breakdown" in ids
        )
        assert "websession__bounce_rate" not in ids and "old__retired" not in ids
        assert set(out.dimensions) >= {"brand", "lost_reason"}
        assert out.outputs == ["dashboard", "report"] and out.business_areas == [
            "sales"
        ]
        by = {k["identifier"]: k for k in out.kpis}
        assert by["rfq__quotes_generated"]["feasibility"] == "ready"
        assert by["rfq__quotes_by_brand"]["feasibility"] == "buildable"
        assert "brand" in by["rfq__quotes_by_brand"]["why"]

    def test_a_diagnostic_question_is_a_one_off_report(self):
        out = suggest_analytics("Why is the closed-won rate low last week?", REGISTRY)
        assert out.kind == "diagnostic" and out.outputs == ["report"]
        assert [k["identifier"] for k in out.kpis][0] == "rfq__closed_won_rate"
        assert (
            "last year" not in out.comparison_reason
            and out.comparison == "prior_period"
        )

    def test_last_year_and_uncovered_words(self):
        out = suggest_analytics(
            "Compare quotes generated with last year; also tell me about churn.",
            REGISTRY,
        )
        assert out.comparison == "prior_year" and "churn" in out.uncovered


class TestCompilerInform:
    def test_the_inform_sequence_and_its_defaults(self):
        spec = {
            "use_case_id": "rfq_weekly",
            "question": "Weekly RFQ pipeline",
            "shape": "inform",
            "parameters": {
                "metrics": ["rfq__quotes_generated"],
                "dimensions": ["brand"],
                "comparison": "prior_period",
                "schedule": "weekly",
                "recipient": "a@example.com",
            },
        }
        executors = {
            stage.executor_ref
            for stage in SHAPE_STAGES[UseCaseShape.INFORM]
            if stage.executor_ref
        }
        compiled = compile_workflow(spec, registered_executors=executors)
        assert compiled.publishable, compiled.refusal_reasons
        assert [s["name"] for s in normalise_config(compiled.config)] == [
            "Read gold",
            "Compare to baseline",
            "Explain",
            "Check grounding",
            "Record provenance",
            "Render report",
            "Deliver",
            "Send report",
        ]
        assert compiled.parameters[
            "title"
        ] == "Weekly RFQ pipeline" and compiled.parameters["outputs"] == ["report"]
        assert compiled.parameter_origins["title"] is ParameterOrigin.DEFAULT
        assert set(SHAPE_PARAMETERS[UseCaseShape.INFORM]) >= {
            "window",
            "title",
            "outputs",
        }
