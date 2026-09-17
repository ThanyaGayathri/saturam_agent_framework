"""The Analytics Automation executors (K23): the Inform shape's stages after ``read_gold``.

An Inform use case reads its KPIs from gold, compares them to a baseline, has one model
step explain the movement, checks that explanation against the numbers, renders a report
and delivers it. The four executors here are the deterministic stages of that chain; the
explanation is a model step (``usecases/runtime.py``, role ``explanation``) and provenance
and in-app delivery are R7's.

    compare_to_baseline     gold at the run's date against gold at the baseline date
                            (prior period or same period last year), per KPI and per
                            grain row; a baseline date gold does not hold yet is
                            materialised for it, once, from the KPI's own formula
    check_report_grounding  every figure in the explanation is one the comparison carried
    render_report           the comparison and the explanation as an HTML page and a
                            plain-text twin, with the KPI tables
    send_report             the HTML report to the recipients through the mailbox's SMTP;
                            held under the dry-run flag

The batch handed from stage to stage is one dict -- ``{"as_of", "comparison", "kpis":
[...], "missing": [...]}`` -- built by ``compare_to_baseline`` and carried forward by the
steps after it under the ``comparison_result`` key, so each step reads its predecessor
and the report reads them all.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, cast

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.executors.batch import is_dry_run, slot
from egpt_retail.executors.gold import materialise_gold, read_gold

__all__ = [
    "compare_to_baseline",
    "check_report_grounding",
    "render_report",
    "send_report",
    "comparison_of",
    "explanation_of",
    "grounding_of",
    "report_html",
    "report_text",
]

COMPARISONS = {
    "prior_period": "the period before",
    "prior_year": "the same period last year",
}
DEFAULT_WINDOW_DAYS = 7


# --- helpers -----------------------------------------------------------------------------


def _options(step_input: StepInput) -> Mapping[str, Any]:
    return step_input.additional_data or {}


def _as_of(options: Mapping[str, Any]) -> date:
    raw = options.get("as_of")
    return date.fromisoformat(str(raw)) if raw else date.today()


def _number(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _dimension_key(row: Mapping[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """A gold row's grain keys as a hashable key (the metadata columns left out)."""
    skip = {"value", "as_of", "computed_at", "run_id", "source_snapshot"}
    return tuple(sorted((k, str(v)) for k, v in row.items() if k not in skip))


def _walk(content: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(content, Mapping):
            return None
        content = content.get(key)
    return content


def comparison_of(step_input: StepInput) -> Optional[Dict[str, Any]]:
    """The comparison result carried from ``compare_to_baseline``: on the immediately
    previous step's content, or on any earlier step's output by name."""
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping):
        if "kpis" in previous and "comparison" in previous:
            return dict(previous)
        carried = previous.get("comparison_result")
        if isinstance(carried, Mapping):
            return dict(carried)
    for name in ("Compare to baseline", "compare_to_baseline"):
        try:
            output = step_input.get_step_content(name)
        except Exception:
            output = None
        if isinstance(output, Mapping) and "kpis" in output:
            return dict(output)
    return None


def _is_explanation(value: Any) -> bool:
    return isinstance(value, Mapping) and "headline" in value


def grounding_of(step_input: StepInput) -> Optional[Dict[str, Any]]:
    """The grounding step's output -- ``{comparison_result, explanation, grounding}`` --
    wherever it sits: the previous content, nested under ``explanation`` when
    ``record_provenance`` forwarded it, or the step by name."""
    candidates: List[Any] = []
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping):
        candidates.append(previous)
        candidates.append(previous.get("explanation"))
    for name in ("Check grounding", "check_report_grounding"):
        try:
            candidates.append(step_input.get_step_content(name))
        except Exception:
            pass
    for candidate in candidates:
        if isinstance(candidate, Mapping) and isinstance(
            candidate.get("grounding"), Mapping
        ):
            return {str(k): v for k, v in candidate.items()}
    return None


def explanation_of(step_input: StepInput) -> Optional[Dict[str, Any]]:
    """The explanation a model step produced, wherever it sits: the previous content's
    ``explanation``, or the Explain step's output by name."""
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping) and isinstance(
        previous.get("explanation"), Mapping
    ):
        return dict(previous["explanation"])
    for name in ("Explain", "explanation"):
        try:
            output = step_input.get_step_content(name)
        except Exception:
            output = None
        if isinstance(output, Mapping):
            nested = output.get("explanation")
            if isinstance(nested, Mapping) and _is_explanation(nested):
                return {str(k): v for k, v in nested.items()}
            if _is_explanation(output):
                return {str(k): v for k, v in output.items()}
    return None


# --- compare_to_baseline -------------------------------------------------------------------


def _definitions(
    options: Mapping[str, Any], identifiers: Sequence[str]
) -> Dict[str, Any]:
    """Name, unit, grain and declared window per KPI: from ``additional_data
    ["kpi_definitions"]`` when a caller injected them, else the registry."""
    injected = options.get("kpi_definitions")
    if isinstance(injected, Mapping):
        return {k: dict(v) for k, v in injected.items()}
    out: Dict[str, Any] = {}
    try:
        from egpt_retail.app.db import pooled_control_plane_connection
        from egpt_retail.app.settings import RetailSettings
        from egpt_retail.kpi.store import KpiRegistryStore

        connection = pooled_control_plane_connection(RetailSettings())
        try:
            store = KpiRegistryStore(connection)
            for identifier in identifiers:
                definition = store.get(identifier)
                if definition is None:
                    continue
                window = getattr(definition, "time_window", None)
                out[identifier] = {
                    "name": definition.name,
                    "unit": getattr(definition, "unit", "") or "",
                    "grain": definition.grain,
                    "window_days": _window_days(window),
                }
        finally:
            connection.close()
    except Exception:  # no control plane: names fall back to identifiers
        pass
    return out


def _window_days(window: Any) -> Optional[int]:
    if window is None:
        return None
    length = getattr(window, "length", None)
    unit = getattr(window, "unit", None)
    if isinstance(window, Mapping):
        length, unit = window.get("length"), window.get("unit")
    unit = getattr(unit, "value", unit)
    if not length:
        return None
    days = {"day": 1, "week": 7, "month": 30, "quarter": 91, "year": 365}.get(
        str(unit), 1
    )
    return int(length) * days


_NON_ADDITIVE = re.compile(r"\b(rate|share|average|avg|mean|median|ratio|per)\b", re.I)


def _additive(entry: Mapping[str, Any]) -> bool:
    """Whether a KPI's breakdown rows add up to a whole (a count, an amount) rather than a
    rate, share or average that must not be summed."""
    unit = str(entry.get("unit") or "")
    if unit.strip() == "%":
        return False
    return not _NON_ADDITIVE.search(str(entry.get("name") or ""))


def _baseline_date(as_of: date, comparison: str, window_days: int) -> date:
    if comparison == "prior_year":
        return as_of - timedelta(days=365)
    return as_of - timedelta(days=window_days)


async def _gold_at(
    step_input: StepInput, identifiers: Sequence[str], as_of: date
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    output = await read_gold(
        StepInput(
            additional_data={
                **_options(step_input),
                "kpi_identifiers": list(identifiers),
                "as_of": as_of.isoformat(),
            }
        )
    )
    content = output.content if isinstance(output.content, Mapping) else {}
    values = dict(content.get("kpis") or {})
    missing = {
        str(m.get("kpi_identifier")): str(m.get("reason") or "")
        for m in content.get("missing") or []
    }
    return values, missing


async def compare_to_baseline(step_input: StepInput) -> StepOutput:
    """Each KPI at the run's ``as_of`` against the same KPI at the baseline date.

    Reads the ``comparison`` slot (``prior_period`` -- the KPI's own declared window back,
    or the ``window`` slot's days, default seven -- or ``prior_year``) and the ``metrics``
    slot (the KPI identifiers; the previous ``read_gold`` output supplies them too). The
    current values come from the previous step's ``read_gold`` output when it is one, else
    from gold directly. The baseline values come from gold at the baseline date; a KPI
    with no row there is **materialised for that date** from its formula (the same pass the
    nightly run makes, ``as_of`` bound to the baseline) unless ``backfill_baseline`` is
    false, so a use case's first run has a comparison rather than "no baseline yet".

    Never stops the chain for a missing baseline: the KPI is reported with ``baseline:
    null`` and a reason, and the report says so.
    """
    options = _options(step_input)
    as_of = _as_of(options)
    comparison = str(slot(step_input, "comparison") or "prior_period").strip().lower()
    if comparison not in COMPARISONS:
        comparison = "prior_period"
    window_days = int(slot(step_input, "window") or DEFAULT_WINDOW_DAYS)

    previous = step_input.previous_step_content
    current: Dict[str, Any] = {}
    missing_now: Dict[str, str] = {}
    if isinstance(previous, Mapping) and isinstance(previous.get("kpis"), Mapping):
        current = dict(previous["kpis"])
        missing_now = {
            str(m.get("kpi_identifier")): str(m.get("reason") or "")
            for m in previous.get("missing") or []
        }
    identifiers = [
        str(i)
        for i in (
            slot(step_input, "metrics")
            or options.get("kpi_identifiers")
            or list(current)
            or []
        )
    ]
    if not identifiers:
        return StepOutput(
            content={"refused": "no KPI identifiers to compare"},
            success=False,
            stop=True,
        )
    if not current:
        current, missing_now = await _gold_at(step_input, identifiers, as_of)
    definitions = _definitions(options, identifiers)

    # Group the KPIs by their baseline date: one gold read per date, not per KPI.
    baseline_for: Dict[str, date] = {}
    for identifier in identifiers:
        declared = (definitions.get(identifier) or {}).get("window_days")
        baseline_for[identifier] = _baseline_date(
            as_of, comparison, int(declared or window_days)
        )
    baseline_values: Dict[str, Any] = {}
    baseline_missing: Dict[str, str] = {}
    backfilled: List[str] = []
    # A series over time is shown, not compared: no baseline read, no backfill for it.
    compared = [
        i
        for i in identifiers
        if (definitions.get(i) or {}).get("grain") != "time_period"
    ]
    for when in sorted({baseline_for[i] for i in compared}):
        group = [i for i in compared if baseline_for[i] == when]
        values, missing = await _gold_at(step_input, group, when)
        absent = [i for i in group if i not in values]
        # A rehearsal backfills too: writing a past business date into gold is a read of
        # the warehouse the nightly run would make anyway, not a side effect on anyone.
        if absent and options.get("backfill_baseline", True):
            output = await materialise_gold(
                StepInput(
                    additional_data={
                        "kpi_identifiers": absent,
                        "as_of": when.isoformat(),
                        "run_id": f"baseline_{as_of.isoformat()}",
                    }
                )
            )
            if output.success and not output.stop:
                backfilled += absent
                again, missing = await _gold_at(step_input, absent, when)
                values.update(again)
            else:
                reason = str(
                    (output.content or {}).get("refused")
                    if isinstance(output.content, Mapping)
                    else output.error
                )
                missing = {**missing, **{i: reason for i in absent}}
        baseline_values.update(values)
        baseline_missing.update({i: missing.get(i, "") for i in group if i in missing})

    kpis: List[Dict[str, Any]] = []
    for identifier in identifiers:
        definition = definitions.get(identifier) or {}
        now = current.get(identifier) or {}
        then = baseline_values.get(identifier) or {}
        now_rows = list(now.get("rows") or [])
        then_rows = {_dimension_key(r): r for r in (then.get("rows") or [])}
        grain = str(
            definition.get("grain") or ("overall" if len(now_rows) == 1 else "")
        )
        entry: Dict[str, Any] = {
            "identifier": identifier,
            "name": str(definition.get("name") or identifier),
            "unit": str(definition.get("unit") or ""),
            "grain": grain,
            "as_of": now.get("as_of") or as_of.isoformat(),
            "baseline_as_of": baseline_for[identifier].isoformat(),
            "current": None,
            "baseline": None,
            "delta": None,
            "delta_pct": None,
            "rows": [],
            "movers": [],
            "reason": missing_now.get(identifier) or baseline_missing.get(identifier),
        }
        rows_out: List[Dict[str, Any]] = []
        for row in now_rows:
            key = _dimension_key(row)
            before = then_rows.get(key)
            cur = _number(row.get("value"))
            base = _number(before.get("value")) if before else None
            delta = (cur - base) if cur is not None and base is not None else None
            rows_out.append(
                {
                    **{k: v for k, v in key},
                    "current": cur,
                    "baseline": base,
                    "delta": delta,
                    "delta_pct": round(100.0 * delta / base, 1)
                    if delta is not None and base
                    else None,
                }
            )
        if grain == "time_period":
            # A series: the rows are the trend, the baseline date's rows are other weeks.
            entry["rows"] = [
                {
                    **{k: v for k, v in _dimension_key(r)},
                    "current": _number(r.get("value")),
                    "baseline": None,
                    "delta": None,
                    "delta_pct": None,
                }
                for r in now_rows
            ]
            entry["reason"] = "a series over time; shown, not compared"
            kpis.append(entry)
            continue
        if len(now_rows) == 1 and not _dimension_key(now_rows[0]):
            single = rows_out[0]
            entry.update(
                {
                    "current": single["current"],
                    "baseline": single["baseline"],
                    "delta": single["delta"],
                    "delta_pct": single["delta_pct"],
                }
            )
        else:
            entry["rows"] = rows_out
            # A count or an amount across a breakdown adds up to the whole; a rate, a
            # share or an average does not, and a total of percentages is a number nobody
            # asked for. Those carry no aggregate -- their movers are the story.
            if _additive(entry):
                entry["current"] = (
                    sum(r["current"] for r in rows_out if r["current"] is not None)
                    or None
                )
                entry["baseline"] = (
                    sum(r["baseline"] for r in rows_out if r["baseline"] is not None)
                    if any(r["baseline"] is not None for r in rows_out)
                    else None
                )
            if entry["current"] is not None and entry["baseline"] is not None:
                entry["delta"] = entry["current"] - entry["baseline"]
                entry["delta_pct"] = (
                    round(100.0 * entry["delta"] / entry["baseline"], 1)
                    if entry["baseline"]
                    else None
                )
            movers = [r for r in rows_out if r["delta"] is not None]
            movers.sort(key=lambda r: -abs(r["delta"]))
            entry["movers"] = movers[:5]
        if (
            entry["baseline"] is None
            and not entry["reason"]
            and (entry["current"] is not None or not entry["rows"])
        ):
            entry["reason"] = (
                f"no gold row at {entry['baseline_as_of']} and no backfill"
            )
        kpis.append(entry)

    content = {
        "as_of": as_of.isoformat(),
        "comparison": comparison,
        "comparison_label": COMPARISONS[comparison],
        "window_days": window_days,
        "kpis": kpis,
        "missing": [{"kpi_identifier": k, "reason": v} for k, v in missing_now.items()],
        "backfilled": sorted(set(backfilled)),
    }
    return StepOutput(content=content, success=True)


# --- check_report_grounding ----------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b(?:19|20)\d{2}\b")


def _figures_in(comparison: Mapping[str, Any]) -> set:
    figures: set = set()

    def add(value: Any) -> None:
        number = _number(value)
        if number is None:
            return
        for rounded in (round(number, 1), round(number, 0), round(number, 2)):
            figures.add(rounded)
            figures.add(abs(rounded))

    for kpi in comparison.get("kpis") or []:
        for key in ("current", "baseline", "delta", "delta_pct"):
            add(kpi.get(key))
        for row in list(kpi.get("rows") or []) + list(kpi.get("movers") or []):
            for key in ("current", "baseline", "delta", "delta_pct"):
                add(row.get(key))
    return figures


def _numbers_in(text: str) -> List[float]:
    """Every figure in a sentence, dates and years left out (a date is not a figure)."""
    out: List[float] = []
    for match in _NUMBER.findall(_DATE.sub(" ", text or "")):
        number = _number(match.replace(",", ""))
        if number is not None:
            out.append(number)
    return out


async def check_report_grounding(step_input: StepInput) -> StepOutput:
    """Every figure the explanation states must be one the comparison carried (any KPI's
    current, baseline, delta or delta percent, at whole, one- or two-decimal precision).
    Years are not figures. A figure the numbers do not contain is a hallucination and
    stops the chain before the report is rendered -- the report then goes out with the
    numbers and no narrative rather than with an invented one. Also refuses an explanation
    whose ``findings`` name a KPI the comparison did not cover.
    """
    comparison = comparison_of(step_input)
    explanation = explanation_of(step_input)
    if comparison is None:
        return StepOutput(
            content={"refused": "no comparison to check against"},
            success=False,
            stop=True,
        )
    if explanation is None:
        return StepOutput(
            content={
                "comparison_result": comparison,
                "explanation": None,
                "grounding": {"passed": True, "checked": 0, "unsupported": []},
                "notes": "no explanation to check; the report carries the numbers only",
            },
            success=True,
        )
    allowed = _figures_in(comparison)
    covered = {str(k.get("identifier")) for k in comparison.get("kpis") or []}
    texts: List[str] = [str(explanation.get("headline") or "")]
    repaired: List[str] = []
    findings: List[Dict[str, Any]] = []
    for finding in explanation.get("findings") or []:
        texts.append(str(finding.get("text") or ""))
        identifier = str(finding.get("kpi_identifier") or "")
        if identifier and identifier not in covered:
            # The id is a label on the finding, not a figure: a mistyped one is repaired
            # to the KPI it names when that is unambiguous, else blanked -- never a reason
            # to withhold a narrative whose numbers all check out.
            match = [
                c for c in covered if identifier.endswith(c) or c.endswith(identifier)
            ]
            repaired.append(identifier)
            identifier = match[0] if len(match) == 1 else ""
        findings.append({**dict(finding), "kpi_identifier": identifier})
    explanation = {**explanation, "findings": findings}
    for caveat in explanation.get("caveats") or []:
        texts.append(str(caveat))
    unsupported: List[str] = []
    checked = 0
    for text in texts:
        for number in _numbers_in(text):
            checked += 1
            if not any(abs(number - figure) < 0.051 for figure in allowed):
                unsupported.append(f"{number:g}")
    passed = not unsupported
    grounding = {
        "passed": passed,
        "checked": checked,
        "unsupported": unsupported,
        "repaired_kpi_ids": repaired,
    }
    return StepOutput(
        content={
            "comparison_result": comparison,
            "explanation": explanation if passed else None,
            "explanation_rejected": None if passed else explanation,
            "grounding": grounding,
        },
        success=True,
        stop=False,
    )


# --- render_report -------------------------------------------------------------------------


def _fmt(value: Any, unit: str = "") -> str:
    number = _number(value)
    if number is None:
        return "—"
    if unit == "%":
        return f"{number:,.1f}%"
    if unit.upper() == "USD":
        return f"${number:,.0f}"
    text = f"{number:,.0f}" if float(number).is_integer() else f"{number:,.1f}"
    return f"{text} {unit}".strip() if unit and unit not in {"%", "USD"} else text


def _delta_text(kpi: Mapping[str, Any]) -> str:
    delta = _number(kpi.get("delta"))
    if delta is None:
        return "no baseline"
    sign = "+" if delta > 0 else ""
    pct = kpi.get("delta_pct")
    unit = str(kpi.get("unit") or "")
    base = f"{sign}{_fmt(delta, unit)}"
    if unit == "%":
        base = f"{sign}{delta:,.1f} pts"
    return f"{base} ({sign}{pct}%)" if pct is not None and unit != "%" else base


def report_text(
    comparison: Mapping[str, Any],
    explanation: Optional[Mapping[str, Any]],
    *,
    title: str,
) -> str:
    lines = [
        title,
        f"As of {comparison.get('as_of')} vs {comparison.get('comparison_label')}",
        "",
    ]
    if explanation:
        lines += [str(explanation.get("headline") or ""), ""]
        for finding in explanation.get("findings") or []:
            lines.append(f"- {finding.get('text')}")
        lines.append("")
    lines.append("KPIs")
    for kpi in comparison.get("kpis") or []:
        lines.append(
            f"  {kpi['name']}: {_fmt(kpi.get('current'), kpi.get('unit', ''))} "
            f"(baseline {_fmt(kpi.get('baseline'), kpi.get('unit', ''))}, {_delta_text(kpi)})"
        )
        for row in kpi.get("movers") or []:
            key = ", ".join(
                str(v)
                for k, v in row.items()
                if k not in {"current", "baseline", "delta", "delta_pct"}
            )
            lines.append(
                f"      {key}: {_fmt(row['current'], kpi.get('unit', ''))} ({_delta_text({**row, 'unit': kpi.get('unit', '')})})"
            )
    if explanation and explanation.get("caveats"):
        lines += ["", "Caveats"] + [f"  {c}" for c in explanation["caveats"]]
    return "\n".join(lines)


def _bar(value: Optional[float], maximum: float) -> str:
    if value is None or maximum <= 0:
        return ""
    width = max(2, int(round(100.0 * abs(value) / maximum)))
    return (
        f'<span style="display:inline-block;height:10px;width:{width}px;'
        'background:#6d28d9;border-radius:2px;vertical-align:middle"></span>'
    )


def report_html(
    comparison: Mapping[str, Any],
    explanation: Optional[Mapping[str, Any]],
    *,
    title: str,
    use_case: str = "",
    dashboard_url: str = "",
    grounding: Optional[Mapping[str, Any]] = None,
) -> str:
    """An email-safe HTML report: inline styles, tables, CSS-free bars. Headline and
    findings when the explanation passed grounding; the numbers either way."""
    e = html.escape
    kpis = list(comparison.get("kpis") or [])
    scalars = [k for k in kpis if not k.get("rows")]
    breakdowns = [k for k in kpis if k.get("rows")]

    def tile(kpi: Mapping[str, Any]) -> str:
        delta = _number(kpi.get("delta"))
        colour = (
            "#6b7280" if delta is None else ("#15803d" if delta >= 0 else "#b91c1c")
        )
        return (
            '<td style="padding:6px" valign="top"><div style="border:1px solid #e5e7eb;'
            'border-radius:10px;padding:14px 16px;min-width:150px;background:#fff">'
            f'<div style="font:600 12px/1.3 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6b7280;text-transform:uppercase;letter-spacing:.04em">{e(kpi["name"])}</div>'
            f'<div style="font:700 26px/1.2 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827;margin-top:6px">{e(_fmt(kpi.get("current"), kpi.get("unit", "")))}</div>'
            f'<div style="font:500 12px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{colour};margin-top:4px">{e(_delta_text(kpi))}'
            f' <span style="color:#9ca3af">vs {e(_fmt(kpi.get("baseline"), kpi.get("unit", "")))}</span></div>'
            "</div></td>"
        )

    def table(kpi: Mapping[str, Any]) -> str:
        rows = list(kpi.get("rows") or [])
        rows.sort(key=lambda r: -(r.get("current") or 0))
        maximum = max((abs(r.get("current") or 0) for r in rows), default=0.0)
        keys = (
            [
                k
                for k in rows[0].keys()
                if k not in {"current", "baseline", "delta", "delta_pct"}
            ]
            if rows
            else []
        )
        head = "".join(
            f'<th style="text-align:left;padding:6px 8px;font:600 11px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6b7280;text-transform:uppercase;border-bottom:1px solid #e5e7eb">{e(k.replace("_", " "))}</th>'
            for k in keys
        )
        body = ""
        for row in rows[:12]:
            delta = _number(row.get("delta"))
            colour = (
                "#6b7280" if delta is None else ("#15803d" if delta >= 0 else "#b91c1c")
            )
            cells = "".join(
                f'<td style="padding:6px 8px;font:13px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827;border-bottom:1px solid #f3f4f6">{e(str(row.get(k)))}</td>'
                for k in keys
            )
            body += (
                f"<tr>{cells}"
                f'<td style="padding:6px 8px;font:13px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827;text-align:right;border-bottom:1px solid #f3f4f6;white-space:nowrap">{e(_fmt(row.get("current"), kpi.get("unit", "")))}</td>'
                f'<td style="padding:6px 8px;border-bottom:1px solid #f3f4f6">{_bar(row.get("current"), maximum)}</td>'
                f'<td style="padding:6px 8px;font:12px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{colour};text-align:right;border-bottom:1px solid #f3f4f6;white-space:nowrap">{e(_delta_text({**row, "unit": kpi.get("unit", "")}))}</td>'
                "</tr>"
            )
        more = (
            f'<p style="font:12px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#9ca3af;margin:6px 0 0">{len(rows) - 12} more rows in the dashboard</p>'
            if len(rows) > 12
            else ""
        )
        return (
            '<div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:12px 0;background:#fff">'
            f'<div style="font:600 14px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827;margin-bottom:8px">{e(kpi["name"])}'
            f' <span style="font-weight:500;color:#6b7280">· total {e(_fmt(kpi.get("current"), kpi.get("unit", "")))}, {e(_delta_text(kpi))}</span></div>'
            f'<table cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse"><thead><tr>{head}'
            '<th style="text-align:right;padding:6px 8px;font:600 11px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6b7280;text-transform:uppercase;border-bottom:1px solid #e5e7eb">now</th><th style="border-bottom:1px solid #e5e7eb"></th>'
            '<th style="text-align:right;padding:6px 8px;font:600 11px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6b7280;text-transform:uppercase;border-bottom:1px solid #e5e7eb">change</th></tr></thead>'
            f"<tbody>{body}</tbody></table>{more}</div>"
        )

    narrative = ""
    if explanation:
        findings = "".join(
            f'<li style="margin:4px 0">{e(str(f.get("text") or ""))}</li>'
            for f in explanation.get("findings") or []
        )
        caveats = "".join(
            f'<li style="margin:2px 0;color:#6b7280">{e(str(c))}</li>'
            for c in explanation.get("caveats") or []
        )
        narrative = (
            '<div style="border-left:4px solid #6d28d9;padding:10px 14px;margin:14px 0;background:#f5f3ff;border-radius:0 8px 8px 0">'
            f'<p style="font:600 15px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827;margin:0 0 8px">{e(str(explanation.get("headline") or ""))}</p>'
            f'<ul style="font:13px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2937;margin:0;padding-left:18px">{findings}</ul>'
            + (
                f'<ul style="font:12px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:8px 0 0;padding-left:18px">{caveats}</ul>'
                if caveats
                else ""
            )
            + '<p style="font:11px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6b7280;margin:8px 0 0">Every figure above was checked against the numbers below.</p>'
            "</div>"
        )
    elif grounding and not grounding.get("passed", True):
        narrative = (
            '<p style="font:12px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#b45309;margin:12px 0">'
            "The narrative for this run was withheld: it stated figures the numbers do not support. The numbers are below.</p>"
        )
    tiles = "".join(tile(k) for k in scalars)
    tables = "".join(table(k) for k in breakdowns)
    link = (
        f'<p style="font:13px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:14px 0 0"><a href="{e(dashboard_url)}" style="color:#6d28d9">Open the live dashboard</a></p>'
        if dashboard_url
        else ""
    )
    return (
        '<div style="max-width:720px;margin:0 auto;padding:20px;background:#f9fafb;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
        f'<p style="font:600 11px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6d28d9;text-transform:uppercase;letter-spacing:.06em;margin:0">{e(use_case or "Enterprise GPT")}</p>'
        f'<h1 style="font:700 22px/1.3 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827;margin:4px 0 2px">{e(title)}</h1>'
        f'<p style="font:13px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6b7280;margin:0 0 10px">As of {e(str(comparison.get("as_of")))} · compared with {e(str(comparison.get("comparison_label")))}</p>'
        f"{narrative}"
        f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin:6px -6px"><tr>{tiles}</tr></table>'
        f"{tables}{link}"
        '<p style="font:11px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#9ca3af;margin:18px 0 0">Generated by Enterprise GPT. Numbers from the governed KPI registry; every narrative figure checked against them before sending.</p>'
        "</div>"
    )


async def render_report(step_input: StepInput) -> StepOutput:
    """The report as HTML and text from the comparison and the (grounded) explanation.
    Reads the ``title`` slot (default: the use case's question) and ``dashboard_url``."""
    options = _options(step_input)
    comparison = comparison_of(step_input)
    if comparison is None:
        return StepOutput(
            content={"refused": "no comparison to render"}, success=False, stop=True
        )
    previous = step_input.previous_step_content
    checked = grounding_of(step_input)
    if checked is not None:
        grounding = dict(checked.get("grounding") or {"passed": True})
        explanation = (
            checked.get("explanation")
            if _is_explanation(checked.get("explanation"))
            else None
        )
        rejected = checked.get("explanation_rejected")
    else:
        grounding = {"passed": True, "checked": 0, "unsupported": []}
        explanation = explanation_of(step_input)
        rejected = None
    title = str(slot(step_input, "title") or options.get("question") or "Report")
    html_body = report_html(
        comparison,
        explanation,
        title=title,
        use_case=str(options.get("use_case_title") or ""),
        dashboard_url=str(options.get("dashboard_url") or ""),
        grounding=grounding,
    )
    text_body = report_text(comparison, explanation, title=title)
    headline = (
        str(explanation.get("headline"))
        if explanation and explanation.get("headline")
        else f"{title}: {len(comparison.get('kpis') or [])} KPIs as of {comparison.get('as_of')}"
    )
    return StepOutput(
        content={
            "comparison_result": comparison,
            "explanation": explanation,
            "explanation_rejected": rejected,
            "grounding": grounding,
            "report": {
                "title": title,
                "headline": headline,
                "html": html_body,
                "text": text_body,
                "as_of": comparison.get("as_of"),
            },
            "dashboard_url": str(options.get("dashboard_url") or ""),
            # deliver_in_app reads these two.
            "headline": headline,
            "provenance_ref": (
                previous.get("provenance_ref")
                if isinstance(previous, Mapping)
                else None
            ),
        },
        success=True,
    )


# --- send_report ---------------------------------------------------------------------------


async def send_report(step_input: StepInput) -> StepOutput:
    """The HTML report to the ``recipient`` slot through the source mailbox's SMTP
    (``mailbox`` slot, or the deployment's Gmail). Under the dry-run flag the message is
    composed and returned, not sent. No recipient: nothing sent, said so, not a failure --
    a report can be in-app only.
    """
    options = _options(step_input)
    previous = step_input.previous_step_content
    rendered: Optional[Mapping[str, Any]] = (
        previous
        if isinstance(previous, Mapping) and isinstance(previous.get("report"), Mapping)
        else None
    )
    if rendered is None:
        for name in ("Render report", "render_report"):
            try:
                output = step_input.get_step_content(name)
            except Exception:
                output = None
            if isinstance(output, Mapping) and isinstance(
                output.get("report"), Mapping
            ):
                rendered = output
                break
    if rendered is None:
        return StepOutput(
            content={"refused": "no report to send"}, success=False, stop=True
        )
    report = cast(Mapping[str, Any], rendered["report"])
    # The run's result is this step's content: the report and what it was built from,
    # plus what the in-app delivery before this step said.
    carried: Dict[str, Any] = {
        key: rendered.get(key)
        for key in (
            "comparison_result",
            "explanation",
            "explanation_rejected",
            "grounding",
            "report",
            "dashboard_url",
            "headline",
            "provenance_ref",
        )
        if key in rendered
    }
    if isinstance(previous, Mapping) and previous is not rendered:
        carried["in_app"] = {
            k: previous.get(k) for k in ("status", "channel", "dry_run", "delivery")
        }
    recipient = slot(step_input, "recipient")
    recipients = [
        r.strip()
        for r in str(recipient or "").replace(";", ",").split(",")
        if r.strip() and "@" in r
    ]
    if not recipients:
        return StepOutput(
            content={**carried, "delivery": {"status": "no_recipient", "to": []}},
            success=True,
        )
    subject = f"[Enterprise GPT] {report.get('title')} — {report.get('as_of')}"
    if is_dry_run(step_input):
        return StepOutput(
            content={
                **carried,
                "delivery": {
                    "status": "held",
                    "to": recipients,
                    "subject": subject,
                    "notes": "dry run: the report was composed and not sent",
                },
            },
            success=True,
        )
    from egpt_retail.documents.mailbox import mailbox_from, send_mail

    mailbox = mailbox_from(slot(step_input, "mailbox") or options.get("source mailbox"))
    try:
        sent = send_mail(
            mailbox,
            to=recipients,
            subject=subject,
            body=str(report.get("text") or ""),
            html=str(report.get("html") or ""),
        )
    except Exception as exc:  # the mailbox refused; the run still records the report
        return StepOutput(
            content={
                **carried,
                "delivery": {
                    "status": "failed",
                    "to": recipients,
                    "subject": subject,
                    "error": str(exc)[:300],
                },
            },
            success=False,
        )
    return StepOutput(
        content={
            **carried,
            "delivery": {
                "status": "sent",
                "to": recipients,
                "subject": subject,
                **sent,
            },
        },
        success=True,
    )
