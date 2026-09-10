"""The output schema for the funnel explanation -- Lane R's second agent from the K14
template (R10, D12).

The RFQ chain's one model step. Every deterministic number it is handed -- the gold values
``read_gold`` fetched, the movers and friction ``sample_rows`` computed -- is copied into the
output unchanged; the model's only free-text contribution is ``summary`` and
``likely_drivers``, and even those may only name a brand or a friction event this schema
already carries in ``brands_cited`` and ``friction``. :func:`citations_within_input` is the
deterministic check a cookbook runs instead of judging the prose by eye, mirroring
``join_path_explainer.verdict_for_path``: this agent explains numbers, it does not add any.
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from pydantic import BaseModel, Field

__all__ = [
    "BrandMovement",
    "Direction",
    "FrictionSample",
    "FunnelExplanation",
    "citations_within_input",
    "closed_sets_match_prompt",
    "aclosed_sets_match_prompt",
    "movers_match_input",
]

PROMPT_PATH = Path(__file__).parent / "funnel_explanation.md"


class Direction(str, Enum):
    """Mirrors ``executors.rfq.rank_movers``'s five outcomes exactly. The model copies this
    field from the movers input; it never characterises a delta in its own words."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    NEW = "new"
    UNKNOWN = "unknown"


class BrandMovement(BaseModel):
    """One row of ``sample_rows``'s ``movers`` output, copied unchanged into the response."""

    kpi_identifier: str = Field(description="Copied from the movers input's key.")
    brand: str = Field(description="Copied from the movers input row, unchanged.")
    current_value: Optional[str] = Field(
        default=None, description="Copied from the movers input row, unchanged."
    )
    prior_value: Optional[str] = Field(
        default=None, description="Copied from the movers input row, unchanged."
    )
    delta: Optional[str] = Field(
        default=None, description="Copied from the movers input row, unchanged."
    )
    # No `description=` here: an enum-typed field serialises to a `$ref` and a strict
    # structured-output schema rejects a `$ref` carrying sibling keywords (template, section 6).
    direction: Direction


class FrictionSample(BaseModel):
    """One row of ``sample_rows``'s ``friction.rows``, copied unchanged."""

    event_name: str = Field(
        description="Copied from the friction input row, unchanged."
    )
    rage_clicks: Optional[int] = Field(
        default=None, description="Copied from the friction input row, unchanged."
    )
    dead_clicks: Optional[int] = Field(
        default=None, description="Copied from the friction input row, unchanged."
    )
    script_errors: Optional[int] = Field(
        default=None, description="Copied from the friction input row, unchanged."
    )


class FunnelExplanation(BaseModel):
    """The whole response. ``notes`` carries the refusal, per section 8 of the template."""

    kpi_identifiers: List[str] = Field(
        description="The rows input's KPI identifiers, copied and sorted."
    )
    top_movers: List[BrandMovement] = Field(
        default_factory=list,
        description="Copied from the movers input verbatim, in the order given -- add none, drop none.",
    )
    friction_available: bool = Field(
        description="Copied from friction.available in the input, unchanged."
    )
    friction: List[FrictionSample] = Field(
        default_factory=list,
        description="Copied from friction.rows when friction_available is true; empty otherwise.",
    )
    brands_cited: List[str] = Field(
        default_factory=list,
        description="Every brand named in summary or likely_drivers. Each one must also appear in top_movers.",
    )
    summary: str = Field(
        description="One to three sentences on what moved and where. Every brand it names must be in brands_cited."
    )
    likely_drivers: List[str] = Field(
        default_factory=list,
        description="Zero or more sentences, each naming a brand from brands_cited or an event_name from friction. Empty when nothing in the input supports a driver.",
    )
    snapshot_version: str = Field(
        description="The input's snapshot_version, copied unchanged."
    )
    notes: str = Field(
        default="",
        description="Empty on a clean run. Otherwise what was missing from the input.",
    )


def movers_match_input(
    explanation: FunnelExplanation,
    movers_input: Mapping[str, Sequence[Mapping[str, Any]]],
) -> bool:
    """``top_movers`` must be the movers input, verbatim, in the same order -- the deterministic
    check for the first specific rule (section 5): the model reasons from these rows, it does
    not get to edit them."""
    expected = [
        {
            "kpi_identifier": identifier,
            "brand": row["brand"],
            "current_value": row.get("current_value"),
            "prior_value": row.get("prior_value"),
            "delta": row.get("delta"),
            "direction": row.get("direction"),
        }
        for identifier, rows in movers_input.items()
        for row in rows
    ]
    actual = [
        {
            "kpi_identifier": m.kpi_identifier,
            "brand": m.brand,
            "current_value": m.current_value,
            "prior_value": m.prior_value,
            "delta": m.delta,
            "direction": m.direction.value,
        }
        for m in explanation.top_movers
    ]
    return actual == expected


def _mentions_a_grounded_term(text: str, allowed_terms: Set[str]) -> bool:
    """Whether ``text`` names at least one term from ``allowed_terms``, word-bounded so a real
    term that is a prefix of a fabricated one (``brand_a`` inside ``brand_ab``) does not count
    as a match."""
    return any(
        re.search(rf"\b{re.escape(term)}\b", text) is not None for term in allowed_terms
    )


def citations_within_input(explanation: FunnelExplanation) -> bool:
    """Every brand and friction event the model claims a driver from actually appears in the
    rows it was handed. This is the check that stands in for reading the prose: a citation
    outside ``top_movers``/``friction`` is a figure the model introduced, which the second
    standing rule ("the language model writes the explanation, and everything the explanation
    is built from was computed rather than recalled") forbids."""
    allowed_brands = {m.brand for m in explanation.top_movers}
    if not set(explanation.brands_cited) <= allowed_brands:
        return False
    allowed_events = {f.event_name for f in explanation.friction}
    allowed_terms = allowed_brands | allowed_events
    # `summary` is required and non-empty even when there is nothing to report (no movers,
    # no friction) -- unlike `likely_drivers`, which is just an empty list in that case and
    # so never reaches this loop. With `allowed_terms` empty there is nothing to cite from
    # and nothing to check against, so a "nothing moved" summary is honest, not ungrounded.
    if allowed_terms and not _mentions_a_grounded_term(
        explanation.summary, allowed_terms
    ):
        return False
    for driver in explanation.likely_drivers:
        if not _mentions_a_grounded_term(driver, allowed_terms):
            return False
    return True


def _enumerated_values(prompt: str, field_name: str) -> Set[str]:
    """Mirrors ``join_path_explainer``'s own helper: every backtick-quoted term on a table row
    whose first cell names ``field_name``, allowing a ``table[].field`` cell (this schema's
    enum sits on a nested model, not a top-level one)."""
    values: Set[str] = set()
    for line in prompt.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        named = cells[0].strip("`").split(".")[-1]
        if named != field_name:
            continue
        values.update(re.findall(r"`([a-z_]+)`", cells[-1]))
    return values


def closed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Whether every enum in this module is enumerated in full in the prompt."""
    prompt = prompt_path.read_text()
    declared = {member.value for member in Direction}
    return {"direction": _enumerated_values(prompt, "direction") == declared}


async def aclosed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(closed_sets_match_prompt, prompt_path)
