"""The output schema for R11's playground agent (K14 template).

``AnswerBlock`` mirrors ``api/openapi/schemas/playground.yaml#AnswerBlock``: only ``id`` and
``kind`` are required there, and the rest is deliberately open ("the playground's answer
format owns the inner shape; the report only needs the id and kind to pin it") -- ``extra=
"allow"`` is what keeps this model accepting the same shape the wire contract does, rather
than silently dropping a field a future block kind adds.
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum
from pathlib import Path
from typing import Dict, List, Set

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AnswerBlockKind",
    "AnswerBlock",
    "PlaygroundAnswer",
    "closed_sets_match_prompt",
    "aclosed_sets_match_prompt",
]

PROMPT_PATH = Path(__file__).parent / "playground.md"


class AnswerBlockKind(str, Enum):
    TEXT = "text"
    FINDING = "finding"
    TABLE = "table"
    CHART = "chart"
    KPI = "kpi"
    RECOMMENDATION = "recommendation"


class BlockItem(BaseModel):
    """One entry in a block's ``items``: a stat tile, or a recommendation.

    Both shapes share one model because ``items`` is one field. A `finding`/`kpi` block fills
    ``title``/``value``/``desc``; a `recommendation` block fills ``text``/``priority``. Every
    field is optional, so neither kind carries the other's empties as anything but blanks.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    value: str = ""
    desc: str = ""
    text: str = ""
    priority: str = Field(
        default="", description="recommendation: high, medium or low."
    )


class ChartPoint(BaseModel):
    """One plotted point. Fixed to ``label``/``value`` rather than arbitrary keys, which is
    also why the block needs no x/y key names: there is only one shape to read."""

    model_config = ConfigDict(extra="forbid")

    label: str = ""
    value: float = 0.0


class TableColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = ""
    label: str = ""
    format: str = Field(
        default="",
        description="number, percent, currency, text or date. Blank means text.",
    )


class AnswerBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    # No `description=` here: an enum-typed field serialises to a `$ref`, and strict
    # structured-output schemas reject a `$ref` carrying sibling keywords (template, section 6).
    kind: AnswerBlockKind
    title: str = ""
    content: str = ""
    source: str = Field(
        default="",
        description="Which tool call, and result id where relevant, this block cites.",
    )

    # The structured payloads the renderer draws from. All optional: a block that carries only
    # `content` still renders as prose, which is what every response looked like before these
    # existed, so nothing regresses when the model omits them.
    #
    # Declared here rather than left to `extra="allow"`, even though the wire contract permits
    # extras: Anthropic's structured-output path sets `additionalProperties: false` on the
    # generated schema, so a field this model does not name is a field the model is *forbidden*
    # to emit. "The contract allows it" and "the model may produce it" are not the same claim.
    #
    # Every inner shape is a declared model, and that is load-bearing rather than tidiness.
    # `List[Dict[str, Any]]` looks like the flexible choice and is in fact an impossible one:
    # it generates `{"type": "object", "properties": {}}`, and the Anthropic path then forces
    # `additionalProperties: false` over the whole schema -- an object with no properties that
    # permits no others can only ever be `{}`. Measured, not theorised: with dicts here the
    # model returned `data: []` on every chart, and the malformed output that followed was put
    # back together by `utils/string.py`'s field-merging repair into duplicate ids and mangled
    # strings. Declared models are what make these fields fillable at all.
    items: List[BlockItem] = Field(
        default_factory=list,
        description="finding/kpi: stat tiles. recommendation: the suggested actions.",
    )
    data: List[ChartPoint] = Field(
        default_factory=list,
        description="chart: one point per category, label plus value.",
    )
    chart_kind: str = Field(
        default="",
        description="chart: bar, line, pie, funnel, spark or treemap. Honour what the user asked for.",
    )
    columns: List[TableColumn] = Field(
        default_factory=list, description="table: one per column, left to right."
    )
    # A matrix, not objects keyed by column: `additionalProperties: false` forbids the
    # arbitrary per-row keys objects would need, so a row is its cells in column order and the
    # renderer zips them back against `columns`.
    rows: List[List[str]] = Field(
        default_factory=list,
        description="table: one list of cell values per row, in the same order as columns.",
    )
    description: str = Field(
        default="", description="chart/table: one line of context under the title."
    )


class PlaygroundAnswer(BaseModel):
    """The whole response. ``notes`` carries the refusal, per section 7 of the template."""

    blocks: List[AnswerBlock] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description="Empty on a clean run. Otherwise what was missing or declined.",
    )
    follow_ups: List[str] = Field(
        default_factory=list,
        description=(
            "Three short questions this answer invites, each answerable by the same mode's "
            "tools. Empty when the answer refused -- offering next steps for a question that "
            "could not be answered invites the user to repeat a failure."
        ),
    )


def _enumerated_values(prompt: str, field_name: str) -> Set[str]:
    values: Set[str] = set()
    for line in prompt.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        named = cells[0].strip("`").split(".")[-1].rstrip("[]")
        if named != field_name:
            continue
        values.update(re.findall(r"`([a-z_]+)`", cells[-1]))
    return values


def closed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Whether every enum in this module is enumerated in full in the prompt."""
    prompt = prompt_path.read_text()
    declared = {member.value for member in AnswerBlockKind}
    return {"kind": _enumerated_values(prompt, "kind") == declared}


async def aclosed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(closed_sets_match_prompt, prompt_path)


class PlaygroundEnrichment(BaseModel):
    """A second, narrow ask run after the main answer, never in place of it.

    The main agent's instructions (this file's own ``playground.md``) ask for chart data,
    table rows, follow-ups and a recommendation inline, in the same call that reads the
    warehouse and writes the prose -- and against every prompt variant tried, that call
    reliably narrates the numbers in `content` and just as reliably leaves `data`/`rows`/
    `follow_ups` empty. A short, single-purpose instruction against a small schema fills them
    every time in isolation; the difference measured was the length and breadth of the
    instruction competing for the model's attention, not the schema or the model's ability.

    So this is that short instruction, asked separately: restructure the numbers the main
    answer already computed and stated, never compute a new one. It reads only the finished
    prose (``PlaygroundAnswer.blocks[].content``), which is why it cannot introduce a number
    that was not already in the grounded answer -- there is nothing else in its input.
    """

    model_config = ConfigDict(extra="forbid")

    chart_kind: str = Field(
        default="",
        description="line for a trend over time, pie for a share of one whole, bar otherwise "
        "-- unless the question names a type, which always wins.",
    )
    chart_data: List[ChartPoint] = Field(default_factory=list)
    table_columns: List[TableColumn] = Field(default_factory=list)
    table_rows: List[List[str]] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)
    recommendations: List[BlockItem] = Field(default_factory=list)


ENRICHMENT_PROMPT = """You restructure an answer that has already been computed and verified.
Every number below is already correct -- you compute nothing new, you only re-shape what is
already stated into the fields requested.

Read INPUT_TEXT. It is prose stating the final numbers for the QUESTION below.

- chart_data: one {label, value} point per category the text names, using its exact numbers.
  Never add a point whose number is not in the text.
- chart_kind: `line` if the text describes a trend over time (dates, weeks, months in
  sequence); `pie` if it describes a share or distribution of one whole; `bar` for a ranking
  or comparison of separate categories -- the default for most answers. If QUESTION names a
  chart type explicitly (bar, line, pie, funnel), use exactly that type instead of judging.
- table_columns / table_rows: the same numbers as a table. One row per category, cells in
  the same order as the columns.
- follow_ups: exactly three short questions a person would ask next, each a natural
  continuation of this specific answer -- not generic, not a restatement of it.
- recommendations: zero to three next steps the numbers support, each `text` plus a
  `priority` of high, medium or low. A recommendation is a claim like any other: it must
  follow from a number in the text, not from outside knowledge.

Leave a field empty rather than guess. An empty `chart_data` is a table with no numbers to
show; a table without a real gap between rows is not a place to invent variation.
"""
