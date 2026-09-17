"""The dashboard composer agent's output, and the executor that stamps it real.

The agent composes a dashboard for a purpose written in the caller's own words. It is shown
the metrics the caller selected -- their definitions, SQL included -- and, behind them, the
semantic layer's tables: every column with its type and the description the schema author
wrote or the meaning review accepted, and the join edges between the tables. From that it
writes the charts the purpose needs, which is not the same as one chart per selected metric:
a metric's own SQL where it already answers part of the purpose (``source: kpi``, read from
gold exactly as R13's tiles are), and a query composed across the tables where it does not
(``source: query``). Contract 1.2.0; before it this agent could only pick identifiers from a
closed list.

**Trust nothing the model said about a KPI beyond its identifier, and nothing about a query
until the guard has.** A ``kpi`` chart's identifier must be in the ready set the prompt was
rendered from, the same set ``POST /dashboards`` requires (``_check_tiles_ready``); the name
is copied from the registry. A ``query`` chart's SQL goes through the caller's ``check`` --
in production ``dashboards/queries.py``'s :func:`check_query`, the warehouse read guard plus
scope, personal-data and row-cap rules -- and then, when it passed, the caller's ``preview``
for a bounded run with real rows. What the guard did is carried on the chart as a verdict:
``ok``, ``rewritten`` (with what changed) or ``refused`` (with why), and a refused chart is
*kept* as a stub rather than dropped, so the person reviewing can fix the SQL in the editor
instead of losing the idea. Only a chart that cannot be represented at all -- an unknown KPI,
a ``query`` chart with no query -- is dropped, and reported in :class:`AssemblyReport`.

**Personal data never reaches the prompt.** :func:`render_prompt` omits every column the
semantic layer marks ``is_personal_data`` from the table listing, and
:meth:`SuggestionContext.summary` names them as withheld so the review screen can say so.
The guard would drop them from a projection anyway; not showing them is cheaper than
correcting for them.

**Drift with the API contract is impossible by construction here**, the same reason K12
returns ``egpt_retail.api.models.KpiSuggestion``: :func:`assemble` returns the generated
``DashboardSuggestion``, so a field the draft carries and the screen does not know is an
error at construction instead of a silent omission.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
)

from pydantic import BaseModel, ConfigDict, Field

from egpt_retail.api import models as wire
from egpt_retail.contracts.kpi import KpiDefinition, referenced_tables
from egpt_retail.contracts.semantic import JoinEdge, Table
from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set

__all__ = [
    "DEFAULT_CHART_BUDGET",
    "MAX_CHART_BUDGET",
    "PROMPT_PATH",
    "AssemblyReport",
    "AxisDraft",
    "ChartQueryDraft",
    "DashboardChartDraft",
    "DashboardSuggestionDraft",
    "QueryCheck",
    "QueryPreviewer",
    "QueryVerdictLike",
    "SuggestionContext",
    "aassemble",
    "arender_prompt",
    "assemble",
    "render_prompt",
]

PROMPT_PATH = Path(__file__).parent / "dashboard_suggestion.md"

#: What the agent is told to aim for when the caller set no budget, and the hard ceiling.
DEFAULT_CHART_BUDGET = 6
MAX_CHART_BUDGET = 12


# --- the draft ------------------------------------------------------------------------------


class AxisDraft(BaseModel):
    """One result column of a composed query, and how the chart uses it."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(description="A column name the query's SELECT list produces.")
    kind: Literal["time", "category"] = Field(
        default="category",
        description="For the x axis: `time` plots a series, `category` labels bars or rows.",
    )
    label: str = Field(default="", description="Axis label, in plain words.")
    unit: str = Field(
        default="", description="Unit of the y column: %, currency, count, days, ..."
    )


class ChartQueryDraft(BaseModel):
    """SQL the agent composed, plus which result columns the chart plots."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(
        description=(
            "One Postgres SELECT over the tables in section 3 only, ending in LIMIT. "
            "Never SELECT *."
        )
    )
    x: Optional[AxisDraft] = Field(
        default=None, description="The x axis column. Null for a single-number chart."
    )
    y: List[AxisDraft] = Field(
        default_factory=list, description="The value column(s). One for most charts."
    )
    series_by: Optional[str] = Field(
        default=None,
        description="A column that splits a line chart into one series per value, or null.",
    )


class DashboardChartDraft(BaseModel):
    """One chart, as the agent proposes it."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Short, specific, the way a person would title it.")
    chart: Literal["number", "line", "bar", "table"]
    source: Literal["kpi", "query"] = Field(
        description=(
            "`kpi` reuses a metric from section 3 as-is (set kpi_identifier); `query` "
            "carries SQL you composed (set query)."
        )
    )
    kpi_identifier: Optional[str] = Field(
        default=None,
        description="For source=kpi: an identifier copied verbatim from section 3.",
    )
    grain_value: Optional[str] = Field(
        default=None,
        description=(
            "For source=kpi at a non-overall grain: which row to show, or null for the "
            "listing."
        ),
    )
    query: Optional[ChartQueryDraft] = Field(
        default=None, description="For source=query: the composed query."
    )
    description: str = Field(
        description=(
            "One or two plain sentences under the chart: what the numbers mean and any "
            "caveat. Written for the person reading the dashboard."
        )
    )
    rationale: str = Field(
        description="One sentence: why this chart serves the stated purpose."
    )


class DashboardSuggestionDraft(BaseModel):
    """The whole response, with the refusal channel section 8 requires."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="A short dashboard name that reflects the purpose.")
    charts: List[DashboardChartDraft] = Field(default_factory=list)
    business_area: Optional[str] = Field(
        default=None,
        description=(
            "A term from the business_area vocabulary in section 3, to scope the dashboard "
            "to -- only when the purpose names or clearly implies one area. Null otherwise."
        ),
    )
    notes: str = Field(
        default="",
        description="Empty on a clean run; otherwise what was missing or why the list is short.",
    )


# --- the context ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SuggestionContext:
    """What one run shows the agent: the selected metrics, the tables behind them (plus any
    the caller widened to), and the join edges among those tables. Built by the router from
    the registry and the semantic reader; rendered into the prompt by :func:`render_prompt`;
    summarised onto the result for the review screen by :meth:`summary`."""

    kpis: Tuple[KpiDefinition, ...] = ()
    tables: Tuple[Table, ...] = ()
    joins: Tuple[JoinEdge, ...] = ()
    chart_budget: Optional[int] = None

    @property
    def table_names(self) -> FrozenSet[str]:
        return frozenset(t.ref.name.lower() for t in self.tables)

    @property
    def budget(self) -> int:
        if self.chart_budget is None:
            return DEFAULT_CHART_BUDGET
        return max(1, min(MAX_CHART_BUDGET, self.chart_budget))

    def summary(self) -> wire.SuggestionContextSummary:
        described = 0
        total = 0
        withheld: List[str] = []
        for table in self.tables:
            for column in table.columns:
                if column.is_personal_data:
                    withheld.append(f"{table.ref.name}.{column.name}")
                    continue
                total += 1
                if column.description.strip() or column.meaning.strip():
                    described += 1
        return wire.SuggestionContextSummary(
            kpi_identifiers=sorted(k.identifier for k in self.kpis),
            table_names=sorted(self.table_names),
            described_columns=described,
            total_columns=total,
            withheld_columns=sorted(withheld),
        )


# --- the executor ---------------------------------------------------------------------------


class QueryVerdictLike:
    """What ``check`` returns -- duck-typed to ``dashboards.queries.QueryVerdict`` so this
    skill module never imports the warehouse side."""

    status: str
    detail: str
    sql: str
    tables: Tuple[str, ...]


QueryCheck = Callable[[str, FrozenSet[str]], QueryVerdictLike]
QueryPreviewer = Callable[[str], wire.QueryPreview]


class AssemblyReport(BaseModel):
    """What :func:`assemble` accepted, and what it dropped and why. Dropped charts are
    reported rather than raised on -- one hallucinated identifier out of five should cost the
    caller that one chart, not the other four (the same rule K12's report follows)."""

    suggestion: wire.DashboardSuggestion
    dropped: List[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _UncheckedVerdict:
    """The verdict :func:`assemble` uses when no ``check`` is supplied: the statement is
    taken as-is and its tables read by the same scan ``KpiDefinition`` uses. For a unit test
    or a fixture-only cookbook, never for a real run."""

    sql: str
    status: str = "ok"
    detail: str = ""

    @property
    def tables(self) -> Tuple[str, ...]:
        return tuple(sorted(referenced_tables(self.sql)))


def _axis_out(axis: AxisDraft) -> wire.ChartAxis:
    return wire.ChartAxis(
        column=axis.column,
        kind=wire.AxisKind(axis.kind),
        label=axis.label,
        unit=axis.unit,
    )


def _reconcile_axes(
    query: ChartQueryDraft, columns: Sequence[str]
) -> Tuple[Optional[AxisDraft], List[AxisDraft], Optional[str], List[str]]:
    """Keep only the axes the query's result actually has columns for -- a preview said
    what those are. An axis naming a column the result lacks would render nothing, so it is
    dropped and noted; when every y axis is gone, the last non-x column stands in."""
    if not columns:
        early_notes = (
            [
                f"series_by {query.series_by!r} was proposed but multi-series charts aren't "
                "rendered yet; showing a single series instead."
            ]
            if query.series_by is not None
            else []
        )
        return query.x, list(query.y), None, early_notes
    have = {c.lower() for c in columns}
    notes: List[str] = []
    x = query.x if query.x is None or query.x.column.lower() in have else None
    if query.x is not None and x is None:
        notes.append(f"x axis {query.x.column!r} is not a result column")
    y = [axis for axis in query.y if axis.column.lower() in have]
    for axis in query.y:
        if axis.column.lower() not in have:
            notes.append(f"y axis {axis.column!r} is not a result column")
    if not y:
        candidates = [c for c in columns if x is None or c.lower() != x.column.lower()]
        if candidates:
            y = [AxisDraft(column=candidates[-1])]
    # Multi-series charts aren't rendered on either path yet (`dashboards/data.py`'s
    # `query_tile_data`, `storage/dashboards.js`'s `tileDataFromPreview` -- round-1 review,
    # finding #13): forced to null here rather than shipping a chart whose axis metadata
    # promises a split the render side can't produce, which would otherwise render as a flat,
    # unsplit series with no indication anything was dropped.
    series_by = None
    if query.series_by is not None:
        notes.append(
            f"series_by {query.series_by!r} was proposed but multi-series charts aren't "
            "rendered yet; showing a single series instead."
        )
    return x, y, series_by, notes


def _query_out(
    query: ChartQueryDraft, sql: str, tables: Sequence[str], columns: Sequence[str]
) -> Tuple[wire.TileQuery, List[str]]:
    x, y, series_by, notes = _reconcile_axes(query, columns)
    return (
        wire.TileQuery(
            sql=sql,
            dialect="postgres",
            source_tables=sorted(set(tables)),
            x=_axis_out(x) if x is not None else None,
            y=[_axis_out(axis) for axis in y],
            series_by=series_by,
        ),
        notes,
    )


def assemble(
    response: DashboardSuggestionDraft,
    *,
    ready_kpis: Sequence[KpiDefinition],
    context: Optional[SuggestionContext] = None,
    check: Optional[QueryCheck] = None,
    preview: Optional[QueryPreviewer] = None,
) -> AssemblyReport:
    """Turn a draft into the contract's ``DashboardSuggestion``.

    ``ready_kpis`` must already be the ``ready`` subset -- the same set the prompt was rendered
    from. ``context`` is what the prompt showed (its table names bound the scope check, its
    summary rides on the result). ``check`` runs a composed statement through the guard and
    returns a verdict; ``preview`` runs a passed statement for a few rows. Both are optional
    so this stays unit-testable without a warehouse; a real run always supplies both.
    """
    context = context or SuggestionContext(kpis=tuple(ready_kpis))
    # Scoped to `context.kpis` -- the subset the prompt actually rendered (`_render_kpis`) --
    # not the wider `ready_kpis` argument. A `kpi`-sourced chart may only reuse a metric the
    # model was shown; validating against the full registry instead would let a chart name any
    # ready KPI anywhere in the deployment, never shown to the model and never selected by the
    # caller, and still pass. `ready_kpis` itself is unused below now that `context` carries
    # the narrower set every caller (including the `context=None` fallback above) resolves to.
    by_identifier = {kpi.identifier: kpi for kpi in context.kpis}

    charts: List[wire.DashboardChartSuggestion] = []
    dropped: List[str] = []
    seen: set = set()
    extra_notes: List[str] = []
    key: Tuple[Any, ...]
    over_budget = max(0, len(response.charts) - context.budget)
    drafts = response.charts[: context.budget]

    for draft in drafts:
        title = draft.title.strip() or "Untitled chart"
        chart_kind = wire.ChartKind(draft.chart)

        if draft.source == "kpi":
            identifier = (draft.kpi_identifier or "").strip()
            definition = by_identifier.get(identifier)
            if definition is None:
                dropped.append(
                    f"{title!r}: {identifier or '<none>'!r} is not among the metrics "
                    "selected for this run"
                )
                continue
            key = ("kpi", identifier, draft.chart, draft.grain_value)
            if key in seen:
                dropped.append(
                    f"{title!r}: {identifier} proposed twice as the same chart"
                )
                continue
            seen.add(key)
            charts.append(
                wire.DashboardChartSuggestion(
                    title=title,
                    chart=chart_kind,
                    source=wire.TileSource.kpi,
                    kpi_identifier=identifier,
                    grain_value=draft.grain_value,
                    query=None,
                    description=draft.description.strip(),
                    rationale=draft.rationale.strip(),
                    guard=wire.QueryGuardVerdict(
                        status=wire.QueryGuardStatus.ok,
                        detail="",
                        tables=sorted(definition.source_tables),
                    ),
                    preview=None,
                )
            )
            continue

        # source == "query"
        if draft.query is None or not draft.query.sql.strip():
            dropped.append(f"{title!r}: a query chart with no query")
            continue
        sql = draft.query.sql.strip()
        key = ("query", sql)
        if key in seen:
            dropped.append(f"{title!r}: the same query proposed twice")
            continue
        seen.add(key)

        verdict: Any = (
            check(sql, context.table_names)
            if check is not None
            else _UncheckedVerdict(sql)
        )
        guard = wire.QueryGuardVerdict(
            status=wire.QueryGuardStatus(verdict.status),
            detail=verdict.detail,
            tables=sorted(verdict.tables),
        )
        query_out: Optional[wire.TileQuery]
        preview_out: Optional[wire.QueryPreview] = None
        if verdict.status == "refused":
            # Kept as a stub, so the SQL can be fixed in the editor. The original text
            # travels with it; there is nothing else to derive source_tables from.
            query_out, _ = _query_out(draft.query, sql, verdict.tables, ())
        else:
            if preview is not None:
                preview_out = preview(verdict.sql)
                if preview_out.error:
                    guard = wire.QueryGuardVerdict(
                        status=wire.QueryGuardStatus.refused,
                        detail=f"The warehouse refused it: {preview_out.error}",
                        tables=sorted(verdict.tables),
                    )
            columns = preview_out.columns if preview_out is not None else ()
            query_out, axis_notes = _query_out(
                draft.query, verdict.sql, verdict.tables, columns
            )
            extra_notes.extend(f"{title}: {note}" for note in axis_notes)

        charts.append(
            wire.DashboardChartSuggestion(
                title=title,
                chart=chart_kind,
                source=wire.TileSource.query,
                kpi_identifier=None,
                grain_value=None,
                query=query_out,
                description=draft.description.strip(),
                rationale=draft.rationale.strip(),
                guard=guard,
                preview=preview_out,
            )
        )

    if over_budget:
        extra_notes.append(
            f"the model proposed {len(response.charts)} charts, above the budget of "
            f"{context.budget}; the last {over_budget} were dropped without being checked "
            "or previewed"
        )

    # `filters` is now a chosen `(table, column)` (R13.1), which this agent has no basis to
    # pick on its own -- a person adds filter columns afterward in the dashboard builder, from
    # the candidates `GET /dashboards/{id}/filter-columns` derives off the charts above.
    # `response.business_area` (still part of the model's own output schema/prompt) no longer
    # becomes a stored filter; retiring the field itself means touching the prompt template
    # too and is out of scope here.
    notes = response.notes.strip()
    if extra_notes:
        notes = "\n".join(part for part in [notes, *extra_notes] if part)

    suggestion = wire.DashboardSuggestion(
        name=response.name.strip() or "Untitled dashboard",
        charts=charts,
        filters=[],
        notes=notes,
        context=context.summary(),
    )
    return AssemblyReport(suggestion=suggestion, dropped=dropped)


async def aassemble(
    response: DashboardSuggestionDraft,
    *,
    ready_kpis: Sequence[KpiDefinition],
    context: Optional[SuggestionContext] = None,
    check: Optional[QueryCheck] = None,
    preview: Optional[QueryPreviewer] = None,
) -> AssemblyReport:
    """Async variant. ``check``/``preview`` may do I/O, so the whole pass runs on a worker
    thread rather than blocking the loop through them."""
    return await asyncio.to_thread(
        assemble,
        response,
        ready_kpis=ready_kpis,
        context=context,
        check=check,
        preview=preview,
    )


# --- the prompt -----------------------------------------------------------------------------


def _render_kpis(kpis: Sequence[KpiDefinition]) -> str:
    """One block per selected metric: identifier, name, area, grain, unit, description and
    the formula itself -- the agent may reuse the formula as-is (``source: kpi``) or read it
    to learn how a table is meant to be queried."""
    if not kpis:
        return (
            "(no metrics selected -- compose every chart from the tables in section 3)"
        )
    blocks = []
    for kpi in sorted(kpis, key=lambda k: k.identifier):
        head = f"- `{kpi.identifier}` — {kpi.name} ({kpi.business_area}, grain: {kpi.grain}"
        if kpi.unit:
            head += f", unit: {kpi.unit}"
        head += ")"
        lines = [head]
        if kpi.description.strip():
            lines.append(f"  {kpi.description.strip()}")
        lines.append(f"  tables: {', '.join(sorted(kpi.source_tables))}")
        lines.append(f"  sql: {' '.join(kpi.sql.split())}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _render_tables(tables: Sequence[Table]) -> str:
    """One block per table: its description, then every non-personal column as
    ``name · type · text``, where the text is the reviewed meaning when there is one and the
    schema author's description otherwise. Personal-data columns are omitted, not marked."""
    if not tables:
        return "(no tables -- propose no query charts and say so in notes)"
    blocks = []
    for table in sorted(tables, key=lambda t: t.ref.name):
        head = f"{table.ref.qualified}"
        if table.description.strip():
            head += f" — {table.description.strip()}"
        if table.ref.row_count is not None:
            head += f" ({table.ref.row_count:,} rows)"
        lines = [head]
        for column in table.columns:
            if column.is_personal_data:
                continue
            text = column.meaning.strip() or column.description.strip()
            lines.append(
                f"  {column.name} · {column.data_type} · "
                + (text if text else "(no description)")
            )
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _render_joins(joins: Sequence[JoinEdge]) -> str:
    if not joins:
        return "(no declared join paths among these tables -- join only on a clear key)"
    return "\n".join(
        f"- {edge.left_table} → {edge.right_table} ON {edge.on} ({edge.cardinality})"
        for edge in joins
    )


def render_prompt(
    *,
    context: SuggestionContext,
    vocabularies: Optional[VocabularySet] = None,
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """The prompt with the selected metrics, the tables behind them, their joins, the chart
    budget and the business_area vocabulary filled in. Every placeholder must be present in
    the template, or the model would silently be left to invent what it was never shown."""
    vocabularies = vocabularies or load_vocabulary_set()
    prompt = prompt_path.read_text()
    filled = {
        "kpis": _render_kpis(context.kpis),
        "tables": _render_tables(context.tables),
        "joins": _render_joins(context.joins),
        "chart_budget": str(context.budget),
        "business_area": vocabularies["business_area"].render_for_prompt(),
    }
    for name, rendered in filled.items():
        placeholder = "{{" + name + "}}"
        if placeholder not in prompt:
            raise ValueError(
                f"{prompt_path.name} has no {placeholder} placeholder, so {name} would never "
                "reach the model and it would invent values."
            )
        prompt = prompt.replace(placeholder, rendered)
    return prompt


async def arender_prompt(
    *,
    context: SuggestionContext,
    vocabularies: Optional[VocabularySet] = None,
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(
        render_prompt,
        context=context,
        vocabularies=vocabularies,
        prompt_path=prompt_path,
    )


def summarise_drafts(charts: Sequence[DashboardChartDraft]) -> Dict[str, int]:
    """How many charts of each source a draft carries -- what the run's ``drafting`` stage
    reports. Pure."""
    counts = {"kpi": 0, "query": 0}
    for chart in charts:
        counts[chart.source] = counts.get(chart.source, 0) + 1
    return counts
