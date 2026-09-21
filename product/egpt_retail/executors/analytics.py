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

from egpt_retail.executors.batch import earlier_outputs, is_dry_run, slot
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
#: The comparison slot left empty: the numbers are read for the run's date and reported
#: as they are -- no baseline read, no backfill, no delta. Not a default; a choice.
NO_COMPARISON = "none"
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
    for name in (
        "Compare to baseline",
        "Take the numbers as they are",
        "compare_to_baseline",
    ):
        try:
            output = step_input.get_step_content(name)
        except Exception:
            output = None
        if isinstance(output, Mapping) and "kpis" in output:
            return dict(output)
    # A later step that carried it forward under its own key (the at-risk kind's
    # scoring and recommendation, K26).
    for output in earlier_outputs(step_input):
        content = getattr(output, "content", None)
        if isinstance(content, Mapping) and isinstance(
            content.get("comparison_result"), Mapping
        ):
            return dict(content["comparison_result"])
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
    comparison = str(slot(step_input, "comparison") or NO_COMPARISON).strip().lower()
    if comparison not in COMPARISONS:
        comparison = NO_COMPARISON
    # The window slot, when the form set one, is the person's choice and wins over each
    # KPI's declared window; blank means "each KPI's own", then the default.
    chosen_window = _number(slot(step_input, "window"))
    window_days = int(chosen_window) if chosen_window else DEFAULT_WINDOW_DAYS

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
    # The run's own date is materialised the same way a missing baseline is: a run made
    # before the nightly pass (or on a date it never covered) computes gold for that date
    # from the formulas rather than reporting "no gold row" -- the read the nightly run
    # would make, not a side effect on anyone.
    absent_now = [i for i in identifiers if i not in current]
    computed_now: List[str] = []
    if absent_now and options.get("backfill_baseline", True):
        output = await materialise_gold(
            StepInput(
                additional_data={
                    "kpi_identifiers": absent_now,
                    "as_of": as_of.isoformat(),
                    "run_id": f"asof_{as_of.isoformat()}",
                }
            )
        )
        if output.success and not output.stop:
            again, still = await _gold_at(step_input, absent_now, as_of)
            current.update(again)
            computed_now = [i for i in absent_now if i in again]
            missing_now = {**missing_now, **still}
            for identifier in computed_now:
                missing_now.pop(identifier, None)
    definitions = _definitions(options, identifiers)

    # Group the KPIs by their baseline date: one gold read per date, not per KPI.
    baseline_for: Dict[str, date] = {}
    for identifier in identifiers:
        declared = (definitions.get(identifier) or {}).get("window_days")
        days = int(chosen_window) if chosen_window else int(declared or window_days)
        baseline_for[identifier] = _baseline_date(as_of, comparison, days)
    baseline_values: Dict[str, Any] = {}
    baseline_missing: Dict[str, str] = {}
    backfilled: List[str] = []
    # A series over time is shown, not compared: no baseline read, no backfill for it.
    compared = [
        i
        for i in identifiers
        if (definitions.get(i) or {}).get("grain") != "time_period"
        and comparison != NO_COMPARISON
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
            "baseline_as_of": (
                baseline_for[identifier].isoformat()
                if comparison != NO_COMPARISON
                else None
            ),
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
        "comparison_label": COMPARISONS.get(
            comparison, "nothing -- the numbers as they are"
        ),
        "computed_now": computed_now,
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


def _figures_in_risk(
    risk: Mapping[str, Any], recommendation_result: Optional[Mapping[str, Any]]
) -> set:
    """The figures an at-risk briefing may quote (K26): the summary's counts and values,
    the model's evaluation, every carried subject's value, age, usual, risk and its
    account's RFM numbers, the plays' counts and the recommendations' totals."""
    figures: set = set()

    def add(value: Any) -> None:
        number = _number(value)
        if number is None:
            return
        for rounded in (number, round(number, 1), round(number, 2), round(number)):
            figures.add(float(rounded))
        if 0 <= number <= 1:
            for rounded in (round(number * 100), round(number * 100, 1)):
                figures.add(float(rounded))
        if number >= 1000:
            figures.add(float(round(number / 1000, 1)))
            figures.add(float(round(number / 1000)))
            figures.add(float(round(number / 1_000_000, 1)))
            figures.add(float(round(number / 1_000_000, 2)))

    for value in (risk.get("summary") or {}).values():
        if isinstance(value, Mapping):
            for v in value.values():
                add(v)
        else:
            add(value)
    for key in (
        "auc",
        "brier",
        "base_rate",
        "training_rows",
        "holdout_rows",
        "closed_quotes",
    ):
        add((risk.get("model") or {}).get(key))
    add(risk.get("churn_days"))
    add(risk.get("subjects_total"))
    for number in _numbers_in(str(risk.get("horizon") or "")):
        add(number)
    for level in (risk.get("levels") or {}).values():
        add(level)
    for subject in risk.get("subjects") or []:
        for key in (
            "quote_id",
            "value",
            "age_days",
            "usual_days",
            "risk",
            "p_convert",
            "value_at_risk",
        ):
            add(subject.get(key))
        for key in ("recency_days", "frequency", "monetary", "won", "lost"):
            add((subject.get("account") or {}).get(key))
        for driver in subject.get("drivers") or []:
            add(driver.get("value"))
            add(driver.get("typical"))
    if recommendation_result:
        for value in (recommendation_result.get("summary") or {}).values():
            add(value)
        for value in (recommendation_result.get("plays") or {}).values():
            add(value)
        for record in list(recommendation_result.get("recommendations") or []) + list(
            recommendation_result.get("alerts") or []
        ):
            add(record.get("value_at_stake"))
            add(record.get("risk_score"))
            add((record.get("action") or {}).get("sla_hours"))
    return figures


def _figures_in_diagnosis(diagnosis: Mapping[str, Any]) -> set:
    """Every number the decomposition produced -- headline, drivers, intersections,
    evidence counts and tallies -- at whole, one- and two-decimal precision. Dates and
    identifiers are not figures; the walk skips strings."""
    figures: set = set()

    def add(value: Any) -> None:
        if isinstance(value, bool):
            return
        number = _number(value)
        if number is None:
            return
        for rounded in (round(number, 1), round(number, 0), round(number, 2)):
            figures.add(rounded)
            figures.add(abs(rounded))
        # A share of the gap is quoted as a percent by the narrator ("51% of the change").
        if -1.0 <= number <= 1.0:
            for rounded in (round(number * 100, 0), round(number * 100, 1)):
                figures.add(rounded)
                figures.add(abs(rounded))

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, inner in value.items():
                if key in {
                    "as_of",
                    "baseline_as_of",
                    "kpi_identifier",
                    "dimension",
                    "value",
                }:
                    continue
                walk(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                walk(inner)
        elif isinstance(value, (int, float)):
            add(value)

    walk(
        {
            k: v
            for k, v in diagnosis.items()
            if k not in {"candidates", "dimensions_searched"}
        }
    )
    # The caveats are text the narrator copies too ("96 slices below the floor of 24").
    for caveat in diagnosis.get("caveats") or []:
        for number in _numbers_in(str(caveat)):
            add(number)
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
    from egpt_retail.executors.diagnostics import diagnosis_of
    from egpt_retail.executors.predict import risk_of
    from egpt_retail.executors.recommend import recommendations_of

    comparison = comparison_of(step_input)
    explanation = explanation_of(step_input)
    diagnosis = diagnosis_of(step_input)
    # The at-risk kind (K26): the briefing quotes the scoring's and the plays' figures.
    risk = risk_of(step_input)
    recommendation_result = recommendations_of(step_input)
    from egpt_retail.executors.recommend import outcomes_of

    outcomes = outcomes_of(step_input)
    if comparison is None and diagnosis is None and risk is None:
        return StepOutput(
            content={"refused": "no comparison to check against"},
            success=False,
            stop=True,
        )
    comparison = comparison or {
        "as_of": (diagnosis or {}).get("headline", {}).get("as_of")
        if diagnosis
        else (risk or {}).get("as_of"),
        "kpis": [],
    }
    extra: Dict[str, Any] = {"diagnosis": diagnosis} if diagnosis is not None else {}
    if risk is not None:
        extra["risk"] = risk
    if recommendation_result is not None:
        extra["recommendation_result"] = recommendation_result
    if outcomes is not None:
        extra["outcomes"] = outcomes
    if explanation is None:
        return StepOutput(
            content={
                "comparison_result": comparison,
                "explanation": None,
                "grounding": {"passed": True, "checked": 0, "unsupported": []},
                "notes": "no explanation to check; the report carries the numbers only",
                **extra,
            },
            success=True,
        )
    allowed = _figures_in(comparison)
    covered = {str(k.get("identifier")) for k in comparison.get("kpis") or []}
    if diagnosis is not None:
        # The diagnostic kind (K25): the narrative quotes the decomposition's figures.
        allowed |= _figures_in_diagnosis(diagnosis)
        covered.add(str((diagnosis.get("headline") or {}).get("kpi_identifier") or ""))
    if risk is not None:
        allowed |= _figures_in_risk(risk, recommendation_result)
        for level in ((outcomes or {}).get("precision") or {}).values():
            for value in (level or {}).values():
                number = _number(value)
                if number is not None:
                    allowed.add(float(number))
                    if 0 <= number <= 1:
                        allowed.add(float(round(number * 100)))
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
            **extra,
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
        return "not compared" if kpi.get("baseline_as_of") is None else "no baseline"
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
    diagnosis: Optional[Mapping[str, Any]] = None,
    risk: Optional[Mapping[str, Any]] = None,
    recommendation_result: Optional[Mapping[str, Any]] = None,
) -> str:
    lines = [
        title,
        f"As of {comparison.get('as_of')}"
        + (
            f" vs {comparison.get('comparison_label')}"
            if comparison.get("comparison") != NO_COMPARISON
            and comparison.get("comparison_label")
            else ""
        ),
        "",
    ]
    if explanation:
        lines += [str(explanation.get("headline") or ""), ""]
        for finding in explanation.get("findings") or []:
            lines.append(f"- {finding.get('text')}")
        lines.append("")
    if diagnosis:
        head = diagnosis.get("headline") or {}
        unit = str(head.get("unit") or "")
        lines.append(
            f"Where the change sits: {head.get('name')} {_fmt(head.get('baseline'), unit)} -> "
            f"{_fmt(head.get('current'), unit)} ({head.get('baseline_as_of')} -> {head.get('as_of')})"
        )
        for entry in (diagnosis.get("ranking") or [])[:5]:
            for driver in (entry.get("drivers") or [])[:1]:
                share = driver.get("share_of_gap")
                lines.append(
                    f"  {driver.get('dimension')} = {driver.get('value')}: {driver.get('effect')}, "
                    f"{_fmt(driver.get('baseline'), unit)} -> {_fmt(driver.get('current'), unit)}, "
                    f"{f'{share * 100:.0f}% of the change' if share is not None else 'share n/a'}, "
                    f"{driver.get('support')} rows"
                )
        evidence = diagnosis.get("evidence") or {}
        for column, values in (evidence.get("tallies") or {}).items():
            lines.append(
                f"  {column}: "
                + ", ".join(f"{v.get('value')} ({v.get('count')})" for v in values[:5])
            )
        already = {str(c) for c in (explanation or {}).get("caveats") or []}
        for caveat in diagnosis.get("caveats") or []:
            if str(caveat) not in already:
                lines.append(f"  note: {caveat}")
        lines.append("")
    if risk:
        lines += _risk_text(risk, recommendation_result)
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


#: The chart hues in fixed order -- the interface's ``--chart-1..6`` -- one per
#: breakdown table, so the report's tables read as different things, as the dashboard's
#: tiles do. Never a status colour.
CHART_HUES = ("#731dd8", "#0d9488", "#4361ee", "#d97706", "#db2777", "#0891b2")


def _bar(value: Optional[float], maximum: float, colour: str = CHART_HUES[0]) -> str:
    if value is None or maximum <= 0:
        return ""
    width = max(2, int(round(100.0 * abs(value) / maximum)))
    return (
        f'<span style="display:inline-block;height:10px;width:{width}px;'
        f'background:{colour};border-radius:2px;vertical-align:middle"></span>'
    )


_FONT = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
_EFFECT_WORDS = {
    "rate": "its own rate moved",
    "mix": "its share of the mix moved",
    "volume": "its count moved",
}


def _bar_html(share: float, *, colour: str, width: int = 160) -> str:
    """A signed horizontal bar, email-safe: two cells, the fill sized by |share| (capped)."""
    filled = max(2, min(width, int(round(abs(share) * width))))
    return (
        f'<div style="display:inline-block;width:{width}px;height:8px;background:#ede9fe;border-radius:4px;vertical-align:middle;overflow:hidden">'
        f'<div style="width:{filled}px;height:8px;background:{colour};border-radius:4px"></div></div>'
    )


def _history_html(history: Sequence[Any], current: Any, unit: str) -> str:
    """Twelve small bars for the trailing values, the current one last and darker: the
    eye sees "usual" without a number. Inline-block divs, which every mail client draws."""
    raw = [_number(v) for v in list(history)[::-1]] + [_number(current)]
    values: List[float] = [v for v in raw if v is not None]
    if len(values) < 3:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    bars = ""
    for i, v in enumerate(values):
        h = 6 + int(round(30 * (v - low) / span))
        last = i == len(values) - 1
        bars += (
            f'<div title="{_fmt(v, unit)}" style="display:inline-block;width:9px;height:{h}px;margin-right:3px;vertical-align:bottom;'
            f'background:{"#6d28d9" if last else "#c4b5fd"};border-radius:2px"></div>'
        )
    from statistics import median as _median

    usual = _median(values[:-1]) if len(values) > 1 else values[-1]
    return (
        f'<div style="height:38px;line-height:0;margin-top:6px">{bars}</div>'
        f'<div style="font:11px {_FONT};color:#6b7280;margin-top:3px;white-space:nowrap">'
        f"{_fmt(values[0], unit)} … <b>{_fmt(values[-2], unit)} → {_fmt(values[-1], unit)}</b> · usual {_fmt(usual, unit)}</div>"
        f'<div style="font:10px {_FONT};color:#9ca3af;margin-top:1px">the last {len(values) - 1} periods, then now</div>'
    )


def _diagnosis_html(diagnosis: Mapping[str, Any]) -> str:
    """The diagnostic kind's section (K25), laid out as an insight rather than a table: the
    KPI's move as two numbers; the driver as a card -- what it is, what it did, how much
    of the change it accounts for as a bar, how unusual against its own history as a
    sparkline of bars; then what else moved, each with a signed bar; then inside the driver;
    then why (the reason tally as bars). Email-safe: tables, inline styles, no scripts."""
    e = html.escape
    head = diagnosis.get("headline") or {}
    unit = str(head.get("unit") or "")
    winner = diagnosis.get("winner") or {}
    delta = _number(head.get("delta"))
    up = delta is not None and delta >= 0
    delta_colour = "#15803d" if up else "#b91c1c"
    delta_text = (
        (
            f"{'+' if up else ''}{delta:,.1f} pts"
            if unit == "%"
            else f"{'+' if up else ''}{_fmt(delta, unit)}"
        )
        if delta is not None
        else "—"
    )
    z_bar = float(diagnosis.get("z_bar") or 2)

    hero = (
        f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%"><tr>'
        f'<td style="vertical-align:bottom;padding:0"><div style="font:600 11px {_FONT};color:#6d28d9;text-transform:uppercase;letter-spacing:.06em">Why it changed</div>'
        f'<div style="font:600 15px {_FONT};color:#111827;margin-top:2px">{e(str(head.get("name", "")))}</div>'
        f'<div style="font:700 34px/1.1 {_FONT};color:#111827;margin-top:6px"><span style="color:#9ca3af;font-weight:500">{e(_fmt(head.get("baseline"), unit))}</span> '
        f'<span style="color:#9ca3af">→</span> {e(_fmt(head.get("current"), unit))} '
        f'<span style="display:inline-block;font:600 13px {_FONT};color:{delta_colour};background:{"#dcfce7" if up else "#fee2e2"};border-radius:999px;padding:3px 10px;vertical-align:middle;margin-left:6px">{e(delta_text)}</span></div>'
        f'<div style="font:12px {_FONT};color:#6b7280;margin-top:4px">{e(str(head.get("baseline_as_of")))} → {e(str(head.get("as_of")))} · {e(str(head.get("rows_in_period")))} rows in the period</div></td>'
        f'<td style="vertical-align:bottom;padding:0 0 0 16px;text-align:right;width:160px"><div style="font:600 10px {_FONT};color:#6b7280;text-transform:uppercase;letter-spacing:.05em;text-align:left">Trend</div>'
        f"{_history_html(head.get('history') or [], head.get('current'), unit)}</td>"
        f"</tr></table>"
    )

    driver = ""
    if winner:
        share = _number(winner.get("share_of_gap"))
        z = _number(winner.get("z"))
        unusual = z is not None and abs(z) >= z_bar
        driver = (
            f'<div style="border:1px solid #d9c2f7;border-radius:12px;padding:16px 18px;margin:14px 0 0;background:linear-gradient(#ffffff,#f7f2ff)">'
            f'<div style="font:600 11px {_FONT};color:#6d28d9;text-transform:uppercase;letter-spacing:.06em">The driver · {e(str(winner.get("dimension", "")).replace("_", " "))}</div>'
            f'<div style="font:700 26px/1.2 {_FONT};color:#111827;margin-top:4px">{e(str(winner.get("value", "")))}</div>'
            f'<div style="font:14px {_FONT};color:#374151;margin-top:6px">{e(_EFFECT_WORDS.get(str(winner.get("effect")), ""))}: '
            f"<b>{e(_fmt(winner.get('baseline'), unit))} → {e(_fmt(winner.get('current'), unit))}</b> over {e(str(winner.get('support')))} rows</div>"
            f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin-top:12px"><tr>'
            f'<td style="padding:0 18px 0 0;vertical-align:top"><div style="font:600 11px {_FONT};color:#6b7280;text-transform:uppercase;letter-spacing:.05em">Of the change</div>'
            f'<div style="font:700 22px {_FONT};color:#111827;margin-top:2px">{e(f"{share * 100:.0f}%" if share is not None else "—")}</div>'
            f'<div style="margin-top:4px">{_bar_html(share or 0, colour="#6d28d9")}</div></td>'
            f'<td style="padding:0 0 0 18px;vertical-align:top;border-left:1px solid #e9d5ff"><div style="font:600 11px {_FONT};color:#6b7280;text-transform:uppercase;letter-spacing:.05em">Against its usual</div>'
            f'<div style="font:700 22px {_FONT};color:{"#b91c1c" if unusual else "#111827"};margin-top:2px">{e(_fmt(winner.get("norm"), unit))}'
            f' <span style="font:600 12px {_FONT};color:{"#b91c1c" if unusual else "#6b7280"}">{e(("unusual · z " + f"{z:+.1f}") if unusual and z is not None else ("within its range" if z is not None else "no history"))}</span></div>'
            f"</td></tr></table>"
            "</div>"
        )

    also = ""
    tops = []
    for entry in diagnosis.get("ranking") or []:
        for d in (entry.get("drivers") or [])[:1]:
            if (
                winner
                and d.get("dimension") == winner.get("dimension")
                and d.get("value") == winner.get("value")
            ):
                continue
            tops.append(d)
    if tops:
        rows = ""
        for d in tops[:5]:
            share = _number(d.get("share_of_gap")) or 0.0
            same_way = (share >= 0) == up if delta is not None else share >= 0
            colour = "#6d28d9" if same_way else "#f59e0b"
            z = _number(d.get("z"))
            unusual = z is not None and abs(z) >= z_bar
            rows += (
                f"<tr>"
                f'<td style="padding:7px 10px 7px 0;font:13px {_FONT};color:#111827;white-space:nowrap"><span style="color:#6b7280">{e(str(d.get("dimension", "")).replace("_", " "))} · </span><b>{e(str(d.get("value", "")))}</b>'
                + (
                    f' <span style="font:600 10px {_FONT};color:#b91c1c;background:#fee2e2;border-radius:999px;padding:1px 6px;vertical-align:middle">unusual</span>'
                    if unusual
                    else ""
                )
                + f"</td>"
                f'<td style="padding:7px 10px;font:12px {_FONT};color:#374151;white-space:nowrap">{e(_fmt(d.get("baseline"), unit))} → {e(_fmt(d.get("current"), unit))}</td>'
                f'<td style="padding:7px 0 7px 10px;white-space:nowrap">{_bar_html(share, colour=colour, width=120)} <span style="font:12px {_FONT};color:{"#374151" if same_way else "#b45309"};margin-left:6px">{e(f"{share * 100:+.0f}%")}{"" if same_way else " · the other way"}</span></td>'
                f"</tr>"
            )
        also = (
            f'<div style="font:600 11px {_FONT};color:#6b7280;text-transform:uppercase;letter-spacing:.06em;margin:18px 0 4px">Also moving · share of the change</div>'
            f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse">{rows}</table>'
        )

    inside = ""
    for entry in (diagnosis.get("intersections") or [])[:1]:
        top = (entry.get("drivers") or [None])[0]
        if top:
            inside = (
                f'<div style="font:600 11px {_FONT};color:#6b7280;text-transform:uppercase;letter-spacing:.06em;margin:18px 0 4px">Inside {e(str(winner.get("value", "")))}</div>'
                f'<div style="font:13px {_FONT};color:#374151">{e(str(top.get("dimension", "")).replace("_", " "))} · <b>{e(str(top.get("value", "")))}</b> — {e(_EFFECT_WORDS.get(str(top.get("effect")), ""))}, '
                f"{e(_fmt(top.get('baseline'), unit))} → {e(_fmt(top.get('current'), unit))}, {e(str(top.get('support')))} rows.</div>"
            )

    why = ""
    evidence = diagnosis.get("evidence") or {}
    for column, values in (evidence.get("tallies") or {}).items():
        values = list(values)[:6]
        top_n = max((int(v.get("count") or 0) for v in values), default=1) or 1
        rows = "".join(
            f'<tr><td style="padding:4px 10px 4px 0;font:13px {_FONT};color:#111827;white-space:nowrap">{e(str(v.get("value")))}</td>'
            f'<td style="padding:4px 0;white-space:nowrap">{_bar_html(int(v.get("count") or 0) / top_n, colour="#0d9488", width=140)} <span style="font:12px {_FONT};color:#374151;margin-left:6px">{v.get("count")}</span></td></tr>'
            for v in values
        )
        side = "that did not convert" if evidence.get("side") == "losing" else "rows"
        why = (
            f'<div style="font:600 11px {_FONT};color:#6b7280;text-transform:uppercase;letter-spacing:.06em;margin:18px 0 4px">Why · {e(column.replace("_", " "))} across the {e(str(evidence.get("count")))} {e(side)}</div>'
            f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse">{rows}</table>'
            f'<div style="font:11px {_FONT};color:#9ca3af;margin-top:4px">The rows themselves are on the run in the app.</div>'
        )

    caveats = "".join(
        f'<li style="margin:2px 0">{e(str(c))}</li>'
        for c in diagnosis.get("caveats") or []
    )
    foot = (
        f'<div style="font:11px {_FONT};color:#9ca3af;margin-top:16px;padding-top:10px;border-top:1px solid #e5e7eb">'
        f"Searched across {e(', '.join(str(d).replace('_', ' ') for d in diagnosis.get('dimensions_searched') or []))} · how sure: {e({'clear': 'very sure', 'usual': 'balanced', 'more': 'show more'}.get(str(diagnosis.get('sensitivity')), str(diagnosis.get('sensitivity'))))} · "
        f"a slice needs {e(str(diagnosis.get('support_floor')))} rows to be named · the norm is each slice's last {e(str(diagnosis.get('history_periods')))} periods."
        + (
            f'<ul style="margin:6px 0 0;padding-left:16px">{caveats}</ul>'
            if caveats
            else ""
        )
        + "</div>"
    )
    return (
        f'<div style="border:1px solid #e5e7eb;border-radius:12px;padding:18px 20px;margin:12px 0;background:#fff">'
        f"{hero}{driver}{also}{inside}{why}{foot}</div>"
    )


def _eyebrow(use_case: str, title: str) -> str:
    """The line above the title: the use case's name, minus the title when the two start
    the same way ("Why did X change? -- weekly report" over "Why did X change?" reads as
    "Enterprise GPT · weekly report")."""
    if not use_case:
        return "Enterprise GPT"
    rest = use_case[len(title) :].strip(" -—·") if use_case.startswith(title) else ""
    if use_case.startswith(title):
        return f"Enterprise GPT · {rest}" if rest else "Enterprise GPT"
    return use_case


def report_html(
    comparison: Mapping[str, Any],
    explanation: Optional[Mapping[str, Any]],
    *,
    title: str,
    use_case: str = "",
    dashboard_url: str = "",
    grounding: Optional[Mapping[str, Any]] = None,
    diagnosis: Optional[Mapping[str, Any]] = None,
    risk: Optional[Mapping[str, Any]] = None,
    recommendation_result: Optional[Mapping[str, Any]] = None,
    outcomes: Optional[Mapping[str, Any]] = None,
    channel: str = "app",
) -> str:
    """An email-safe HTML report: inline styles, tables, CSS-free bars. Headline and
    findings when the explanation passed grounding; the numbers either way; the
    decomposition when the run made one (K25); the at-risk list and its plays when the
    run scored one (K26)."""
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
            + (
                f' <span style="color:#9ca3af">vs {e(_fmt(kpi.get("baseline"), kpi.get("unit", "")))}</span>'
                if kpi.get("baseline_as_of")
                else ""
            )
            + "</div></div></td>"
        )

    def table(kpi: Mapping[str, Any], hue: str = CHART_HUES[0]) -> str:
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
                f'<td style="padding:6px 8px;border-bottom:1px solid #f3f4f6">{_bar(row.get("current"), maximum, hue)}</td>'
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
    # A diagnosis carries its KPI in its own hero; the tile would say the same twice.
    diagnosed = (
        {str((diagnosis or {}).get("headline", {}).get("kpi_identifier") or "")}
        if diagnosis
        else set()
    )
    tiles = "".join(
        tile(k) for k in scalars if str(k.get("identifier")) not in diagnosed
    )
    tables = "".join(
        table(k, CHART_HUES[i % len(CHART_HUES)]) for i, k in enumerate(breakdowns)
    )
    link = (
        f'<p style="font:13px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:14px 0 0"><a href="{e(dashboard_url)}" style="color:#6d28d9">Open the live dashboard</a></p>'
        if dashboard_url
        else ""
    )
    return (
        '<div style="max-width:720px;margin:0 auto;padding:20px;background:#f9fafb;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
        f'<p style="font:600 11px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6d28d9;text-transform:uppercase;letter-spacing:.06em;margin:0">{e(_eyebrow(use_case, title))}</p>'
        f'<h1 style="font:700 22px/1.3 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827;margin:4px 0 2px">{e(title)}</h1>'
        f'<p style="font:13px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6b7280;margin:0 0 10px">As of {e(str(comparison.get("as_of")))}'
        + (
            f" · compared with {e(str(comparison.get('comparison_label')))}"
            if comparison.get("comparison") != NO_COMPARISON
            and comparison.get("comparison_label")
            else ""
        )
        + "</p>"
        f"{narrative}"
        + (_diagnosis_html(diagnosis) if diagnosis else "")
        + (
            _risk_html(
                risk,
                recommendation_result,
                outcomes,
                channel=channel,
                dashboard_url=dashboard_url,
            )
            if risk
            else ""
        )
        + f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin:6px -6px"><tr>{tiles}</tr></table>'
        f"{tables}{link}"
        '<p style="font:11px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#9ca3af;margin:18px 0 0">Generated by Enterprise GPT. Numbers from the governed KPI registry; every narrative figure checked against them before sending.</p>'
        "</div>"
    )


# --- the at-risk section (K26) ------------------------------------------------------------

_FONT = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
_LEVEL_COLOUR = {"high": "#b91c1c", "medium": "#b45309", "low": "#15803d"}
_LEVEL_FILL = {"high": "#fee2e2", "medium": "#fef3c7", "low": "#dcfce7"}


def _money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if abs(number) >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    if abs(number) >= 10_000:
        return f"${number / 1000:.0f}K"
    return f"${number:,.0f}"


def _risk_text(
    risk: Mapping[str, Any], recommendation_result: Optional[Mapping[str, Any]]
) -> List[str]:
    summary = risk.get("summary") or {}
    model = risk.get("model") or {}
    lines = [
        "At-risk pipeline",
        f"  Open quotes: {summary.get('open_quotes')} worth {_money(summary.get('value_open'))}; "
        f"{summary.get('stalled')} already past the usual time to close",
        f"  Still within the usual range: {summary.get('within_range')}, of which "
        f"{summary.get('at_risk_within_range')} at risk worth "
        f"{_money(summary.get('value_at_risk_within_range'))}",
        f"  Accounts: {summary.get('at_risk_accounts')} of {summary.get('accounts_scored')} at risk "
        f"of churn ({risk.get('churn_days')} days without a quote)",
        f"  Model: AUC {model.get('auc')} on {model.get('holdout_rows')} held-out rows, "
        f"trained on {model.get('closed_quotes')} closed quotes",
    ]
    if recommendation_result:
        plays = recommendation_result.get("plays") or {}
        rs = recommendation_result.get("summary") or {}
        lines.append(
            f"  Recommendations: {rs.get('recommendations')} worth {_money(rs.get('value_at_stake'))}; "
            f"alerts: {rs.get('alerts')} to {rs.get('owners')} reps"
        )
        for record in recommendation_result.get("recommendations") or []:
            action = record.get("action") or {}
            lines.append(
                f"    {record.get('subject_label')} | {record.get('account_label')} | "
                f"risk {record.get('risk_score')} ({record.get('risk_level')}) | "
                f"{action.get('title')} — {action.get('because')} | {action.get('owner')}"
            )
        if plays:
            lines.append(
                "  Plays: "
                + ", ".join(
                    f"{k} {v}" for k, v in sorted(plays.items(), key=lambda kv: -kv[1])
                )
            )
    lines.append("")
    return lines


def _precision_words(outcomes: Optional[Mapping[str, Any]]) -> str:
    """The use case's own precision so far, in one clause: of the quotes named high
    risk whose horizon has ended, how many did not convert."""
    precision = (outcomes or {}).get("precision") or {}
    parts = []
    for level in ("high", "medium"):
        p = precision.get(level) or {}
        if p.get("named"):
            parts.append(
                f"of {p['named']} named {level} risk, {p['did_not_convert']} did not "
                f"convert ({float(p['rate'] or 0) * 100:.0f}%)"
            )
    return "; ".join(parts)


#: The ranked list's styles, one entry per role. The app renders them as a stylesheet
#: (every row light, the whole list scrolls); an email client drops stylesheets, so the
#: email renders the same roles inline on the rows it carries.
_NBA: Mapping[str, str] = {
    "th": f"text-align:left;padding:6px 8px;font:600 11px {_FONT};color:#6b7280;text-transform:uppercase;border-bottom:1px solid #e5e7eb;background:#fff",
    "thc": f"text-align:center;padding:6px 8px;font:600 11px {_FONT};color:#6b7280;text-transform:uppercase;border-bottom:1px solid #e5e7eb;background:#fff",
    "td": f"padding:9px 8px;border-bottom:1px solid #f3f4f6;font:13px {_FONT};color:#111827;vertical-align:top",
    "n": f"padding:9px 6px 9px 10px;border-bottom:1px solid #f3f4f6;font:12px {_FONT};color:#9ca3af;vertical-align:top;text-align:right;width:22px",
    "r": f"padding:9px 8px;border-bottom:1px solid #f3f4f6;font:13px {_FONT};color:#111827;vertical-align:top;text-align:center;white-space:nowrap",
    "t": "font-weight:600",
    "m": "color:#6b7280;font-size:12px;margin-top:1px",
    "p": "font-weight:600;color:#4c1d95",
    "w": "color:#374151;font-size:12px;margin-top:2px",
    "o": "font-weight:600;font-size:12px;color:#6d28d9;margin-top:4px",
    "ch": "color:#b45309",
    "pl": f"display:inline-block;padding:2px 8px;border-radius:999px;font:600 11px {_FONT}",
    "hi": "color:#b91c1c;background:#fee2e2",
    "me": "color:#b45309;background:#fef3c7",
    "lo": "color:#15803d;background:#dcfce7",
}


def _nba_attr(role: str, inline: bool, extra: str = "") -> str:
    """``class="…"`` for the app, ``style="…"`` for an email; ``extra`` is appended to
    the style either way (a row's stripe colour is data, not a role)."""
    if inline:
        base = ";".join(_NBA[r] for r in role.split() if r in _NBA)
        return f' style="{base}{";" + extra if extra else ""}"'
    return f' class="{role}"' + (f' style="{extra}"' if extra else "")


_NBA_STYLE = (
    "<style>.nb{width:100%;border-collapse:collapse}.nb th{position:sticky;top:0}"
    ".nb tr.z td{background:#fafafa}"
    + "".join(
        f".nb .{k}{{{v}}}" for k, v in _NBA.items() if k not in ("th", "thc", "td")
    )
    + f".nb th{{{_NBA['th']}}}.nb th.thc{{{_NBA['thc']}}}.nb td{{{_NBA['td']}}}"
    + "</style>"
)

#: How many recommendations an email carries: Gmail clips a message past 102 KB, and a
#: row is close to a kilobyte, so the email shows the top of the list and links to the rest.
EMAIL_ROWS = 20
#: Past this the client clips the message, so the short list is sent instead.
EMAIL_LIMIT_BYTES = 96 * 1024


def _risk_html(
    risk: Mapping[str, Any],
    recommendation_result: Optional[Mapping[str, Any]],
    outcomes: Optional[Mapping[str, Any]] = None,
    *,
    channel: str = "app",
    dashboard_url: str = "",
) -> str:
    e = html.escape
    summary = risk.get("summary") or {}
    model = risk.get("model") or {}
    churn = risk.get("churn_days")

    def stat(label: str, value: str, note: str = "", colour: str = "#111827") -> str:
        edge = colour if colour != "#111827" else "#6d28d9"
        return (
            f'<td style="padding:6px" valign="top"><div style="border:1px solid #e5e7eb;border-top:3px solid {edge};border-radius:10px;padding:12px 14px;min-width:140px;background:#fff">'
            f'<div style="font:600 11px/1.3 {_FONT};color:#6b7280;text-transform:uppercase;letter-spacing:.04em">{e(label)}</div>'
            f'<div style="font:700 24px/1.2 {_FONT};color:{colour};margin-top:6px">{e(value)}</div>'
            + (
                f'<div style="font:500 12px/1.4 {_FONT};color:#6b7280;margin-top:4px">{e(note)}</div>'
                if note
                else ""
            )
            + "</div></td>"
        )

    hero = (
        '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin:6px -6px"><tr>'
        + stat(
            "Act now",
            str(summary.get("at_risk_within_range", 0)),
            f"quotes at risk, still in range · {_money(summary.get('value_at_risk_within_range'))}",
            "#b91c1c",
        )
        + stat(
            "Already stalled",
            str(summary.get("stalled", 0)),
            f"of {summary.get('open_quotes', 0)} open quotes past the usual time to close",
        )
        + stat(
            "Accounts at risk",
            str(summary.get("at_risk_accounts", 0)),
            f"of {summary.get('accounts_scored', 0)} · no quote in {churn} days",
            "#b45309",
        )
        + "</tr></table>"
    )

    def pill(level: str) -> str:
        return (
            f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;font:600 11px {_FONT};'
            f'color:{_LEVEL_COLOUR.get(level, "#374151")};background:{_LEVEL_FILL.get(level, "#f3f4f6")}">{e(level)}</span>'
        )

    ranked = ""
    if recommendation_result:
        records = list(recommendation_result.get("recommendations") or [])
        shown = records[:EMAIL_ROWS] if channel == "email_short" else records
        inline = channel.startswith("email")
        rows = ""
        for rank, record in enumerate(shown, start=1):
            context = record.get("context") or {}
            account = context.get("account") or {}
            action = record.get("action") or {}
            because = str(action.get("because") or "")
            # The play's reason and a driver can be the same fact; say it once.
            drivers = "; ".join(
                str(d.get("text") or "")
                for d in (record.get("drivers") or [])
                if str(d.get("text") or "") not in because
            )[:160]
            stalled = context.get("stalled")
            pct = f"{float(record.get('risk_score') or 0) * 100:.0f}%"
            hours = int(action.get("sla_hours") or 0)
            sla = (
                f" · within {hours // 24}d"
                if hours >= 24
                else (f" · within {hours}h" if hours else "")
            )
            age = f"{context.get('age_days')} of {context.get('usual_days')} days" + (
                " · past usual" if stalled else ""
            )
            level = str(record.get("risk_level") or "low")
            stripe = _LEVEL_COLOUR.get(level, "#e5e7eb")
            churn_note = (
                f" · <span{_nba_attr('ch', inline)}>account at risk of churn</span>"
                if account.get("at_risk")
                else ""
            )
            rep_note = (
                " · rep " + e(str(record.get("owner"))) if record.get("owner") else ""
            )
            # Classes, not inline styles: a row is a third of the bytes, which is what
            # lets the whole list travel in the email under the client's clip limit.
            a = lambda role, extra="": _nba_attr(role, inline, extra)  # noqa: E731
            zebra = (
                ' style="background:#fafafa"'
                if inline and rank % 2 == 0
                else (' class="z"' if rank % 2 == 0 else "")
            )
            rows += (
                f"<tr{zebra}>"
                f"<td{a('n', f'border-left:3px solid {stripe}')}>{rank}</td>"
                f"<td{a('td') if inline else ''}><div{a('t')}>{e(str(record.get('subject_label') or ''))}</div>"
                f"<div{a('m')}>{e(str(record.get('account_label') or ''))} · {e(str(context.get('territory') or ''))} · {e(age)}</div>"
                f"<div{a('m')}>RFM {e(str(account.get('segment') or '—'))}{churn_note}{rep_note}</div></td>"
                f"<td{a('r')}><span{a('pl ' + level[:2])}>{e(level)}</span><div{a('t')}>{e(pct)}</div><div{a('m')}>{e(_money(record.get('value_at_stake')))}</div></td>"
                f"<td{a('td') if inline else ''}><div{a('p')}>{e(str(action.get('title') or ''))}</div>"
                f"<div{a('w')}>{e(because)}</div>"
                f"<div{a('m')}>{e(drivers)}</div>"
                f"<div{a('o')}>{e(str(action.get('owner') or ''))}{e(sla)}</div></td></tr>"
            )
        plays = recommendation_result.get("plays") or {}
        chips = "".join(
            f'<span style="display:inline-block;margin:2px 6px 2px 0;padding:3px 10px;border-radius:999px;background:#ede9fe;color:#4c1d95;font:600 11px {_FONT}">{e(str(k).replace("_", " ").title())} · {v}</span>'
            for k, v in sorted(plays.items(), key=lambda kv: -kv[1])
        )
        rs = recommendation_result.get("summary") or {}
        left = len(records) - len(shown)
        more = (
            f'<p style="font:13px {_FONT};color:#374151;margin:10px 0 0;padding:8px 10px;background:#f5f3ff;border-radius:8px">The first {len(shown)} of {len(records)} here; the full report, with every one, is attached. '
            + (
                f'<a href="{e(dashboard_url)}" style="color:#6d28d9;font-weight:600">Open the live dashboard</a> for all {len(records)}, searchable by account, rep or territory.'
                if dashboard_url
                else f"All {len(records)} are on the dashboard and the use case's Recommendations tab."
            )
            + "</p>"
            if left > 0
            else ""
        )
        alerts = list(recommendation_result.get("alerts") or [])
        by_rep: Dict[str, int] = {}
        for a in alerts:
            by_rep[str(a.get("owner") or "—")] = (
                by_rep.get(str(a.get("owner") or "—"), 0) + 1
            )
        alert_line = (
            f'<p style="font:13px {_FONT};color:#374151;margin:10px 0 0">Alert digest: {len(alerts)} high-risk quotes across {len(by_rep)} reps — '
            + e(
                ", ".join(
                    f"{rep} ({n})"
                    for rep, n in sorted(by_rep.items(), key=lambda kv: -kv[1])[:8]
                )
            )
            + "</p>"
            if alerts
            else ""
        )
        ranked = (
            '<div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:12px 0;background:#fff">'
            f'<div style="font:600 14px {_FONT};color:#111827">Next best actions'
            f' <span style="font-weight:500;color:#6b7280">· {rs.get("recommendations")} recommendations worth {e(_money(rs.get("value_at_stake")))}, ranked by what can still be saved</span></div>'
            f'<div style="margin:8px 0 10px">{chips}</div>'
            # In the app the list scrolls inside the report; an email client ignores the
            # overflow, which is why the email carries a capped list instead.
            + (
                '<div style="max-height:560px;overflow:auto;border:1px solid #f3f4f6;border-radius:8px">'
                if channel != "email"
                else "<div>"
            )
            + ("" if inline else _NBA_STYLE)
            + '<table class="nb" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse"><thead><tr>'
            + f"<th{_nba_attr('th', inline)}></th><th{_nba_attr('th', inline)}>Quote · account</th>"
            + f"<th{_nba_attr('thc', inline)}>Risk · at stake</th><th{_nba_attr('th', inline)}>Play · why · owner</th>"
            + f"</tr></thead><tbody>{rows}</tbody></table></div>{more}{alert_line}</div>"
        )

    segments = risk.get("summary", {}).get("rfm_segments") or {}
    seg_chips = "".join(
        f'<span style="display:inline-block;margin:2px 6px 2px 0;padding:3px 10px;border-radius:999px;background:#f3f4f6;color:#374151;font:600 11px {_FONT}">{e(str(k))} · {v}</span>'
        for k, v in sorted(segments.items(), key=lambda kv: -kv[1])
    )
    auc_pct = f"{float(model.get('auc') or 0) * 100:.0f}%"
    honesty = (
        f'<p style="font:11px {_FONT};color:#6b7280;margin:8px 0 0">Risk = the chance a quote does not close within its category\'s usual time (the 90th percentile). '
        f"Model trained on {model.get('closed_quotes')} closed quotes at this run; on the newest {model.get('holdout_rows')} held-out rows it ranks correctly {e(auc_pct)} of the time (AUC {model.get('auc')}). "
        + (
            f" Tracked so far: {e(_precision_words(outcomes))}."
            if _precision_words(outcomes)
            else ""
        )
        + f" Accounts by RFM over the trailing year: {seg_chips or '—'}</p>"
    )
    return (
        '<div style="margin:12px 0 4px">'
        f'<div style="font:600 11px {_FONT};color:#6d28d9;text-transform:uppercase;letter-spacing:.06em;margin:0 0 4px">Pipeline at risk · as of {e(str(risk.get("as_of")))}</div>'
        f"{hero}{ranked}{honesty}</div>"
    )


async def render_report(step_input: StepInput) -> StepOutput:
    """The report as HTML and text from the comparison and the (grounded) explanation.
    Reads the ``title`` slot (default: the use case's question) and ``dashboard_url``."""
    from egpt_retail.executors.diagnostics import diagnosis_of
    from egpt_retail.executors.predict import risk_of
    from egpt_retail.executors.recommend import outcomes_of, recommendations_of

    options = _options(step_input)
    comparison = comparison_of(step_input)
    diagnosis = diagnosis_of(step_input)
    risk = risk_of(step_input)
    recommendation_result = recommendations_of(step_input)
    outcomes = outcomes_of(step_input)
    if comparison is None and diagnosis is None and risk is None:
        return StepOutput(
            content={"refused": "no comparison to render"}, success=False, stop=True
        )
    comparison = comparison or {
        "as_of": (diagnosis or risk or {}).get("headline", {}).get("as_of")
        if diagnosis
        else (risk or {}).get("as_of"),
        "kpis": [],
        "comparison": NO_COMPARISON,
    }
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
        diagnosis=diagnosis,
        risk=risk,
        recommendation_result=recommendation_result,
        outcomes=outcomes,
    )
    # The email is the same report as the app's; only when a run's list would take the
    # message past the client's clip limit does the email carry the top of it and link
    # to the rest.
    email_body = html_body
    if risk:
        email_body = report_html(
            comparison,
            explanation,
            title=title,
            use_case=str(options.get("use_case_title") or ""),
            dashboard_url=str(options.get("dashboard_url") or ""),
            grounding=grounding,
            diagnosis=diagnosis,
            risk=risk,
            recommendation_result=recommendation_result,
            outcomes=outcomes,
            channel="email",
        )
        if len(email_body.encode("utf-8")) > EMAIL_LIMIT_BYTES:
            email_body = report_html(
                comparison,
                explanation,
                title=title,
                use_case=str(options.get("use_case_title") or ""),
                dashboard_url=str(options.get("dashboard_url") or ""),
                grounding=grounding,
                diagnosis=diagnosis,
                risk=risk,
                recommendation_result=recommendation_result,
                outcomes=outcomes,
                channel="email_short",
            )
    text_body = report_text(
        comparison,
        explanation,
        title=title,
        diagnosis=diagnosis,
        risk=risk,
        recommendation_result=recommendation_result,
    )
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
                "email_html": email_body,
                "text": text_body,
                "as_of": comparison.get("as_of"),
            },
            "dashboard_url": str(options.get("dashboard_url") or ""),
            **({"diagnosis": diagnosis} if diagnosis is not None else {}),
            **({"risk": risk} if risk is not None else {}),
            **(
                {"recommendation_result": recommendation_result}
                if recommendation_result is not None
                else {}
            ),
            **({"outcomes": outcomes} if outcomes is not None else {}),
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


def _report_filename(title: str, as_of: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "report"
    return f"{slug}-{as_of}.html" if as_of else f"{slug}.html"


def _standalone_report(body: str, title: str) -> str:
    """The report as a complete HTML document a browser opens on its own."""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title>"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '</head><body style="margin:0;background:#f9fafb">' + body + "</body></html>"
    )


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
            "diagnosis",
            "risk",
            "recommendation_result",
            "outcomes",
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
            html=str(report.get("email_html") or report.get("html") or ""),
            # The full report rides along as a file -- the same document the app shows --
            # so a mail client's clip limit never decides what the reader gets.
            attachments=[
                (
                    _report_filename(
                        str(report.get("title") or "report"),
                        str(report.get("as_of") or ""),
                    ),
                    _standalone_report(
                        str(report.get("html") or ""),
                        str(report.get("title") or "Report"),
                    ).encode("utf-8"),
                    "text/html",
                )
            ]
            if report.get("html")
            else None,
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
