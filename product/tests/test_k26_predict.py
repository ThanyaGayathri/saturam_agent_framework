"""K26: the at-risk kind's predictive stage -- the model, the RFM rule, the feature SQL,
training with a time holdout, the drivers and the executor over a stubbed warehouse."""

from __future__ import annotations

import asyncio
import random
from datetime import date, timedelta
from typing import Any, Dict, List

import numpy as np
import pytest
from egpt.workflow.types import StepInput

from egpt_retail.executors import predict as predict_module
from egpt_retail.executors.predict import (
    AUC_FLOOR,
    _drivers,
    _level,
    risk_of,
    score_risk,
    train_and_evaluate,
)
from egpt_retail.predict.features import CATEGORICAL, NUMERIC, scoring_sql, training_sql
from egpt_retail.predict.model import (
    auc,
    brier,
    calibration,
    fit_logistic,
    predict_proba,
)
from egpt_retail.predict.rfm import RFM_SEGMENTS, rfm_segments, segment_of


def _synthetic(n: int, *, seed: int = 7) -> tuple:
    """Rows whose label depends on age ratio and category, so a model can learn them."""
    rng = random.Random(seed)
    rows, labels = [], []
    for i in range(n):
        ratio = rng.uniform(0.0, 3.0)
        category = rng.choice(["Ranges", "Griddles", "Fryers"])
        logit = 3.0 - 2.5 * ratio + (1.5 if category == "Fryers" else 0.0)
        p = 1 / (1 + np.exp(-logit))
        rows.append(
            {
                "quote_id": i,
                "quote_date": str(date(2026, 1, 1) + timedelta(days=i % 200)),
                "age_ratio": ratio,
                "age_days": ratio * 28,
                "usual_days": 28,
                "pim_category": category,
                "deal_value_usd": rng.uniform(500, 9000),
                "sales_rep_territory": rng.choice(["East", "West"]),
                "pipeline_stage": "Closed Won" if rng.random() < p else "Closed Lost",
                "days_to_close": 10 if rng.random() < p else 40,
            }
        )
        labels.append(int(rng.random() < p))
    return rows, labels


class TestModel:
    def test_learns_a_signal_and_reports_contributions(self):
        rows, labels = _synthetic(600)
        fitted = fit_logistic(
            rows[:450],
            labels[:450],
            numeric=["age_ratio"],
            categorical=["pim_category"],
        )
        probs, contributions = predict_proba(fitted, rows[450:])
        assert auc(labels[450:], probs) > 0.7
        assert contributions.shape == (150, len(fitted.columns))
        # A higher age ratio pushes against converting.
        j = fitted.columns.index("age_ratio")
        assert fitted.coef[j] < 0
        assert 0.0 <= brier(labels[450:], probs) <= 0.3
        assert len(calibration(labels[450:], probs, bins=5)) <= 5

    def test_auc_is_none_with_one_class(self):
        assert auc([1, 1, 1], [0.2, 0.5, 0.9]) is None

    def test_an_unseen_category_level_scores_without_error(self):
        rows, labels = _synthetic(200)
        fitted = fit_logistic(
            rows, labels, numeric=["age_ratio"], categorical=["pim_category"]
        )
        probs, _ = predict_proba(fitted, [{**rows[0], "pim_category": "Never seen"}])
        assert 0.0 < float(probs[0]) < 1.0


class TestRfm:
    def test_segments_are_the_standard_names(self):
        assert "Champions" in RFM_SEGMENTS and "Lost" in RFM_SEGMENTS
        assert segment_of(5, 5, 5) == "Champions"
        assert segment_of(1, 1, 1) == "Lost"

    def test_quintiles_rank_recent_frequent_big_accounts_first(self):
        accounts = [
            {
                "account_id": i,
                "recency_days": 200 - i * 10,
                "frequency": i,
                "monetary": i * 1000.0,
            }
            for i in range(1, 21)
        ]
        out = rfm_segments(accounts)
        by_id = {a["account_id"]: a for a in out}
        assert by_id[20]["r"] == 5 and by_id[20]["f"] == 5 and by_id[20]["m"] == 5
        assert by_id[20]["segment"] == "Champions"
        assert by_id[1]["r"] == 1 and by_id[1]["segment"] in ("Lost", "Hibernating")

    def test_empty_input(self):
        assert rfm_segments([]) == []


class TestFeatureSql:
    def test_training_reads_only_closed_quotes_by_the_date(self):
        sql = training_sql("silver.dc_rfq_quotes")
        assert ":as_of" in sql
        assert "Closed Won" in sql and "Closed Lost" in sql
        assert "closed_date <=" in sql.replace("  ", " ")
        for column in NUMERIC + CATEGORICAL:
            assert column in sql, column

    def test_scoring_reads_open_quotes(self):
        sql = scoring_sql("silver.dc_rfq_quotes")
        assert "NOT IN ('Closed Won', 'Closed Lost')" in sql
        assert ":as_of" in sql


class TestTraining:
    def test_time_holdout_is_the_newest_quarter(self):
        rows, _ = _synthetic(400)
        fitted, evaluation = train_and_evaluate(rows)
        assert evaluation["holdout_rows"] == 100
        assert evaluation["training_rows"] == 300
        dates = sorted(r["quote_date"] for r in rows)
        assert evaluation["holdout_from"] >= dates[299]
        assert evaluation["auc"] is not None
        assert evaluation["version"]
        assert fitted.columns

    def test_drivers_say_age_once_and_word_the_categories(self):
        rows, _ = _synthetic(300)
        fitted, _ = train_and_evaluate(rows)
        row = {
            **rows[0],
            "age_ratio": 3.0,
            "age_days": 84,
            "usual_days": 28,
            "pim_category": "Griddles",
        }
        _, contributions = predict_proba(fitted, [row])
        typical = {f: 1.0 for f in NUMERIC}
        drivers = _drivers(fitted, row, contributions[0], typical)
        assert drivers
        features = [d["feature"] for d in drivers]
        assert not ({"age_days", "age_ratio"} <= set(features))
        assert all(d["contribution"] < 0 for d in drivers)
        assert all(d["text"] for d in drivers)
        age = next((d for d in drivers if d["feature"] == "age_ratio"), None)
        if age:
            assert "84 of 28 days" in age["text"]

    def test_levels(self):
        levels = {"high": 0.7, "medium": 0.4}
        assert _level(0.9, levels) == "high"
        assert _level(0.5, levels) == "medium"
        assert _level(0.1, levels) == "low"


def _frames() -> Dict[str, List[Dict[str, Any]]]:
    rows, _ = _synthetic(320)
    training = [{**r, **{c: r.get(c) for c in CATEGORICAL}} for r in rows]
    for r in training:
        r.setdefault("customer_id", r["quote_id"] % 40)
    open_rows = []
    for i in range(30):
        open_rows.append(
            {
                "quote_id": 10_000 + i,
                "customer_id": i % 40,
                "customer_name": f"Account {i % 40}",
                "quote_date": str(date(2026, 9, 1)),
                "pipeline_stage": "Proposal",
                "pim_category": ["Ranges", "Griddles", "Fryers"][i % 3],
                "brand": "Brand",
                "sales_rep_territory": "East",
                "sales_rep_name": "Rep",
                "customer_segment": "SMB",
                "quote_source": "Portal",
                "audience_channel": "Web",
                "deal_value_usd": 1000.0 + i * 100,
                "quantity": 1,
                "age_days": 5 + i * 3,
                "usual_days": 28,
                "age_ratio": (5 + i * 3) / 28,
                "prior_quotes": i % 4,
                "prior_win_rate": 0.5,
                "days_since_prev_quote": 30,
                "prior_won_value": 0.0,
                "live_chat_buy": 0,
                "last_lost_reason": "Decision Delayed" if i % 5 == 0 else None,
            }
        )
    accounts = [
        {
            "customer_id": i,
            "customer_name": f"Account {i}",
            "recency_days": 10 + i * 5,
            "frequency": 1 + i % 6,
            "monetary": 500.0 * i,
            "won": i % 3,
            "lost": i % 2,
        }
        for i in range(40)
    ]
    return {"training": training, "scoring": open_rows, "accounts": accounts}


@pytest.fixture
def stubbed_warehouse(monkeypatch):
    frames = _frames()

    def rows(engine, statement, params):
        if "open_quotes" in statement:
            return [{"open_quotes": len(frames["scoring"])}]
        if "recency_days" in statement:
            return frames["accounts"]
        if "NOT IN ('Closed Won', 'Closed Lost')" in statement:
            return frames["scoring"]
        return frames["training"]

    class Guard:
        def check(self, statement):
            return None

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Engine:
        def dispose(self):
            return None

    monkeypatch.setattr(predict_module, "_rows", rows)
    monkeypatch.setattr(predict_module, "_engine", lambda settings: Engine())
    monkeypatch.setattr(
        predict_module, "open_semantic_reader", lambda settings: Reader()
    )
    monkeypatch.setattr(
        predict_module, "build_warehouse_read_guard", lambda reader, row_cap: Guard()
    )
    monkeypatch.setattr(predict_module, "note_sql_execution", lambda *a, **k: None)
    monkeypatch.setattr(predict_module, "note_dataset_read", lambda *a, **k: None)
    return frames


class TestScoreRisk:
    def test_scores_every_open_quote_with_context(self, stubbed_warehouse):
        step = StepInput(
            previous_step_content={
                "kpis": [{"identifier": "rfq__stalled_quotes"}],
                "comparison": "none",
                "as_of": "2026-09-20",
                "outcomes": {"status": "nothing_due"},
            },
            additional_data={"as_of": "2026-09-20"},
        )
        out = asyncio.run(score_risk(step))
        assert out.success, out.content
        # A bare comparison is tucked under its own key; the outcomes ride along.
        assert out.content["comparison_result"]["comparison"] == "none"
        assert out.content["outcomes"] == {"status": "nothing_due"}
        risk = out.content["risk"]
        assert risk["subjects_total"] == 30
        assert risk["summary"]["open_quotes"] == 30
        assert (
            risk["summary"]["high"] + risk["summary"]["medium"] + risk["summary"]["low"]
            == 30
        )
        assert risk["summary"]["within_range"] + risk["summary"]["stalled"] == 30
        assert risk["model"]["auc"] is not None
        first = risk["subjects"][0]
        assert {
            "quote_id",
            "risk",
            "level",
            "drivers",
            "account",
            "value_at_risk",
        } <= set(first)
        assert first["account"]["segment"] in RFM_SEGMENTS
        assert 0.0 <= first["risk"] <= 1.0
        # Still-in-range quotes come before the stalled ones; within each, by the value
        # at risk weighted by what the account is worth.
        stalled_flags = [s["stalled"] for s in risk["subjects"]]
        assert stalled_flags == sorted(stalled_flags)
        in_range = [s for s in risk["subjects"] if not s["stalled"]]
        assert [s["priority"] for s in in_range] == sorted(
            (s["priority"] for s in in_range), reverse=True
        )
        assert all(
            abs(
                s["priority"]
                - round(s["value_at_risk"] * s["account"]["importance"], 2)
            )
            < 0.02
            for s in risk["subjects"]
        )
        assert {s["account"]["importance"] for s in risk["subjects"]} <= {
            0.8,
            0.9,
            1.0,
            1.2,
            1.4,
            1.5,
        }
        assert all({"r", "f", "m"} <= set(s["account"]) for s in risk["subjects"])
        assert risk_of(StepInput(previous_step_content=out.content)) == risk

    def test_churn_window_slot_flags_accounts(self, stubbed_warehouse):
        step = StepInput(additional_data={"as_of": "2026-09-20", "churn_window": 100})
        out = asyncio.run(score_risk(step))
        assert out.content["risk"]["churn_days"] == 100
        # Accounts with recency >= 100 days: recency 10 + 5i >= 100 -> i >= 18 -> 22 of 40.
        assert out.content["risk"]["summary"]["at_risk_accounts"] == 22

    def test_too_little_history_refuses(self, stubbed_warehouse, monkeypatch):
        monkeypatch.setattr(
            predict_module,
            "_rows",
            lambda e, s, p: [] if "Closed Lost')" in s and "NOT IN" not in s else [],
        )
        out = asyncio.run(
            score_risk(StepInput(additional_data={"as_of": "2026-09-20"}))
        )
        assert not out.success and out.stop
        assert "needs at least 50" in out.content["refused"]

    def test_readiness_reports_the_holdout_against_the_floor(self, stubbed_warehouse):
        result = predict_module.risk_model_readiness(as_of=date(2026, 9, 20))
        assert result["open_quotes"] == 30
        assert result["auc_floor"] == AUC_FLOOR
        assert result["ready"] == (result["auc"] >= AUC_FLOOR)

    def test_risk_of_finds_the_result_in_earlier_outputs(self):
        from egpt.workflow.types import StepOutput

        step = StepInput(
            previous_step_content={"explanation": {}},
            previous_step_outputs=[StepOutput(content={"risk": {"summary": {}}})],
        )
        assert risk_of(step) == {"summary": {}}
        assert risk_of(StepInput(previous_step_content="text")) is None
