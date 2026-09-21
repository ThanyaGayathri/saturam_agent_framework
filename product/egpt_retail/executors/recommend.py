"""The at-risk kind's prescriptive stage (K26): ``recommend``.

Turns the scored subjects ``score_risk`` carried into Recommendation records -- one per
at-risk quote, with the sales play the catalogue chose and why -- and, where the use case
asks for alerts, an alert per high-risk quote for its rep. Written to
``retail_recommendations`` on the control plane (held under dry run), where Decision
Intelligence and the screen read them; the records ride on the step content for the
report and the dashboard.

Slots: ``outcome`` (``recommend`` | ``alert`` | ``both``, default both), ``top n`` (how many
recommendations a run writes; zero, the default, is every carried subject -- the quotes
still within their usual range; alerts are per high-risk quote regardless).
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.db import build_control_plane_engine
from egpt_retail.app.settings import RetailSettings
from egpt_retail.executors.batch import earlier_outputs, is_dry_run, slot
from egpt_retail.executors.predict import risk_of
from egpt_retail.recommendations import (
    RecommendationStore,
    apply_recommendations_ddl,
    new_recommendation_id,
    play_for,
)

__all__ = [
    "recommend",
    "recommendations_of",
    "records_for",
    "send_alerts",
    "track_outcomes",
    "outcomes_of",
    "outcome_of",
]

log = logging.getLogger("egpt_retail.executors.recommend")

OUTCOMES = ("recommend", "alert", "both")


def _outcome(step_input: StepInput) -> str:
    raw = str(slot(step_input, "outcome") or "both").strip().lower()
    return raw if raw in OUTCOMES else "both"


def _territory_medians(subjects: List[Mapping[str, Any]]) -> Dict[str, float]:
    by: Dict[str, List[float]] = {}
    for s in subjects:
        by.setdefault(str(s.get("territory") or ""), []).append(
            float(s.get("value") or 0)
        )
    return {k: statistics.median(v) for k, v in by.items() if v}


def _expires(as_of: date, subject: Mapping[str, Any]) -> datetime:
    """The horizon: the rest of the usual range for a quote still inside it, one more usual
    range for a stalled one -- when the outcome is looked up."""
    usual = int(subject.get("usual_days") or 28)
    age = int(subject.get("age_days") or 0)
    left = usual - age if age < usual else usual
    return datetime.combine(
        as_of + timedelta(days=max(left, 7)), datetime.min.time(), tzinfo=timezone.utc
    )


def records_for(
    risk: Mapping[str, Any], *, outcome: str, top_n: int, as_of: date
) -> List[Dict[str, Any]]:
    """The Recommendation records for one scoring: recommendations for the top-n at-risk
    subjects (medium or high, in the carried order: still-in-range first, then by value
    at risk), alerts for every high-risk subject when the outcome asks for them."""
    subjects = [
        s for s in risk.get("subjects") or [] if s.get("level") in ("high", "medium")
    ]
    medians = _territory_medians(subjects)
    version = str((risk.get("model") or {}).get("version") or "")
    records: List[Dict[str, Any]] = []

    def base(subject: Mapping[str, Any], kind: str) -> Dict[str, Any]:
        account = subject.get("account") or {}
        return {
            "id": new_recommendation_id(),
            "kind": kind,
            "subject_type": "quote",
            "subject_id": str(subject.get("quote_id")),
            "subject_label": f"Quote {subject.get('quote_id')} · {subject.get('category')} · ${float(subject.get('value') or 0):,.0f}",
            "account_id": None
            if subject.get("account_id") is None
            else str(subject.get("account_id")),
            "account_label": str(subject.get("account_name") or ""),
            "owner": str(subject.get("rep") or ""),
            "risk_score": float(subject.get("risk") or 0),
            "risk_level": str(subject.get("level") or "low"),
            "horizon_days": int(subject.get("usual_days") or 0),
            "model_version": version,
            "drivers": list(subject.get("drivers") or []),
            "value_at_stake": float(subject.get("value") or 0),
            "value_unit": "USD",
            "context": {
                "category": subject.get("category"),
                "brand": subject.get("brand"),
                "territory": subject.get("territory"),
                "segment": subject.get("segment"),
                "source": subject.get("source"),
                "stage": subject.get("stage"),
                "quote_date": subject.get("quote_date"),
                "age_days": subject.get("age_days"),
                "usual_days": subject.get("usual_days"),
                "stalled": subject.get("stalled"),
                "p_convert": subject.get("p_convert"),
                "priority": subject.get("priority"),
                "last_lost_reason": subject.get("last_lost_reason"),
                "account": dict(account),
                "as_of": as_of.isoformat(),
            },
            "expires_at": _expires(as_of, subject),
        }

    if outcome in ("recommend", "both"):
        for subject in subjects[:top_n] if top_n else subjects:
            record = base(subject, "recommendation")
            record["action"] = play_for(
                subject,
                territory_median_value=medians.get(str(subject.get("territory") or "")),
            )
            records.append(record)
    if outcome in ("alert", "both"):
        for subject in subjects:
            if subject.get("level") == "high":
                record = base(subject, "alert")
                record["action"] = None
                records.append(record)
    return records


async def recommend(step_input: StepInput) -> StepOutput:
    """Recommendation records from the carried risk scoring, written to the store (held
    under dry run) and carried on the content as ``recommendations``."""
    risk = risk_of(step_input)
    if not risk:
        return StepOutput(
            content={
                "refused": "no risk scoring to recommend from; score_risk runs first"
            },
            success=False,
            stop=True,
        )
    options: Mapping[str, Any] = step_input.additional_data or {}
    as_of = date.fromisoformat(
        str(risk.get("as_of") or options.get("as_of") or date.today())
    )
    outcome = _outcome(step_input)
    top_n = int(slot(step_input, "top n") or 0)
    records = records_for(risk, outcome=outcome, top_n=top_n, as_of=as_of)
    use_case_id = str(options.get("use_case_id") or "")
    run_id = options.get("run_id")
    written: Dict[str, int] = {"created": 0, "refreshed": 0, "kept": 0}
    held = is_dry_run(step_input) or not use_case_id
    if not held:
        settings = RetailSettings()
        engine = build_control_plane_engine(settings)
        try:

            def write() -> Dict[str, int]:
                apply_recommendations_ddl(engine)
                return RecommendationStore(engine).upsert_open(
                    use_case_id, str(run_id) if run_id else None, records
                )

            written = await asyncio.to_thread(write)
        finally:
            engine.dispose()
    plays: Dict[str, int] = {}
    for r in records:
        if r["kind"] == "recommendation" and r.get("action"):
            plays[r["action"]["code"]] = plays.get(r["action"]["code"], 0) + 1
    result = {
        "outcome": outcome,
        "as_of": as_of.isoformat(),
        "recommendations": [r for r in records if r["kind"] == "recommendation"],
        "alerts": [r for r in records if r["kind"] == "alert"],
        "plays": plays,
        "written": written,
        "held": held,
        "summary": {
            "recommendations": sum(1 for r in records if r["kind"] == "recommendation"),
            "alerts": sum(1 for r in records if r["kind"] == "alert"),
            "value_at_stake": round(
                sum(
                    r["value_at_stake"]
                    for r in records
                    if r["kind"] == "recommendation"
                ),
                2,
            ),
            "owners": len({r["owner"] for r in records if r["kind"] == "alert"}),
        },
    }
    previous = step_input.previous_step_content
    carried = dict(previous) if isinstance(previous, Mapping) else {}
    return StepOutput(
        content={
            **{
                k: v
                for k, v in carried.items()
                if k
                in ("kpis", "as_of", "missing", "risk", "comparison_result", "outcomes")
            },
            "recommendation_result": result,
        },
        success=True,
    )


def recommendations_of(step_input: StepInput) -> Optional[Dict[str, Any]]:
    """The recommend result carried on the chain, wherever it sits."""
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping):
        if isinstance(previous.get("recommendation_result"), Mapping):
            return dict(previous["recommendation_result"])
        for key in ("explanation", "comparison_result"):
            nested = previous.get(key)
            if isinstance(nested, Mapping) and isinstance(
                nested.get("recommendation_result"), Mapping
            ):
                return dict(nested["recommendation_result"])
    for output in earlier_outputs(step_input):
        content = getattr(output, "content", None)
        if isinstance(content, Mapping) and isinstance(
            content.get("recommendation_result"), Mapping
        ):
            return dict(content["recommendation_result"])
    return None


# --- send_alerts ---------------------------------------------------------------------------


def _alert_rows(
    alerts: List[Mapping[str, Any]], plays: Mapping[str, Mapping[str, Any]]
) -> str:
    import html as _html

    e = _html.escape
    font = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
    rows = ""
    for alert in alerts:
        context = alert.get("context") or {}
        account = context.get("account") or {}
        play = plays.get(str(alert.get("subject_id"))) or {}
        because = str(play.get("because") or "")
        drivers = "; ".join(
            str(d.get("text") or "")
            for d in (alert.get("drivers") or [])
            if str(d.get("text") or "") not in because
        )[:160]
        age = f"{context.get('age_days')} of {context.get('usual_days')} days" + (
            " · past usual" if context.get("stalled") else ""
        )
        pct = f"{float(alert.get('risk_score') or 0) * 100:.0f}%"
        rows += (
            "<tr>"
            f'<td style="padding:8px;border-bottom:1px solid #f3f4f6;font:13px {font};color:#111827;vertical-align:top">'
            f'<div style="font-weight:600">{e(str(alert.get("subject_label") or ""))}</div>'
            f'<div style="color:#6b7280;font-size:12px">{e(str(alert.get("account_label") or ""))} · {e(str(context.get("stage") or ""))} · {e(age)}</div>'
            f'<div style="color:#6b7280;font-size:12px">RFM {e(str(account.get("segment") or "—"))}{" · account at risk of churn" if account.get("at_risk") else ""}</div>'
            "</td>"
            f'<td style="padding:8px;border-bottom:1px solid #f3f4f6;text-align:center;vertical-align:top;font:600 13px {font};color:#b91c1c">{e(pct)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #f3f4f6;font:13px {font};color:#111827;vertical-align:top">'
            + (
                f'<div style="font-weight:600">{e(str(play.get("title") or ""))}</div>'
                f'<div style="color:#374151;font-size:12px;margin-top:2px">{e(because)}</div>'
                if play
                else '<div style="color:#6b7280;font-size:12px">See the weekly report for the play.</div>'
            )
            + f'<div style="color:#6b7280;font-size:12px;margin-top:2px">{e(drivers)}</div>'
            "</td></tr>"
        )
    return rows


def _alert_html(
    by_rep: Mapping[str, List[Mapping[str, Any]]],
    plays: Mapping[str, Mapping[str, Any]],
    *,
    as_of: str,
    title: str,
) -> str:
    """One digest: the high-risk quotes grouped by rep, each rep's section headed with
    their count and value at stake, the whole headed with the totals."""
    import html as _html

    e = _html.escape
    font = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
    total_alerts = sum(len(v) for v in by_rep.values())
    total_value = sum(
        float(a.get("value_at_stake") or 0) for v in by_rep.values() for a in v
    )
    sections = ""
    for rep, items in sorted(by_rep.items(), key=lambda kv: -len(kv[1])):
        value = sum(float(a.get("value_at_stake") or 0) for a in items)
        sections += (
            '<div style="border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px;margin:12px 0;background:#fff">'
            f'<div style="font:600 14px {font};color:#111827">{e(rep)}'
            f' <span style="font-weight:500;color:#6b7280">· {len(items)} quote{"s" if len(items) != 1 else ""} at high risk · ${value:,.0f} at stake</span></div>'
            f'<table cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;margin-top:6px"><thead><tr>'
            f'<th style="text-align:left;padding:6px 8px;font:600 11px {font};color:#6b7280;text-transform:uppercase;border-bottom:1px solid #e5e7eb">Quote · account</th>'
            f'<th style="text-align:center;padding:6px 8px;font:600 11px {font};color:#6b7280;text-transform:uppercase;border-bottom:1px solid #e5e7eb">Risk</th>'
            f'<th style="text-align:left;padding:6px 8px;font:600 11px {font};color:#6b7280;text-transform:uppercase;border-bottom:1px solid #e5e7eb">Play · why</th>'
            f"</tr></thead><tbody>{_alert_rows(items, plays)}</tbody></table></div>"
        )
    reps = ", ".join(
        f"{rep} ({len(items)})"
        for rep, items in sorted(by_rep.items(), key=lambda kv: -len(kv[1]))
    )
    return (
        f'<div style="max-width:720px;margin:0 auto;padding:20px;background:#f9fafb;font-family:{font}">'
        f'<p style="font:600 11px {font};color:#b91c1c;text-transform:uppercase;letter-spacing:.06em;margin:0">Alert · {e(title)}</p>'
        f'<h1 style="font:700 20px/1.3 {font};color:#111827;margin:4px 0 2px">{total_alerts} quote{"s" if total_alerts != 1 else ""} at high risk across {len(by_rep)} rep{"s" if len(by_rep) != 1 else ""}, ${total_value:,.0f} at stake</h1>'
        f"<p style=\"font:13px {font};color:#6b7280;margin:0 0 4px\">As of {e(as_of)}. Each is at high risk of not closing within its category's usual time; the play beside it is the catalogue's suggestion. Grouped by the rep who owns the quote.</p>"
        f'<p style="font:12px {font};color:#374151;margin:0 0 8px">{e(reps)}</p>'
        f"{sections}"
        f'<p style="font:11px {font};color:#9ca3af;margin:18px 0 0">Generated by Enterprise GPT. Scores from a model trained on this pipeline\'s own history; plays from the sales play catalogue.</p>'
        "</div>"
    )


def _alert_text(
    by_rep: Mapping[str, List[Mapping[str, Any]]],
    plays: Mapping[str, Mapping[str, Any]],
    *,
    as_of: str,
    title: str,
) -> str:
    lines = [f"{title} — at-risk alerts as of {as_of}", ""]
    for rep, items in sorted(by_rep.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"{rep} — {len(items)} at high risk")
        for alert in items:
            play = plays.get(str(alert.get("subject_id"))) or {}
            lines.append(
                f"- {alert.get('subject_label')} | {alert.get('account_label')} | risk "
                f"{float(alert.get('risk_score') or 0) * 100:.0f}%"
                + (f" | {play.get('title')} — {play.get('because')}" if play else "")
            )
        lines.append("")
    return "\n".join(lines)


async def send_alerts(step_input: StepInput) -> StepOutput:
    """The alerts as one digest -- the high-risk quotes grouped by the rep who owns each,
    with the play -- to the ``alert recipient`` slot (the ``recipient`` slot when unset).
    Composed and held under dry run; nothing to send when the outcome asked for
    recommendations only."""
    result = recommendations_of(step_input)
    previous = step_input.previous_step_content
    carried = dict(previous) if isinstance(previous, Mapping) else {}
    if result is None:
        return StepOutput(
            content={**carried, "alerts": {"status": "no_recommendations", "sent": []}},
            success=True,
        )
    alerts = list(result.get("alerts") or [])
    options: Mapping[str, Any] = step_input.additional_data or {}
    if result.get("outcome") == "recommend" or not alerts:
        return StepOutput(
            content={
                **carried,
                "alerts": {
                    "status": "not_requested"
                    if result.get("outcome") == "recommend"
                    else "none",
                    "sent": [],
                },
            },
            success=True,
        )
    recipient = slot(step_input, "alert recipient") or slot(step_input, "recipient")
    recipients = [
        r.strip()
        for r in str(recipient or "").replace(";", ",").split(",")
        if r.strip() and "@" in r
    ]
    if not recipients:
        return StepOutput(
            content={**carried, "alerts": {"status": "no_recipient", "sent": []}},
            success=True,
        )
    plays = {
        str(r.get("subject_id")): r.get("action") or {}
        for r in result.get("recommendations") or []
        if r.get("action")
    }
    by_rep: Dict[str, List[Mapping[str, Any]]] = {}
    for alert in alerts:
        by_rep.setdefault(str(alert.get("owner") or "Unassigned"), []).append(alert)
    title = str(
        slot(step_input, "title") or options.get("use_case_title") or "At-risk pipeline"
    )
    as_of = str(result.get("as_of") or options.get("as_of") or "")
    # One digest, the reps as sections: the fact carries no rep addresses, so every
    # alert goes to the alert recipient. A rep-address map (a later slot) would split
    # this into one message per rep with the same composer.
    total = sum(len(v) for v in by_rep.values())
    messages: List[Dict[str, Any]] = [
        {
            "reps": sorted(by_rep, key=lambda r: -len(by_rep[r])),
            "to": recipients,
            "subject": (
                f"[Enterprise GPT] {total} quote{'s' if total != 1 else ''} at high risk "
                f"across {len(by_rep)} rep{'s' if len(by_rep) != 1 else ''} — {as_of}"
            ),
            "quotes": [a.get("subject_id") for v in by_rep.values() for a in v],
            "html": _alert_html(by_rep, plays, as_of=as_of, title=title),
            "text": _alert_text(by_rep, plays, as_of=as_of, title=title),
        }
    ]
    if is_dry_run(step_input):
        return StepOutput(
            content={
                **carried,
                "alerts": {
                    "status": "held",
                    "sent": [
                        {k: m[k] for k in ("reps", "to", "subject", "quotes")}
                        for m in messages
                    ],
                    "notes": "dry run: the alerts were composed and not sent",
                },
            },
            success=True,
        )
    from egpt_retail.documents.mailbox import mailbox_from, send_mail

    mailbox = mailbox_from(slot(step_input, "mailbox") or options.get("source mailbox"))
    sent, failed = [], []
    for m in messages:
        try:
            await asyncio.to_thread(
                send_mail,
                mailbox,
                to=list(recipients),
                subject=str(m["subject"]),
                body=str(m["text"]),
                html=str(m["html"]),
            )
            sent.append({k: m[k] for k in ("reps", "to", "subject", "quotes")})
        except Exception as exc:  # the mailbox refused; the run still records it
            failed.append({"reps": m["reps"], "error": f"{type(exc).__name__}: {exc}"})
    return StepOutput(
        content={
            **carried,
            "alerts": {
                "status": "sent"
                if sent and not failed
                else ("partial" if sent else "failed"),
                "sent": sent,
                "failed": failed,
            },
        },
        success=True,
    )


# --- track_outcomes -------------------------------------------------------------------------

#: What became of a quote after its horizon: the words the record carries.
OUTCOMES_OF_QUOTE = ("converted", "lapsed", "still_open")


def outcome_of(stage: Optional[str], horizon_end: Any, as_of: date) -> str:
    """The outcome the fact shows for one quote: closed won is ``converted``; closed
    lost, or still open once the horizon has passed, is ``lapsed`` -- the risk was that
    it would not convert in time, and it did not; open inside the horizon (a run made
    before the end) is ``still_open``."""
    if stage == "Closed Won":
        return "converted"
    if stage == "Closed Lost":
        return "lapsed"
    end = horizon_end.date() if isinstance(horizon_end, datetime) else horizon_end
    return "lapsed" if end is not None and as_of >= end else "still_open"


async def track_outcomes(step_input: StepInput) -> StepOutput:
    """Before the scoring: every recommendation whose horizon has ended and whose outcome
    is not yet written is looked up on the fact -- did the quote convert in time, lapse,
    or is it still open -- and the outcome written on the record. From those the run
    reports its own precision: of the quotes named high risk, how many did not convert.
    The retraining reads the same table. Held under dry run; nothing when the use case
    has no records yet."""
    options: Mapping[str, Any] = step_input.additional_data or {}
    use_case_id = str(options.get("use_case_id") or "")
    as_of = date.fromisoformat(str(options.get("as_of") or date.today()))
    fact = str(options.get("fact") or "silver.dc_rfq_quotes")
    previous = step_input.previous_step_content
    carried = dict(previous) if isinstance(previous, Mapping) else {}
    if not use_case_id or is_dry_run(step_input):
        return StepOutput(
            content={
                **carried,
                "outcomes": {
                    "status": "held" if use_case_id else "no_use_case",
                    "checked": 0,
                    "written": 0,
                },
            },
            success=True,
        )
    settings = RetailSettings()
    engine = build_control_plane_engine(settings)

    def work() -> Dict[str, Any]:
        from sqlalchemy import text

        from egpt_retail.executors.predict import _engine as warehouse_engine

        apply_recommendations_ddl(engine)
        store = RecommendationStore(engine)
        due = store.due(
            use_case_id,
            before=datetime.combine(as_of, datetime.max.time(), tzinfo=timezone.utc),
        )
        quote_ids = sorted(
            {
                str(r["subject_id"])
                for r in due
                if r.get("subject_type", "quote") == "quote"
            }
        )
        stages: Dict[str, Dict[str, Any]] = {}
        if quote_ids:
            wh = warehouse_engine(settings)
            try:
                with wh.connect() as connection:
                    rows = connection.execute(
                        text(
                            f"SELECT CAST(quote_id AS text) AS quote_id, pipeline_stage "
                            f"FROM {fact} WHERE CAST(quote_id AS text) = ANY(:ids)"
                        ),
                        {"ids": quote_ids},
                    ).mappings()
                    stages = {str(r["quote_id"]): dict(r) for r in rows}
            finally:
                wh.dispose()
        written = 0
        tally: Dict[str, Dict[str, int]] = {}
        for record in due:
            row = stages.get(str(record["subject_id"]))
            if row is None:
                continue
            outcome = outcome_of(
                row.get("pipeline_stage"), record.get("expires_at"), as_of
            )
            store.set_outcome(str(record["id"]), outcome, at=datetime.now(timezone.utc))
            written += 1
            level = str(record.get("risk_level") or "low")
            tally.setdefault(level, {"converted": 0, "lapsed": 0, "still_open": 0})
            tally[level][outcome] = tally[level].get(outcome, 0) + 1
        # Precision over everything ever tracked for this use case, not only this run.
        rows_all = store.list(
            use_case_id=use_case_id, kind="recommendation", limit=2000
        ) + store.list(use_case_id=use_case_id, kind="alert", limit=2000)
        seen: set = set()
        precision: Dict[str, Dict[str, int]] = {}
        for r in rows_all:
            key = (r.get("kind"), r.get("subject_id"), r.get("run_id"))
            if key in seen or not r.get("outcome"):
                continue
            seen.add(key)
            level = str(r.get("risk_level") or "low")
            precision.setdefault(level, {"named": 0, "did_not_convert": 0})
            precision[level]["named"] += 1
            if r["outcome"] in ("lapsed",):
                precision[level]["did_not_convert"] += 1
        return {
            "status": "written" if written else "nothing_due",
            "checked": len(due),
            "written": written,
            "this_run": tally,
            "precision": {
                level: {
                    **v,
                    "rate": round(v["did_not_convert"] / v["named"], 3)
                    if v["named"]
                    else None,
                }
                for level, v in precision.items()
            },
        }

    try:
        outcomes = await asyncio.to_thread(work)
    finally:
        engine.dispose()
    return StepOutput(content={**carried, "outcomes": outcomes}, success=True)


def outcomes_of(step_input: StepInput) -> Optional[Dict[str, Any]]:
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping) and isinstance(previous.get("outcomes"), Mapping):
        return dict(previous["outcomes"])
    for output in earlier_outputs(step_input):
        content = getattr(output, "content", None)
        if isinstance(content, Mapping) and isinstance(
            content.get("outcomes"), Mapping
        ):
            return dict(content["outcomes"])
    return None
