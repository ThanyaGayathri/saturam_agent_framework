"""Ask the at-risk use case a question (K26 demo): ten questions the data answers.

A demo surface, not a product one: a question is matched to one of ten handlers by its
words, each handler runs real SQL on the quote fact and reads the use case's latest run,
and the answer comes back as the interface's answer-block payload (insight, findings,
chart, table, recommendation, citation) so the same renderer the playground uses draws it.
Nothing here calls a model; every figure is computed. A question outside the ten is told
what it can ask.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

__all__ = ["ask", "QUESTIONS"]

FACT = "silver.dc_rfq_quotes"

#: The ten, as the chips offer them; the cues decide which one a typed question is.
QUESTIONS: Sequence[Dict[str, Any]] = (
    {
        "id": "at_risk_by_brand",
        "label": "Which brands have the most quotes at risk?",
        "cues": ("brand",),
    },
    {
        "id": "at_risk_by_category",
        "label": "Which product categories are most at risk?",
        "cues": ("categor", "product"),
    },
    {
        "id": "at_risk_by_territory",
        "label": "Which territory has the most at stake?",
        "cues": (
            "territor",
            "region",
            "geography",
            "midwest",
            "northeast",
            "southeast",
        ),
    },
    {
        "id": "at_risk_by_rep",
        "label": "Which reps hold the most value at risk?",
        "cues": ("rep", "sales person", "salesperson", "owner"),
    },
    {
        "id": "lost_reasons",
        "label": "Why are we losing quotes?",
        "cues": ("why", "lost", "lose", "reason"),
    },
    {
        "id": "conversion_by_source",
        "label": "Which quote source converts best?",
        "cues": ("source", "channel", "convert"),
    },
    {
        "id": "stalled_backlog",
        "label": "How big is the stalled backlog, and where?",
        "cues": ("stall", "backlog", "aged", "old"),
    },
    {
        "id": "top_accounts",
        "label": "Which accounts should we call first?",
        "cues": ("account", "customer", "call first", "who first"),
    },
    {
        "id": "usual_time",
        "label": "How long does a quote usually take to close?",
        "cues": ("how long", "usual", "cycle", "days to close", "time to close"),
    },
    {
        "id": "this_week",
        "label": "What is at stake this week, and what should we do?",
        "cues": (
            "this week",
            "what should",
            "what do we do",
            "plays",
            "summary",
            "priorit",
        ),
    },
)


def _match(question: str) -> Optional[str]:
    q = question.lower()
    # Longer, more specific cues first so "at risk by brand" beats "at stake".
    best: Tuple[int, Optional[str]] = (0, None)
    for spec in QUESTIONS:
        score = max((len(c) for c in spec["cues"] if c in q), default=0)
        if score > best[0]:
            best = (score, spec["id"])
    return best[1]


def _rows(
    engine: Engine, statement: str, params: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(r)
            for r in connection.execute(text(statement), dict(params)).mappings()
        ]


def _money(value: Any) -> str:
    n = float(value or 0)
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.2f}M"
    if abs(n) >= 10_000:
        return f"${n / 1000:.0f}K"
    return f"${n:,.0f}"


def _lineage(as_of: str, note: str = "") -> Dict[str, Any]:
    return {
        "sources": [
            {
                "system": "Quote fact (silver.dc_rfq_quotes)",
                "scope": f"as of {as_of}",
                "isReal": True,
                "note": note or "The same rows the at-risk model scores.",
            },
            {
                "system": "At-risk pipeline run",
                "scope": "latest live run",
                "isReal": True,
                "note": "Risk scores, plays and this week's ten from the run.",
            },
        ]
    }


def _subjects(run_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return list(((run_result.get("risk") or {}).get("subjects")) or [])


def _recs(run_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return list(
        ((run_result.get("recommendation_result") or {}).get("recommendations")) or []
    )


def _group(subjects: Sequence[Mapping[str, Any]], key: str) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for s in subjects:
        k = str(s.get(key) or "—")
        g = out.setdefault(
            k, {"label": k, "quotes": 0, "value": 0.0, "at_risk": 0.0, "high": 0}
        )
        g["quotes"] += 1
        g["value"] += float(s.get("value") or 0)
        g["at_risk"] += float(s.get("value_at_risk") or 0)
        g["high"] += 1 if s.get("level") == "high" else 0
    rows = sorted(out.values(), key=lambda r: -r["at_risk"])
    for r in rows:
        r["value"] = round(r["value"], 2)
        r["at_risk"] = round(r["at_risk"], 2)
    return rows


def _breakdown_answer(
    qid: str,
    title: str,
    key: str,
    noun: str,
    subjects: Sequence[Mapping[str, Any]],
    as_of: str,
) -> Dict[str, Any]:
    rows = _group(subjects, key)
    if not rows:
        return _empty(as_of, "No open quotes in their window at risk right now.")
    top = rows[0]
    total = sum(r["at_risk"] for r in rows)
    share = top["at_risk"] / total if total else 0
    return {
        "intro": f"{len(subjects)} open quotes still inside their usual window are at risk; here is where that risk sits by {noun}.",
        "blocks": [
            {
                "id": "insight",
                "type": "insight",
                "headline": f"{top['label']} carries the most at risk: {_money(top['at_risk'])} across {top['quotes']} quotes ({share:.0%} of the total).",
                "sentiment": "negative",
            },
            {
                "id": "findings",
                "type": "findings",
                "items": [
                    {
                        "title": r["label"],
                        "value": _money(r["at_risk"]),
                        "desc": f"{r['quotes']} quotes · {r['high']} high risk · {_money(r['value'])} quoted",
                        "sentiment": "negative" if r["high"] else "neutral",
                    }
                    for r in rows[:4]
                ],
            },
            {
                "id": "chart",
                "type": "chart",
                "title": f"Value at risk by {noun}",
                "chartKind": "bar",
                "data": [
                    {"label": r["label"], "value": round(r["at_risk"])}
                    for r in rows[:10]
                ],
                "config": {"xKey": "label", "yKey": "value"},
            },
            {
                "id": "table",
                "type": "table",
                "title": f"By {noun}",
                "columns": [
                    {"key": "label", "label": noun.title()},
                    {"key": "quotes", "label": "At-risk quotes", "format": "number"},
                    {"key": "high", "label": "High", "format": "number"},
                    {"key": "value", "label": "Quoted", "format": "currency"},
                    {"key": "at_risk", "label": "At risk", "format": "currency"},
                ],
                "rows": rows,
            },
        ],
        "followUps": _follow_ups(qid),
        "lineage": _lineage(as_of),
    }


def _follow_ups(exclude: str) -> List[str]:
    return [q["label"] for q in QUESTIONS if q["id"] != exclude][:3]


def _empty(as_of: str, why: str) -> Dict[str, Any]:
    return {
        "intro": why,
        "blocks": [
            {
                "id": "insight",
                "type": "insight",
                "headline": why,
                "sentiment": "neutral",
            }
        ],
        "followUps": _follow_ups(""),
        "lineage": _lineage(as_of),
    }


# --- the ten ------------------------------------------------------------------------------


def q_at_risk_by_brand(
    engine: Engine, run: Mapping[str, Any], as_of: str
) -> Dict[str, Any]:
    return _breakdown_answer(
        "at_risk_by_brand", "brand", "brand", "brand", _subjects(run), as_of
    )


def q_at_risk_by_category(
    engine: Engine, run: Mapping[str, Any], as_of: str
) -> Dict[str, Any]:
    return _breakdown_answer(
        "at_risk_by_category", "category", "category", "category", _subjects(run), as_of
    )


def q_at_risk_by_territory(
    engine: Engine, run: Mapping[str, Any], as_of: str
) -> Dict[str, Any]:
    return _breakdown_answer(
        "at_risk_by_territory",
        "territory",
        "territory",
        "territory",
        _subjects(run),
        as_of,
    )


def q_at_risk_by_rep(
    engine: Engine, run: Mapping[str, Any], as_of: str
) -> Dict[str, Any]:
    return _breakdown_answer(
        "at_risk_by_rep", "rep", "rep", "rep", _subjects(run), as_of
    )


def q_lost_reasons(
    engine: Engine, run: Mapping[str, Any], as_of: str
) -> Dict[str, Any]:
    rows = _rows(
        engine,
        f"""
        SELECT lost_reason AS label, COUNT(*) AS quotes, ROUND(SUM(deal_value_usd)) AS value,
               ROUND(AVG(days_to_close)) AS days
        FROM {FACT} WHERE pipeline_stage = 'Closed Lost' AND lost_reason IS NOT NULL
          AND closed_date <= CAST(:as_of AS date) AND closed_date > CAST(:as_of AS date) - 180
        GROUP BY 1 ORDER BY 2 DESC""",
        {"as_of": as_of},
    )
    total = sum(int(r["quotes"]) for r in rows) or 1
    by_cat = _rows(
        engine,
        f"""
        SELECT pim_category AS category, lost_reason AS reason, COUNT(*) AS n
        FROM {FACT} WHERE pipeline_stage = 'Closed Lost' AND lost_reason IS NOT NULL
          AND closed_date <= CAST(:as_of AS date) AND closed_date > CAST(:as_of AS date) - 180
        GROUP BY 1,2""",
        {"as_of": as_of},
    )
    top_by_cat: Dict[str, Tuple[str, int]] = {}
    for r in by_cat:
        c = str(r["category"])
        if c not in top_by_cat or int(r["n"]) > top_by_cat[c][1]:
            top_by_cat[c] = (str(r["reason"]), int(r["n"]))
    plays = {
        "Chosen Competitor": "Position against the named competitor",
        "Budget Constraints": "Targeted commercial offer within policy",
        "Price Too High": "Targeted commercial offer within policy",
        "No Response": "Send the total-cost-of-ownership summary to the decision maker",
        "Alternative Specification": "Re-quote with the alternative specification",
        "Decision Delayed": "Send the total-cost-of-ownership summary to the decision maker",
    }
    return {
        "intro": "Of the quotes lost in the last 180 days, the reasons split like this; each reason has a play in the catalogue.",
        "blocks": [
            {
                "id": "insight",
                "type": "insight",
                "headline": f"{rows[0]['label']} is the top reason: {int(rows[0]['quotes'])} of {total} lost quotes ({int(rows[0]['quotes']) / total:.0%}), {_money(rows[0]['value'])}."
                if rows
                else "No lost quotes in the window.",
                "sentiment": "negative",
            },
            {
                "id": "chart",
                "type": "chart",
                "title": "Lost quotes by reason, last 180 days",
                "chartKind": "pie",
                "data": [
                    {"label": r["label"], "value": int(r["quotes"])} for r in rows
                ],
                "config": {"xKey": "label", "yKey": "value"},
            },
            {
                "id": "table",
                "type": "table",
                "title": "Where each reason bites hardest",
                "columns": [
                    {"key": "category", "label": "Category"},
                    {"key": "reason", "label": "Top reason"},
                    {"key": "n", "label": "Lost", "format": "number"},
                ],
                "rows": [
                    {"category": c, "reason": r, "n": n}
                    for c, (r, n) in sorted(
                        top_by_cat.items(), key=lambda kv: -kv[1][1]
                    )
                ],
            },
            {
                "id": "rec",
                "type": "recommendation",
                "title": "The play per reason",
                "items": [
                    {
                        "text": f"{r['label']} → {plays.get(str(r['label']), 'Re-engage before the quote lapses')}",
                        "priority": "high" if i < 2 else "medium",
                    }
                    for i, r in enumerate(rows[:6])
                ],
            },
        ],
        "followUps": _follow_ups("lost_reasons"),
        "lineage": _lineage(as_of, "Closed-lost quotes in the trailing 180 days."),
    }


def q_conversion_by_source(
    engine: Engine, run: Mapping[str, Any], as_of: str
) -> Dict[str, Any]:
    rows = _rows(
        engine,
        f"""
        SELECT quote_source AS label, COUNT(*) AS quotes,
               ROUND(100.0 * COUNT(*) FILTER (WHERE pipeline_stage = 'Closed Won') / NULLIF(COUNT(*) FILTER (WHERE pipeline_stage IN ('Closed Won','Closed Lost')), 0), 1) AS won_rate,
               ROUND(AVG(days_to_close) FILTER (WHERE pipeline_stage = 'Closed Won')) AS days
        FROM {FACT} WHERE quote_date <= CAST(:as_of AS date) GROUP BY 1 ORDER BY 3 DESC""",
        {"as_of": as_of},
    )
    open_by = _group([s for s in _subjects(run)], "source")
    at_risk = {r["label"]: r for r in open_by}
    return {
        "intro": "Closed-won rate by the channel a quote came through, and where today's at-risk quotes came from.",
        "blocks": [
            {
                "id": "insight",
                "type": "insight",
                "headline": f"{rows[0]['label']} converts best at {rows[0]['won_rate']}%; {rows[-1]['label']} worst at {rows[-1]['won_rate']}%."
                if len(rows) > 1
                else "One source only.",
                "sentiment": "neutral",
            },
            {
                "id": "chart",
                "type": "chart",
                "title": "Closed-won rate by quote source",
                "chartKind": "bar",
                "data": [
                    {"label": r["label"], "value": float(r["won_rate"] or 0)}
                    for r in rows
                ],
                "config": {"xKey": "label", "yKey": "value"},
            },
            {
                "id": "table",
                "type": "table",
                "title": "By source",
                "columns": [
                    {"key": "label", "label": "Source"},
                    {"key": "quotes", "label": "Quotes", "format": "number"},
                    {"key": "won_rate", "label": "Won rate %", "format": "number"},
                    {"key": "days", "label": "Days to win", "format": "number"},
                    {"key": "at_risk", "label": "At risk now", "format": "currency"},
                ],
                "rows": [
                    {**r, "at_risk": at_risk.get(str(r["label"]), {}).get("at_risk", 0)}
                    for r in rows
                ],
            },
        ],
        "followUps": _follow_ups("conversion_by_source"),
        "lineage": _lineage(as_of),
    }


def q_stalled_backlog(
    engine: Engine, run: Mapping[str, Any], as_of: str
) -> Dict[str, Any]:
    summary = (run.get("risk") or {}).get("summary") or {}
    rows = _rows(
        engine,
        f"""
        WITH usual AS (SELECT pim_category, PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY days_to_close) AS usual_days
                       FROM {FACT} WHERE days_to_close IS NOT NULL AND closed_date <= CAST(:as_of AS date) GROUP BY 1)
        SELECT q.sales_rep_territory AS label, COUNT(*) AS quotes, ROUND(SUM(q.deal_value_usd)) AS value,
               ROUND(AVG(CAST(:as_of AS date) - q.quote_date)) AS avg_age
        FROM {FACT} q JOIN usual u USING (pim_category)
        WHERE q.pipeline_stage NOT IN ('Closed Won','Closed Lost') AND q.quote_date <= CAST(:as_of AS date)
          AND (CAST(:as_of AS date) - q.quote_date) > u.usual_days
        GROUP BY 1 ORDER BY 3 DESC""",
        {"as_of": as_of},
    )
    return {
        "intro": "The stalled backlog: open quotes already past their category's usual time to close. Not the act-now list -- these have missed their window.",
        "blocks": [
            {
                "id": "insight",
                "type": "insight",
                "headline": f"{summary.get('stalled', 0):,} of {summary.get('open_quotes', 0):,} open quotes are stalled, {_money(sum(float(r['value']) for r in rows))} quoted; {rows[0]['label']} holds the most."
                if rows
                else "No stalled quotes.",
                "sentiment": "negative",
            },
            {
                "id": "findings",
                "type": "findings",
                "items": [
                    {
                        "title": "Stalled",
                        "value": f"{summary.get('stalled', 0):,}",
                        "desc": "past the usual window",
                        "sentiment": "negative",
                    },
                    {
                        "title": "In window",
                        "value": f"{summary.get('within_range', 0):,}",
                        "desc": f"{summary.get('at_risk_within_range', 0)} of them at risk",
                        "sentiment": "neutral",
                    },
                    {
                        "title": "Avg age of the backlog",
                        "value": f"{round(sum(float(r['avg_age']) * int(r['quotes']) for r in rows) / max(1, sum(int(r['quotes']) for r in rows)))} days",
                        "desc": "since the quote",
                        "sentiment": "negative",
                    },
                ],
            },
            {
                "id": "chart",
                "type": "chart",
                "title": "Stalled quotes by territory",
                "chartKind": "bar",
                "data": [
                    {"label": r["label"], "value": int(r["quotes"])} for r in rows
                ],
                "config": {"xKey": "label", "yKey": "value"},
            },
            {
                "id": "rec",
                "type": "recommendation",
                "title": "What to do with the backlog",
                "items": [
                    {
                        "text": "Release the small, old quotes to nurture so they stop counting as pipeline",
                        "priority": "medium",
                    },
                    {
                        "text": "Work the in-window list first: that is where a call still changes the outcome",
                        "priority": "high",
                    },
                ],
            },
        ],
        "followUps": _follow_ups("stalled_backlog"),
        "lineage": _lineage(as_of),
    }


def q_top_accounts(
    engine: Engine, run: Mapping[str, Any], as_of: str
) -> Dict[str, Any]:
    recs = _recs(run)
    by_account: Dict[str, Dict[str, Any]] = {}
    for r in recs:
        ctx = r.get("context") or {}
        a = by_account.setdefault(
            str(r.get("account_label") or "—"),
            {
                "account": r.get("account_label"),
                "quotes": 0,
                "at_stake": 0.0,
                "segment": (ctx.get("account") or {}).get("segment"),
                "top_play": (r.get("action") or {}).get("title"),
                "rep": r.get("owner"),
                "high": 0,
            },
        )
        a["quotes"] += 1
        a["at_stake"] += float(r.get("value_at_stake") or 0)
        a["high"] += 1 if r.get("risk_level") == "high" else 0
    rows = sorted(by_account.values(), key=lambda a: -a["at_stake"])[:10]
    for a in rows:
        a["at_stake"] = round(a["at_stake"], 2)
    return {
        "intro": "Accounts ranked by the value of their at-risk quotes, with the play the catalogue chose and the rep who owns them.",
        "blocks": [
            {
                "id": "insight",
                "type": "insight",
                "headline": f"Call {rows[0]['account']} first: {_money(rows[0]['at_stake'])} at stake across {rows[0]['quotes']} quote{'s' if rows[0]['quotes'] != 1 else ''}, RFM {rows[0]['segment']}."
                if rows
                else "No at-risk accounts.",
                "sentiment": "negative",
            },
            {
                "id": "table",
                "type": "table",
                "title": "Accounts to call, in order",
                "columns": [
                    {"key": "account", "label": "Account"},
                    {"key": "segment", "label": "RFM"},
                    {"key": "quotes", "label": "At-risk quotes", "format": "number"},
                    {"key": "at_stake", "label": "At stake", "format": "currency"},
                    {"key": "top_play", "label": "Play"},
                    {"key": "rep", "label": "Rep"},
                ],
                "rows": rows,
            },
            {
                "id": "rec",
                "type": "recommendation",
                "title": "First three calls",
                "items": [
                    {
                        "text": f"{a['account']} — {a['top_play']} ({a['rep']})",
                        "priority": "high",
                    }
                    for a in rows[:3]
                ],
            },
        ],
        "followUps": _follow_ups("top_accounts"),
        "lineage": _lineage(as_of),
    }


def q_usual_time(engine: Engine, run: Mapping[str, Any], as_of: str) -> Dict[str, Any]:
    rows = _rows(
        engine,
        f"""
        SELECT pim_category AS label,
               CAST(ROUND(CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close) AS numeric)) AS int) AS median_days,
               CAST(ROUND(CAST(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY days_to_close) AS numeric)) AS int) AS usual_days,
               COUNT(*) AS closed
        FROM {FACT} WHERE days_to_close IS NOT NULL AND closed_date <= CAST(:as_of AS date)
        GROUP BY 1 ORDER BY 3 DESC""",
        {"as_of": as_of},
    )
    return {
        "intro": "The usual time to close per category: the median, and the 90th percentile the at-risk model uses as each quote's window.",
        "blocks": [
            {
                "id": "insight",
                "type": "insight",
                "headline": f"A quote usually closes in about {rows[0]['median_days'] if rows else '?'} days; past {rows[0]['usual_days'] if rows else '?'} days ({rows[0]['label'] if rows else ''}) it rarely does -- that is the window we score against.",
                "sentiment": "neutral",
            },
            {
                "id": "chart",
                "type": "chart",
                "title": "Usual time to close (90th percentile) by category, days",
                "chartKind": "bar",
                "data": [
                    {"label": r["label"], "value": int(r["usual_days"])} for r in rows
                ],
                "config": {"xKey": "label", "yKey": "value"},
            },
            {
                "id": "table",
                "type": "table",
                "title": "By category",
                "columns": [
                    {"key": "label", "label": "Category"},
                    {"key": "median_days", "label": "Median days", "format": "number"},
                    {"key": "usual_days", "label": "Window (p90)", "format": "number"},
                    {"key": "closed", "label": "Closed quotes", "format": "number"},
                ],
                "rows": rows,
            },
        ],
        "followUps": _follow_ups("usual_time"),
        "lineage": _lineage(as_of, "Closed quotes with a recorded days-to-close."),
    }


def q_this_week(engine: Engine, run: Mapping[str, Any], as_of: str) -> Dict[str, Any]:
    summary = (run.get("risk") or {}).get("summary") or {}
    focus = (run.get("risk") or {}).get("focus") or {}
    plays = (run.get("recommendation_result") or {}).get("plays") or {}
    recs = {str(r.get("subject_id")): r for r in _recs(run)}
    ten = []
    for q in (focus.get("quotes") or [])[:10]:
        r = recs.get(str(q.get("quote_id"))) or {}
        a = r.get("action") or {}
        ten.append(
            {
                "quote": r.get("subject_label") or f"Quote {q.get('quote_id')}",
                "account": r.get("account_label"),
                "risk": f"{round(float(r.get('risk_score') or 0) * 100)}%",
                "at_stake": r.get("value_at_stake"),
                "play": a.get("title"),
                "owner": f"{a.get('owner') or ''}"
                + (
                    f" · {int(a.get('sla_hours') or 0) // 24}d"
                    if a.get("sla_hours")
                    else ""
                ),
            }
        )
    return {
        "intro": "This week in one screen: what is at risk, what the ten to attack are worth, and how the plays split.",
        "blocks": [
            {
                "id": "insight",
                "type": "insight",
                "headline": f"{summary.get('at_risk_within_range', 0)} quotes at risk in their window worth {_money(summary.get('value_at_risk_within_range'))}; attack these ten in the next 7 days: {_money(focus.get('value'))} of quotes, {_money(focus.get('at_risk'))} of it otherwise lost.",
                "sentiment": "negative",
            },
            {
                "id": "findings",
                "type": "findings",
                "items": [
                    {
                        "title": "Act now",
                        "value": str(summary.get("at_risk_within_range", 0)),
                        "desc": f"of {summary.get('within_range', 0)} in window",
                        "sentiment": "negative",
                    },
                    {
                        "title": "Already stalled",
                        "value": f"{summary.get('stalled', 0):,}",
                        "desc": "past the window",
                        "sentiment": "neutral",
                    },
                    {
                        "title": "Accounts at risk",
                        "value": f"{summary.get('at_risk_accounts', 0):,}",
                        "desc": "no quote in 60 days",
                        "sentiment": "negative",
                    },
                ],
            },
            {
                "id": "chart",
                "type": "chart",
                "title": "Plays this week",
                "chartKind": "bar",
                "data": [
                    {"label": k.replace("_", " ").title(), "value": v}
                    for k, v in sorted(plays.items(), key=lambda kv: -kv[1])
                ],
                "config": {"xKey": "label", "yKey": "value"},
            },
            {
                "id": "table",
                "type": "table",
                "title": "This week's ten",
                "columns": [
                    {"key": "quote", "label": "Quote"},
                    {"key": "account", "label": "Account"},
                    {"key": "risk", "label": "Risk"},
                    {"key": "at_stake", "label": "At stake", "format": "currency"},
                    {"key": "play", "label": "Play"},
                    {"key": "owner", "label": "Owner"},
                ],
                "rows": ten,
            },
        ],
        "followUps": _follow_ups("this_week"),
        "lineage": _lineage(as_of),
    }


HANDLERS: Dict[str, Callable[[Engine, Mapping[str, Any], str], Dict[str, Any]]] = {
    "at_risk_by_brand": q_at_risk_by_brand,
    "at_risk_by_category": q_at_risk_by_category,
    "at_risk_by_territory": q_at_risk_by_territory,
    "at_risk_by_rep": q_at_risk_by_rep,
    "lost_reasons": q_lost_reasons,
    "conversion_by_source": q_conversion_by_source,
    "stalled_backlog": q_stalled_backlog,
    "top_accounts": q_top_accounts,
    "usual_time": q_usual_time,
    "this_week": q_this_week,
}


def _trace(qid: str, run_result: Mapping[str, Any], as_of: str) -> List[str]:
    """What the answer did, in order -- shown while it 'computes', and true."""
    summary = (run_result.get("risk") or {}).get("summary") or {}
    n_open = summary.get("open_quotes", 0)
    n_range = summary.get("within_range", 0)
    n_risk = summary.get("at_risk_within_range", 0)
    reads = f"Reading the quote fact (silver.dc_rfq_quotes) as of {as_of}"
    run = (
        f"Loading the latest run: {n_open:,} open quotes scored, {n_range} in their "
        f"window, {n_risk} at risk"
    )
    by_qid = {
        "at_risk_by_brand": [
            reads,
            run,
            "Grouping the at-risk quotes by brand and summing value × risk",
            "Ranking brands by value at risk",
        ],
        "at_risk_by_category": [
            reads,
            run,
            "Grouping the at-risk quotes by product category",
            "Ranking categories by value at risk",
        ],
        "at_risk_by_territory": [
            reads,
            run,
            "Grouping the at-risk quotes by territory",
            "Ranking territories by value at risk",
        ],
        "at_risk_by_rep": [
            reads,
            run,
            "Grouping the at-risk quotes by the rep who owns them",
            "Ranking reps by value at risk",
        ],
        "lost_reasons": [
            reads,
            "Selecting closed-lost quotes in the trailing 180 days",
            "Counting lost quotes and value by reason",
            "Finding the top reason per category",
            "Mapping each reason to its play in the catalogue",
        ],
        "conversion_by_source": [
            reads,
            "Computing closed-won rate and days-to-win per quote source",
            run,
            "Attaching today's value at risk per source",
        ],
        "stalled_backlog": [
            reads,
            "Computing each category's usual time to close (90th percentile)",
            "Selecting open quotes past their window, by territory",
            run,
        ],
        "top_accounts": [
            run,
            "Reading the recommendations and grouping them by account",
            "Summing value at stake and picking each account's play",
            "Ranking accounts by what is at stake",
        ],
        "usual_time": [
            reads,
            "Selecting closed quotes with a recorded days-to-close",
            "Computing the median and the 90th percentile per category",
        ],
        "this_week": [
            run,
            "Reading this week's ten and the plays",
            "Totalling what the ten are worth and what is otherwise lost",
        ],
    }
    return by_qid.get(qid, [reads, run])


def _narrative(answer: Mapping[str, Any]) -> str:
    """The answer in markdown, from its own blocks: the headline, then the key numbers as
    a list -- what the reader sees first, before the charts."""
    lines: List[str] = []
    for b in answer.get("blocks") or []:
        if b.get("type") == "insight" and b.get("headline"):
            lines.append(f"**{b['headline']}**")
            lines.append("")
    for b in answer.get("blocks") or []:
        if b.get("type") == "findings":
            for item in (b.get("items") or [])[:4]:
                desc = f" — {item['desc']}" if item.get("desc") else ""
                lines.append(f"- **{item.get('title')}**: {item.get('value')}{desc}")
            lines.append("")
        if b.get("type") == "recommendation":
            lines.append(f"*{b.get('title') or 'Next'}:*")
            for item in (b.get("items") or [])[:3]:
                text_ = item.get("text") if isinstance(item, Mapping) else str(item)
                lines.append(f"- {text_}")
            lines.append("")
    return "\n".join(lines).strip()


def ask(
    engine: Engine,
    question: str,
    run_result: Mapping[str, Any],
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """The answer-block payload for one question over the use case's data and latest run."""
    day = as_of or str(((run_result.get("risk") or {}).get("as_of")) or date.today())
    qid = _match(question)
    if qid is None:
        return {
            "matched": None,
            "intro": "I can answer these about the at-risk pipeline:",
            "blocks": [
                {
                    "id": "rec",
                    "type": "recommendation",
                    "title": "Try one of these",
                    "items": [{"text": q["label"]} for q in QUESTIONS],
                }
            ],
            "followUps": [q["label"] for q in QUESTIONS[:4]],
            "lineage": _lineage(day),
        }
    answer = HANDLERS[qid](engine, run_result, day)
    answer["matched"] = qid
    answer["trace"] = _trace(qid, run_result, day)
    answer["narrative"] = _narrative(answer)
    answer["variant"] = "answer-block"
    answer["dataGrounded"] = True
    answer["source"] = "at-risk-pipeline"
    return answer
