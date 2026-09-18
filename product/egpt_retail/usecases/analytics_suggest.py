"""The Analytics Automation form's suggestion from the ask (K23): what the connected data
supports, chosen for a question.

A person types "Full visibility into the quote pipeline by brand, territory and state,
every Monday to the sales team" or "why is my revenue low last week?" and the form should
fill itself with what the data can actually answer: the KPIs from the registry that match
the ask and have gold behind them, the breakdowns those KPIs offer, the baseline, the
cadence, the outputs and the recipients the words name. Deterministic -- a lexical match
over the governed registry and the gold the deployment holds -- so the same ask on the same
registry always suggests the same form, and every suggestion says why it was made. A phrase
nothing in the registry covers is returned as such, with the pointer to the KPI screen's
"Suggest KPIs" run, which is where a model proposes a metric from the data itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

__all__ = ["AnalyticsSuggestion", "suggest_analytics", "tokens"]

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "by",
    "with",
    "is",
    "are",
    "my",
    "our",
    "we",
    "i",
    "it",
    "this",
    "that",
    "from",
    "as",
    "at",
    "be",
    "every",
    "each",
    "all",
    "full",
    "into",
    "up",
    "out",
    "through",
    "vs",
    "versus",
    "last",
    "week",
    "month",
    "day",
    "year",
    "please",
    "give",
    "me",
    "show",
    "send",
    "report",
    "dashboard",
    "team",
    "low",
    "high",
    "why",
    "what",
    "how",
    "when",
    "who",
    "which",
    "was",
    "were",
    "has",
    "have",
    "not",
    "no",
    "than",
    "more",
    "less",
    "over",
    "under",
    "per",
    "visibility",
    "being",
    "broken",
    "total",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "weekly",
    "monthly",
    "daily",
    "morning",
    "sales",
    "performance",
    "breakdown",
    "analysis",
}
_SYNONYMS: Mapping[str, Tuple[str, ...]] = {
    "quotes": ("quote", "rfq", "quotation"),
    "quote": ("quotes", "rfq"),
    "rfq": ("quote", "quotes"),
    "pipeline": ("pipeline", "stage", "funnel"),
    "win": ("won", "closed"),
    "won": ("win", "closed"),
    "lost": ("loss", "lose"),
    "revenue": ("sales", "value", "amount"),
    "sales": ("revenue",),
    "rep": ("representative", "territory"),
    "reps": ("representative", "territory"),
    "category": ("categories", "pim"),
    "categories": ("category", "pim"),
    "conversion": ("rate", "converted"),
    "chat": ("inquiry", "inquiries", "live"),
    "inquiries": ("inquiry", "chat"),
    "close": ("closed", "days"),
    "turnaround": ("days", "close"),
}
_GRAIN_CUES: Mapping[str, Tuple[str, ...]] = {
    "brand": ("brand", "brands"),
    "product_category": ("category", "categories", "pim"),
    "sales_territory": ("territory", "territories", "rep", "reps"),
    "location": ("state", "states", "region", "location", "city"),
    "customer_segment": ("segment", "segments"),
    "lost_reason": ("reason", "reasons", "lost"),
    "pipeline_stage": ("stage", "stages", "pipeline"),
    "channel": ("channel", "channels"),
    "time_period": ("weekly", "week", "trend", "over time", "monthly"),
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@dataclass
class AnalyticsSuggestion:
    kind: str  # monitoring | diagnostic
    title: str
    business_areas: List[str] = field(default_factory=list)
    kpis: List[Dict[str, Any]] = field(default_factory=list)
    dimensions: List[str] = field(default_factory=list)
    comparison: Optional[str] = None
    comparison_reason: str = ""
    schedule: str = "weekly"
    schedule_reason: str = ""
    window: Optional[int] = None
    outputs: List[str] = field(default_factory=list)
    recipients: List[str] = field(default_factory=list)
    uncovered: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "business_areas": self.business_areas,
            "kpis": self.kpis,
            "dimensions": self.dimensions,
            "comparison": self.comparison,
            "comparison_reason": self.comparison_reason,
            "schedule": self.schedule,
            "schedule_reason": self.schedule_reason,
            "window": self.window,
            "outputs": self.outputs,
            "recipients": self.recipients,
            "uncovered": self.uncovered,
            "notes": self.notes,
        }


def tokens(text: str) -> List[str]:
    return [
        w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1
    ]


def _expand(words: Sequence[str]) -> Set[str]:
    out: Set[str] = set(words)
    for word in words:
        out.update(_SYNONYMS.get(word, ()))
        if word.endswith("s") and len(word) > 3:
            out.add(word[:-1])
        else:
            out.add(word + "s")
    return out


def _kpi_words(kpi: Mapping[str, Any]) -> Set[str]:
    text = " ".join(
        str(kpi.get(k) or "")
        for k in (
            "identifier",
            "name",
            "description",
            "subject_area",
            "concept",
            "grain",
        )
    )
    return _expand(tokens(text.replace("__", " ").replace("_", " ")))


#: Words that describe the shape of a metric, not its subject: matching on these alone
#: ("rate", "count") would pull in every rate in the registry for an ask about one.
_GENERIC = {
    "rate",
    "rates",
    "share",
    "shares",
    "count",
    "counts",
    "total",
    "totals",
    "average",
    "averages",
    "number",
    "numbers",
    "value",
    "values",
    "by",
    "per",
    "ratio",
    "ratios",
}


def _score(
    ask: Set[str], plain: Sequence[str], kpi: Mapping[str, Any]
) -> Tuple[int, List[str]]:
    """The match score and, for the person, the ask's own words that matched. A KPI
    scores only when a subject word matched; generic words add to the score after that."""
    words = _kpi_words(kpi)
    name_words = _expand(tokens(str(kpi.get("name") or "").replace("_", " ")))
    hits = ask & words
    subject_hits = hits - _GENERIC
    if not subject_hits:
        return 0, []
    name_hits = ask & name_words
    shown = [w for w in plain if w in hits or any(x in hits for x in _expand([w]))]
    return len(hits) + 2 * len(name_hits), list(dict.fromkeys(shown))


def suggest_analytics(
    question: str,
    registry: Sequence[Mapping[str, Any]],
    *,
    gold_latest: Optional[Mapping[str, Optional[date]]] = None,
    default_recipient: str = "",
) -> AnalyticsSuggestion:
    """The form's suggestion for ``question`` over ``registry`` (rows with identifier, name,
    description, business_area, grain, concept, subject_area, active) and ``gold_latest``
    (identifier -> latest business date in gold, None when never computed)."""
    text = (question or "").strip()
    recipients = [m.rstrip(".,;:") for m in _EMAIL.findall(text)]
    wordy = _EMAIL.sub(" ", text)
    low = wordy.lower()
    plain = tokens(wordy)
    ask = _expand(plain)
    gold_latest = gold_latest or {}

    kind = (
        "diagnostic"
        if re.match(r"^\s*(why|what happened|what caused|explain why)", low)
        or re.search(r"\b(drop|dropped|fell|falling|low|declin|down)\b", low)
        else "monitoring"
    )
    first_sentence = re.split(r"[.\n]", text, maxsplit=1)[0].strip()
    title = (first_sentence[:1].upper() + first_sentence[1:])[:90] or "Report"

    # Grains the ask names, in its own words.
    wanted_grains: List[str] = []
    for grain, cues in _GRAIN_CUES.items():
        if any(cue in low for cue in cues):
            wanted_grains.append(grain)

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for kpi in registry:
        if kpi.get("active") is False:
            continue
        score, hits = _score(ask, plain, kpi)
        grain = str(kpi.get("grain") or "overall")
        if grain in wanted_grains:
            score += 2
            hits = hits + [f"by {grain.replace('_', ' ')}"]
        elif grain not in {"overall", "time_period"} and score > 0:
            # A breakdown the ask did not name ("quotes by state" for an ask about
            # quotes) steps back behind the overall KPI; the person can still add it.
            score //= 2
        if score <= 0:
            continue
        latest = gold_latest.get(str(kpi.get("identifier")))
        scored.append(
            (
                score,
                {
                    "identifier": kpi.get("identifier"),
                    "name": kpi.get("name"),
                    "grain": grain,
                    "business_area": kpi.get("business_area"),
                    "unit": kpi.get("unit") or "",
                    "score": score,
                    "why": ", ".join(dict.fromkeys(hits)),
                    "feasibility": "ready" if latest else "buildable",
                    "latest_as_of": latest.isoformat() if latest else None,
                },
            )
        )
    scored.sort(key=lambda pair: (-pair[0], str(pair[1]["identifier"])))
    # The ask's own breadth decides how many: a KPI that matched on a stray word or two
    # while another matched on six is not what the person asked for. Kept: those within
    # reach of the best match; a broad monitoring ask keeps more than a pointed question.
    best = scored[0][0] if scored else 0
    floor = max(2, int(best * (0.45 if kind == "monitoring" else 0.6)))
    chosen = [k for score, k in scored if score >= floor][
        : 16 if kind == "monitoring" else 6
    ]
    if not chosen and scored:
        chosen = [k for _, k in scored[:4]]

    areas = sorted(
        {str(k.get("business_area")) for k in chosen if k.get("business_area")}
    )
    # The breakdowns: the grains the ask names, when some registered KPI carries them
    # (so the data supports them), plus the grains of the KPIs chosen.
    supported = {str(k.get("grain") or "overall") for k in registry}
    dimensions = sorted(
        ({g for g in wanted_grains if g in supported} | {k["grain"] for k in chosen})
        - {"overall", "time_period"}
    )

    # A comparison only when the words ask for one: naming a period, a change, or a
    # question about a change. A monitoring ask without those reports the numbers as
    # they are, and the form offers "compare with" as a choice.
    comparison: Optional[str]
    if re.search(r"last year|year over year|yoy|same period", low):
        comparison, comparison_reason = "prior_year", "the ask says last year"
    elif re.search(r"plan|target|budget", low):
        comparison, comparison_reason = (
            "prior_period",
            "plan/target is not connected; the period before is the baseline",
        )
    elif kind == "diagnostic" or re.search(
        r"\b(compare|compared|vs\.?|versus|change[ds]?|dropp?(ed|ing)?|fell|fall|"
        r"rose|rise|up|down|trend|movement|moved|delta|growth|decline|previous|prior|"
        r"before|week over week|month over month|wow|mom)\b",
        low,
    ):
        comparison, comparison_reason = (
            "prior_period",
            "the ask is about a change, so the period before is the baseline"
            if kind == "diagnostic"
            else "the ask compares, so the period before is the baseline",
        )
    else:
        comparison, comparison_reason = None, "nothing compared; the ask does not"

    if re.search(r"\b(weekly|every week|every monday|each week|on mondays?)\b", low):
        schedule, schedule_reason = "weekly", "the ask says weekly"
    elif re.search(r"\b(daily|every day|each day|every morning)\b", low):
        schedule, schedule_reason = "daily", "the ask says daily"
    elif re.search(r"\b(monthly|every month|each month|per month)\b", low):
        schedule, schedule_reason = "monthly", "the ask says monthly"
    elif kind == "diagnostic":
        schedule, schedule_reason = "weekly", "run once now; weekly if kept"
    else:
        schedule, schedule_reason = (
            "weekly",
            "weekly, the default for a monitoring report",
        )

    # The window the numbers cover, in days, when the ask names a period; blank means
    # each KPI's own declared window.
    window: Optional[int] = None
    if re.search(r"\b(last|past|previous|this) (week|7 days)\b|\bweekly\b", low):
        window = 7
    elif re.search(r"\b(last|past|previous|this) (month|30 days)\b|\bmonthly\b", low):
        window = 30
    elif re.search(r"\b(last|past|previous|this) quarter\b|\bquarterly\b", low):
        window = 90
    elif re.search(r"\b(yesterday|last 24 hours|today)\b|\bdaily\b", low):
        window = 1

    outputs: List[str] = []
    if re.search(r"dashboard|visibility|live view", low):
        outputs.append("dashboard")
    if re.search(r"report|email|mail|send|digest|summary", low) or kind == "diagnostic":
        outputs.append("report")
    if re.search(r"alert|notify (me|us) when|threshold|crosses", low):
        outputs.append("alert")
    if not outputs:
        outputs = ["report", "dashboard"] if kind == "monitoring" else ["report"]
    if "report" not in outputs:
        outputs.append("report")

    if not recipients and default_recipient:
        recipients = [default_recipient]

    covered = set()
    for kpi in chosen:
        covered |= _kpi_words(kpi)
    uncovered = [
        w
        for w in dict.fromkeys(plain)
        if w not in covered
        and not any(w in cues for cues in _GRAIN_CUES.values())
        and w not in {"salesforce", "hubspot", "middleby"}
    ]

    notes: List[str] = []
    if any(k["feasibility"] != "ready" for k in chosen):
        notes.append(
            "Some KPIs have no gold row yet; the first run computes them, or compute them on the KPI screen."
        )
    if uncovered:
        notes.append(
            "Words no registered KPI covers: "
            + ", ".join(uncovered[:8])
            + ". Use 'Suggest KPIs' on the KPI screen to propose one from the data."
        )
    return AnalyticsSuggestion(
        kind=kind,
        title=title,
        business_areas=areas,
        kpis=chosen,
        dimensions=dimensions,
        comparison=comparison,
        comparison_reason=comparison_reason,
        schedule=schedule,
        schedule_reason=schedule_reason,
        window=window,
        outputs=outputs,
        recipients=recipients,
        uncovered=uncovered,
        notes=notes,
    )
