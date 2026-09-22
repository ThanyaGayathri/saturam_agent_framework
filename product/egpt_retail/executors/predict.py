"""The at-risk kind's predictive stage (K26): ``score_risk``.

Trains the conversion model on the fact's closed history (a time-ordered holdout kept
back for the numbers readiness shows), scores every open quote at the run's date, attaches
the account's RFM context, and turns each row's feature contributions into drivers a
narrative can quote. Deterministic given the data; nothing here calls a language model.

Slots: ``metrics`` (the KPIs the use case reads, for the record), ``churn window`` (days
without a quote before an account is at risk, default 60), ``risk levels`` (the
probability lines, default high >= 0.7, medium >= 0.4), ``top n`` (subjects carried in
detail, default 50). ``additional_data``: ``as_of``, ``fact`` (default the quote fact).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from egpt.exceptions import SQLGuardRefusal
from egpt.workflow.types import StepInput, StepOutput
from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.app.db import build_warehouse_engine, build_warehouse_readonly_engine
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.app.sql_guard import build_warehouse_read_guard
from egpt_retail.executors.batch import earlier_outputs, slot
from egpt_retail.executors.provenance import note_dataset_read, note_sql_execution
from egpt_retail.predict.features import (
    CATEGORICAL,
    NUMERIC,
    scoring_sql,
    training_sql,
)
from egpt_retail.predict.model import (
    Fitted,
    auc,
    brier,
    calibration,
    fit_logistic,
    predict_proba,
)
from egpt_retail.predict.rfm import importance_of, rfm_segments

__all__ = [
    "focus_of",
    "risk_of",
    "risk_model_readiness",
    "score_risk",
    "train_and_evaluate",
    "AUC_FLOOR",
]

log = logging.getLogger("egpt_retail.executors.predict")

FACT = "silver.dc_rfq_quotes"
AUC_FLOOR = 0.65
DEFAULT_LEVELS = {"high": 0.7, "medium": 0.4}
ROW_CAP = 20000

#: How each feature reads in a driver, with the value and what is typical.
_DRIVER_WORDS: Mapping[str, str] = {
    "age_days": "open {value:.0f} days against a usual {usual:.0f}",
    "age_ratio": "open {value:.1f}x the usual time to close ({age:.0f} of {usual:.0f} days)",
    "deal_value_usd": "quote value ${value:,.0f} (typical ${typical:,.0f})",
    "quantity": "quantity {value:.0f} (typical {typical:.0f})",
    "prior_quotes": "{value:.0f} earlier quotes from this account (typical {typical:.1f})",
    "prior_win_rate": "the account's win rate so far {value:.0%} (typical {typical:.0%})",
    "days_since_prev_quote": "{value:.0f} days since the account's previous quote (typical {typical:.0f})",
    "prior_won_value": "${value:,.0f} won from this account before (typical ${typical:,.0f})",
    "live_chat_buy": "came through a live-chat buy inquiry",
}


def _options(step_input: StepInput) -> Mapping[str, Any]:
    return step_input.additional_data or {}


def _as_of(options: Mapping[str, Any]) -> date:
    raw = options.get("as_of")
    return date.fromisoformat(str(raw)) if raw else date.today()


def _engine(settings: RetailSettings) -> Engine:
    if settings.retail_warehouse_readonly_db_url:
        return build_warehouse_readonly_engine(settings)
    return build_warehouse_engine(settings)


def _rows(
    engine: Engine, statement: str, params: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    with engine.begin() as connection:
        return [
            dict(r)
            for r in connection.execute(text(statement), dict(params)).mappings()
        ]


def _label(row: Mapping[str, Any]) -> int:
    usual = float(row.get("usual_days") or 0)
    return int(
        row.get("pipeline_stage") == "Closed Won"
        and float(row.get("days_to_close") or 0) <= usual
    )


def _clean(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        for key in NUMERIC + ["usual_days", "days_to_close"]:
            if r.get(key) is not None:
                r[key] = float(r[key])
        out.append(r)
    return out


def train_and_evaluate(
    training: Sequence[Mapping[str, Any]], *, holdout_share: float = 0.25
) -> Tuple[Fitted, Dict[str, Any]]:
    """Fit on the older three quarters (by quote date), evaluate on the newest quarter,
    then refit on everything for scoring. The evaluation is what readiness shows."""
    rows = sorted(
        _clean(training), key=lambda r: (str(r.get("quote_date")), r.get("quote_id"))
    )
    cut = max(1, int(len(rows) * (1 - holdout_share)))
    train, hold = rows[:cut], rows[cut:]
    fitted = fit_logistic(
        train, [_label(r) for r in train], numeric=NUMERIC, categorical=CATEGORICAL
    )
    hold_labels = [_label(r) for r in hold]
    scores: List[float] = []
    if hold:
        hold_probs, _ = predict_proba(fitted, hold)
        scores = [float(v) for v in hold_probs]
    hold_auc = auc(hold_labels, scores) if hold else None
    evaluation: Dict[str, Any] = {
        "training_rows": len(train),
        "holdout_rows": len(hold),
        "closed_quotes": len({r.get("quote_id") for r in rows}),
        "base_rate": round(float(np.mean([_label(r) for r in rows])), 3)
        if rows
        else None,
        "auc": round(hold_auc, 3) if hold_auc is not None else None,
        "brier": round(brier(hold_labels, scores), 3) if hold else None,
        "calibration": calibration(hold_labels, scores) if hold else [],
        "holdout_from": str(hold[0].get("quote_date")) if hold else None,
    }
    final = fit_logistic(
        rows, [_label(r) for r in rows], numeric=NUMERIC, categorical=CATEGORICAL
    )
    top = sorted(zip(final.columns, final.coef), key=lambda t: -abs(float(t[1])))[:12]
    evaluation["top_coefficients"] = [
        {"feature": n, "coefficient": round(float(v), 3)} for n, v in top
    ]
    evaluation["version"] = hashlib.sha1(
        f"{len(rows)}|{rows[0].get('quote_date') if rows else ''}|{rows[-1].get('quote_date') if rows else ''}".encode()
    ).hexdigest()[:10]
    return final, evaluation


def _drivers(
    fitted: Fitted,
    row: Mapping[str, Any],
    contributions: np.ndarray,
    typical: Mapping[str, float],
    *,
    top: int = 3,
) -> List[Dict[str, Any]]:
    """The columns that pushed this row towards not converting (negative logit of
    converting), largest first, each in words with the row's value and the typical."""
    order = np.argsort(contributions)  # most negative = most risk
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for j in order:
        if contributions[j] >= 0 or len(out) >= top:
            continue
        column = fitted.columns[j]
        feature, _, level = column.partition("=")
        # Age in days and age as a ratio of the usual are one fact; say it once.
        family = "age" if feature in ("age_days", "age_ratio") else feature
        if family in seen:
            continue
        seen.add(family)
        if feature in NUMERIC:
            value = float(row.get(feature) or 0)
            words = _DRIVER_WORDS.get(
                feature, "{feature} {value:.1f} (typical {typical:.1f})"
            )
            text_ = words.format(
                feature=feature.replace("_", " "),
                value=value,
                typical=typical.get(feature, 0.0),
                usual=float(row.get("usual_days") or 0),
                age=float(row.get("age_days") or 0),
            )
            out.append(
                {
                    "feature": feature,
                    "value": round(value, 2),
                    "typical": round(typical.get(feature, 0.0), 2),
                    "contribution": round(float(contributions[j]), 3),
                    "text": text_,
                }
            )
        else:
            shown = level if level and level != "other" else str(row.get(feature) or "")
            if feature == "last_lost_reason" and not shown:
                continue
            words = {
                "last_lost_reason": f"the account's last quote was lost: {shown}",
                "pim_category": f"{shown} quotes convert less often",
                "sales_rep_territory": f"{shown} converts less often",
                "customer_segment": f"{shown} accounts convert less often",
                "quote_source": f"quotes from {shown} convert less often",
                "audience_channel": f"the {shown} channel converts less often",
            }.get(feature, f"{feature.replace('_', ' ')} = {shown}")
            out.append(
                {
                    "feature": feature,
                    "value": shown,
                    "typical": None,
                    "contribution": round(float(contributions[j]), 3),
                    "text": words,
                }
            )
    return out


def _levels(step_input: StepInput) -> Dict[str, float]:
    raw = slot(step_input, "risk levels")
    levels = dict(DEFAULT_LEVELS)
    if isinstance(raw, Mapping):
        for key in ("high", "medium"):
            try:
                if raw.get(key) is not None:
                    levels[key] = float(raw[key])
            except (TypeError, ValueError):
                pass
    return levels


def _level(risk: float, levels: Mapping[str, float]) -> str:
    return (
        "high"
        if risk >= levels["high"]
        else "medium"
        if risk >= levels["medium"]
        else "low"
    )


async def score_risk(step_input: StepInput) -> StepOutput:
    """Every open quote scored for the risk of not converting within its category's usual
    time to close, with the account's RFM context and the drivers; the model trained on
    the fact's own closed history at each run and evaluated on a time holdout."""
    options = _options(step_input)
    as_of = _as_of(options)
    fact = str(options.get("fact") or FACT)
    churn_days = int(slot(step_input, "churn window") or 60)
    levels = _levels(step_input)
    # Zero (the default) carries every quote still within its usual range -- the ones a
    # rep can still save, which is what the report's "act now" counts -- and none of
    # the stalled backlog; a number carries that many from the ranked list instead.
    top_n = int(slot(step_input, "top n") or 0)
    settings = RetailSettings()
    engine = _engine(settings)

    def work() -> Dict[str, Any]:
        with open_semantic_reader(settings) as reader:
            guard = build_warehouse_read_guard(reader, row_cap=ROW_CAP)
            train_stmt = training_sql(fact)
            score_stmt = scoring_sql(fact)
            guard.check(train_stmt.replace(":as_of", "'2000-01-01'"))
            guard.check(score_stmt.replace(":as_of", "'2000-01-01'"))
            params = {"as_of": as_of.isoformat()}
            training = _rows(engine, train_stmt, params)
            note_sql_execution(
                step_input, statement="training frame", row_count=len(training)
            )
            scoring = _clean(_rows(engine, score_stmt, params))
            note_sql_execution(
                step_input, statement="scoring frame", row_count=len(scoring)
            )
            note_dataset_read(
                step_input, table=fact.split(".")[-1], as_of=as_of.isoformat()
            )
        if len(training) < 50:
            return {
                "refused": f"only {len(training)} closed-quote snapshots to learn from; the model needs at least 50"
            }
        fitted, evaluation = train_and_evaluate(training)
        if not scoring:
            return {"refused": "no open quotes to score at this date"}
        probs, contributions = predict_proba(fitted, scoring)
        typical = {
            f: float(np.mean([r.get(f) or 0.0 for r in scoring])) for f in NUMERIC
        }

        # Account context: RFM over the trailing year, and the churn flag.
        accounts: Dict[Any, Dict[str, Any]] = {}
        acct_stmt = (
            f"SELECT customer_id, MAX(customer_name) AS customer_name, "
            f"CAST(:as_of AS date) - MAX(quote_date) AS recency_days, COUNT(*) AS frequency, "
            f"COALESCE(SUM(deal_value_usd) FILTER (WHERE pipeline_stage = 'Closed Won'), 0) AS monetary, "
            f"COUNT(*) FILTER (WHERE pipeline_stage = 'Closed Won') AS won, "
            f"COUNT(*) FILTER (WHERE pipeline_stage = 'Closed Lost') AS lost "
            f"FROM {fact} WHERE quote_date > CAST(:as_of AS date) - 365 AND quote_date <= CAST(:as_of AS date) "
            f"GROUP BY customer_id"
        )
        with open_semantic_reader(settings) as reader:
            guard = build_warehouse_read_guard(reader, row_cap=ROW_CAP)
            guard.check(acct_stmt.replace(":as_of", "'2000-01-01'"))
        raw_accounts = _rows(engine, acct_stmt, {"as_of": as_of.isoformat()})
        segmented = rfm_segments(
            [
                {
                    "account_id": r["customer_id"],
                    "account_name": r["customer_name"],
                    "recency_days": float(r["recency_days"]),
                    "frequency": float(r["frequency"]),
                    "monetary": float(r["monetary"]),
                    "won": int(r["won"]),
                    "lost": int(r["lost"]),
                }
                for r in raw_accounts
            ]
        )
        # The churn flag is the registry's: an account that quoted in the window
        # before the churn line (the 180-day frequency window the RFM KPIs use) and
        # not since -- the same accounts ``rfq__at_risk_accounts`` counts.
        active_window = churn_days + 180
        for a in segmented:
            a["at_risk"] = bool(churn_days <= a["recency_days"] < active_window)
            accounts[a["account_id"]] = a

        subjects: List[Dict[str, Any]] = []
        for i, row in enumerate(scoring):
            risk = float(1.0 - probs[i])
            account = accounts.get(row.get("customer_id"), {})
            usual = float(row.get("usual_days") or 0)
            age = float(row.get("age_days") or 0)
            subjects.append(
                {
                    "quote_id": row.get("quote_id"),
                    "account_id": row.get("customer_id"),
                    "account_name": row.get("customer_name"),
                    "category": row.get("pim_category"),
                    "brand": row.get("brand"),
                    "territory": row.get("sales_rep_territory"),
                    "rep": row.get("sales_rep_name"),
                    "segment": row.get("customer_segment"),
                    "source": row.get("quote_source"),
                    "stage": row.get("pipeline_stage"),
                    "quote_date": str(row.get("quote_date")),
                    "value": round(float(row.get("deal_value_usd") or 0), 2),
                    "age_days": int(age),
                    "usual_days": int(usual),
                    "stalled": bool(usual and age > usual),
                    "p_convert": round(float(probs[i]), 3),
                    "risk": round(risk, 3),
                    "level": _level(risk, levels),
                    "value_at_risk": round(
                        risk * float(row.get("deal_value_usd") or 0), 2
                    ),
                    "drivers": _drivers(fitted, row, contributions[i], typical),
                    "last_lost_reason": row.get("last_lost_reason"),
                    # Why we lost before, specifically: the reason, the quote and when.
                    "lost_before": {
                        "reason": row.get("last_lost_reason"),
                        "quote_id": row.get("last_lost_quote_id"),
                        "date": str(row.get("last_lost_date"))
                        if row.get("last_lost_date")
                        else None,
                        "category": row.get("last_lost_category"),
                        "lost": int(row.get("prior_lost") or 0),
                        "won": int(row.get("prior_won") or 0),
                        "quotes": int(row.get("prior_quotes") or 0),
                    }
                    if row.get("last_lost_reason")
                    else None,
                    "account": {
                        "recency_days": int(account.get("recency_days", 0)),
                        "frequency": int(account.get("frequency", 0)),
                        "monetary": round(float(account.get("monetary", 0.0)), 2),
                        "segment": account.get("segment"),
                        "r": int(account.get("r", 0) or 0),
                        "f": int(account.get("f", 0) or 0),
                        "m": int(account.get("m", 0) or 0),
                        "importance": importance_of(account.get("segment")),
                        "at_risk": bool(account.get("at_risk", False)),
                        "won": int(account.get("won", 0)),
                        "lost": int(account.get("lost", 0)),
                    },
                }
            )
        # Still within the usual range first: those a rep can still save.
        # What to save first: still-in-range quotes, by the value at risk weighted by
        # what the account is worth -- a Cannot-lose-them account's $6K outranks a
        # one-off buyer's $9K.
        for s in subjects:
            s["priority"] = round(s["value_at_risk"] * s["account"]["importance"], 2)
        subjects.sort(key=lambda s: (s["stalled"], -s["priority"], -s["risk"]))
        counts = {
            lvl: sum(1 for s in subjects if s["level"] == lvl)
            for lvl in ("high", "medium", "low")
        }
        in_range = [s for s in subjects if not s["stalled"]]
        summary = {
            "open_quotes": len(subjects),
            **counts,
            "stalled": sum(1 for s in subjects if s["stalled"]),
            "within_range": len(in_range),
            "at_risk_within_range": sum(1 for s in in_range if s["level"] != "low"),
            "high_within_range": sum(1 for s in in_range if s["level"] == "high"),
            "medium_within_range": sum(1 for s in in_range if s["level"] == "medium"),
            "value_at_risk_within_range": round(
                sum(s["value"] for s in in_range if s["level"] != "low"), 2
            ),
            "value_at_risk_high": round(
                sum(s["value"] for s in subjects if s["level"] == "high"), 2
            ),
            "value_open": round(sum(s["value"] for s in subjects), 2),
            "accounts_scored": len(accounts),
            "at_risk_accounts": sum(1 for a in accounts.values() if a["at_risk"]),
            "rfm_segments": {
                seg: sum(1 for a in accounts.values() if a["segment"] == seg)
                for seg in sorted({a["segment"] for a in accounts.values()})
            },
        }
        return {
            "as_of": as_of.isoformat(),
            "fact": fact,
            "horizon": "the category's usual time to close (90th percentile)",
            "churn_days": churn_days,
            "levels": levels,
            "model": evaluation,
            "summary": summary,
            "subjects": subjects[:top_n]
            if top_n
            else [s for s in subjects if not s["stalled"]],
            "subjects_total": len(subjects),
            "focus": focus_of([s for s in subjects if not s["stalled"]]),
        }

    try:
        risk = await asyncio.to_thread(work)
    except SQLGuardRefusal as exc:
        return StepOutput(
            content={"refused": f"guard refused: {exc.message}"},
            success=False,
            stop=True,
        )
    finally:
        engine.dispose()
    if "refused" in risk:
        return StepOutput(content=risk, success=False, stop=True)
    previous = step_input.previous_step_content
    carried = dict(previous) if isinstance(previous, Mapping) else {}
    # The comparison the report's tiles read rides along under its own key; a bare
    # comparison (the previous step's whole content) is tucked under it.
    if (
        "comparison_result" not in carried
        and "kpis" in carried
        and "comparison" in carried
    ):
        carried = {
            "comparison_result": dict(carried),
            "as_of": carried.get("as_of"),
            **({"outcomes": carried["outcomes"]} if "outcomes" in carried else {}),
        }
    return StepOutput(
        content={
            **{
                k: v
                for k, v in carried.items()
                if k in ("kpis", "as_of", "missing", "comparison_result", "outcomes")
            },
            "risk": risk,
        },
        success=True,
    )


#: The week's ten (K26): how many the focus list carries, and the days a rep gets.
FOCUS_SIZE = 10
FOCUS_DAYS = 7


def focus_of(
    in_range: Sequence[Mapping[str, Any]], *, size: int = FOCUS_SIZE
) -> Dict[str, Any]:
    """The ten quotes to attack this week, from the at-risk ones still inside their
    window: a blend of what each is worth, how winnable it still is, how soon its window
    closes, what the account is worth, and whether the lever is known. Each carries the
    factors that put it there in words, and the list carries what the ten are worth --
    quoted, at risk of being lost, and the days to act."""
    pool = [s for s in in_range if s.get("level") in ("high", "medium")]
    if not pool:
        return {
            "quotes": [],
            "days": FOCUS_DAYS,
            "value": 0.0,
            "at_risk": 0.0,
            "closest_days": None,
        }
    values = sorted(float(s.get("value") or 0) for s in pool)
    top_value = values[int(len(values) * 0.75)] if len(values) > 3 else values[-1]
    max_value = values[-1] or 1.0
    scored = []
    for s in pool:
        value = float(s.get("value") or 0)
        p_convert = float(s.get("p_convert") or 0)
        usual = float(s.get("usual_days") or 28) or 28
        days_left = max(0, int(round(usual - float(s.get("age_days") or 0))))
        importance = float((s.get("account") or {}).get("importance") or 1.0)
        known_lever = bool(s.get("last_lost_reason"))
        urgency = 1.0 - min(days_left, usual) / usual
        score = (
            0.35 * (value / max_value)
            + 0.25 * p_convert
            + 0.15 * urgency
            + 0.15 * (importance - 0.8) / 0.7
            + 0.10 * (1.0 if known_lever else 0.0)
        )
        factors = []
        if value >= top_value:
            factors.append("high value")
        if s.get("level") == "medium" and days_left >= 3:
            factors.append("low-hanging")
        if days_left <= 5:
            factors.append("closing soon")
        if importance >= 1.4:
            factors.append("key account")
        if known_lever:
            factors.append("known lever")
        scored.append((score, days_left, factors, s))
    scored.sort(key=lambda t: (-t[0], t[1]))
    chosen = scored[:size]
    quotes: List[Dict[str, Any]] = [
        {
            "quote_id": s.get("quote_id"),
            "score": round(score, 3),
            "days_left": days_left,
            "factors": factors,
            "value": float(s.get("value") or 0),
            "risk": float(s.get("risk") or 0),
            "value_at_risk": float(s.get("value_at_risk") or 0),
            "level": s.get("level"),
        }
        for score, days_left, factors, s in chosen
    ]
    return {
        "quotes": quotes,
        "days": FOCUS_DAYS,
        "value": round(sum(float(q["value"]) for q in quotes), 2),
        "at_risk": round(sum(float(q["value_at_risk"]) for q in quotes), 2),
        "closest_days": min(int(q["days_left"]) for q in quotes),
        "factor_counts": {
            f: sum(1 for q in quotes if f in list(q["factors"]))
            for f in (
                "high value",
                "low-hanging",
                "closing soon",
                "key account",
                "known lever",
            )
        },
    }


def risk_of(step_input: StepInput) -> Optional[Dict[str, Any]]:
    """The risk result carried from ``score_risk``, wherever it sits in the chain."""
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping):
        if isinstance(previous.get("risk"), Mapping):
            return dict(previous["risk"])
        for key in ("explanation", "comparison_result"):
            nested = previous.get(key)
            if isinstance(nested, Mapping) and isinstance(nested.get("risk"), Mapping):
                return dict(nested["risk"])
    for output in earlier_outputs(step_input):
        content = getattr(output, "content", None)
        if isinstance(content, Mapping) and isinstance(content.get("risk"), Mapping):
            return dict(content["risk"])
    return None


def risk_model_readiness(
    *, fact: str = FACT, as_of: Optional[date] = None
) -> Dict[str, Any]:
    """What readiness shows for the at-risk kind: the model trained on the fact's closed
    history at the date and evaluated on the time holdout, against the AUC floor; and
    how many open quotes there are to score. Raises when the warehouse does not answer."""
    settings = RetailSettings()
    engine = _engine(settings)
    day = as_of or date.today()
    try:
        with open_semantic_reader(settings) as reader:
            guard = build_warehouse_read_guard(reader, row_cap=ROW_CAP)
            train_stmt = training_sql(fact)
            guard.check(train_stmt.replace(":as_of", "'2000-01-01'"))
            count_stmt = (
                f"SELECT COUNT(*) AS open_quotes FROM {fact} "
                f"WHERE pipeline_stage NOT IN ('Closed Won', 'Closed Lost') "
                f"AND quote_date <= CAST(:as_of AS date)"
            )
            guard.check(count_stmt.replace(":as_of", "'2000-01-01'"))
        params = {"as_of": day.isoformat()}
        training = _rows(engine, train_stmt, params)
        open_quotes = int(_rows(engine, count_stmt, params)[0]["open_quotes"])
    finally:
        engine.dispose()
    if len(training) < 50:
        return {
            "ready": False,
            "reason": f"only {len(training)} closed-quote snapshots to learn from; "
            "the model needs at least 50",
            "open_quotes": open_quotes,
        }
    _, evaluation = train_and_evaluate(training)
    score = evaluation.get("auc")
    ready = score is not None and score >= AUC_FLOOR
    return {
        "ready": ready,
        "reason": None
        if ready
        else (
            f"holdout AUC {score} is under the {AUC_FLOOR} floor; the history does "
            "not separate quotes that convert from those that do not"
            if score is not None
            else "the holdout has one class only; the model cannot be evaluated"
        ),
        "open_quotes": open_quotes,
        "auc_floor": AUC_FLOOR,
        **evaluation,
    }
