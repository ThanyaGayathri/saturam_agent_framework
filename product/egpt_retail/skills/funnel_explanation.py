"""The output schema for the funnel explanation -- Lane R's second agent from the K14
template (R10, D12).

The RFQ chain's one model step. Every deterministic number it is handed -- the gold values
``read_gold`` fetched, the movers and friction ``sample_rows`` computed -- is copied into the
output unchanged. The model's only judgment call is ``drivers``: which movers or friction
events matter, and why. It never writes a brand or event name into prose to say so --
:class:`Driver` names its subject by reference (``kind`` + ``ref``) into ``top_movers`` or
``friction``, a row already present in this same output. That closes issue #209: a checker
that only required *one* real citation in a free-text sentence let a fabricated entity ride
alongside a real one ("brand_a's drop was driven by rival evilbrand"). With no free-text span
left in ``drivers`` in which to name an entity, :func:`citations_within_input` is exact-match
set membership, not text search, mirroring ``join_path_explainer.verdict_for_path``: this
agent explains numbers, it does not add any. :func:`render_summary` builds the human-readable
sentence a person reads, deterministically, from the same grounded data -- never authored by
the model, so it can never carry a citation the checker didn't already verify. ``notes``
remains free text and ungrounded (pre-existing, not narrowed by this closure); it is never
read by :func:`render_summary`, but a future caller that surfaces it verbatim would reopen
this same shape through a field this module does not guard.
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "BrandMovement",
    "Direction",
    "Driver",
    "DriverKind",
    "DriverReason",
    "FrictionSample",
    "FunnelExplanation",
    "citations_within_input",
    "closed_sets_match_prompt",
    "aclosed_sets_match_prompt",
    "friction_matches_input",
    "movers_match_input",
    "render_summary",
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


class DriverKind(str, Enum):
    """Which reference space a :class:`Driver`'s ``ref`` points into. A closed set for the
    same reason ``Direction`` is: there are exactly two things a driver can be about."""

    BRAND = "brand"
    FRICTION_EVENT = "friction_event"


class DriverReason(str, Enum):
    """Closed set of why a row is called out as a likely driver. Mirrors ``Direction``: a
    judgment this schema already has the shape for, not a phrase the model composes."""

    LARGEST_MOVE = "largest_move"
    NEW_ENTRANT = "new_entrant"
    ELEVATED_FRICTION = "elevated_friction"
    CORRELATED_WITH_FRICTION = "correlated_with_friction"
    OTHER = "other"


class Driver(BaseModel):
    """One likely driver, named only by reference. ``kind`` says which of ``top_movers`` or
    ``friction`` ``ref`` points into; ``ref`` must equal a ``brand`` or ``event_name`` already
    present there. The model never types a brand or event name into free text -- there is no
    prose span left in which an entity absent from the input could be smuggled in alongside a
    real one, which is the gap issue #209 closes."""

    kind: DriverKind
    ref: str = Field(
        min_length=1,
        description="A brand already present in top_movers when kind is brand, or an "
        "event_name already present in friction when kind is friction_event. Copied, never "
        "invented.",
    )
    kpi_identifier: str = Field(
        default="",
        description="Required (non-empty) when kind is brand: the top_movers row's own "
        "kpi_identifier, disambiguating a brand that moved on more than one brand-grain KPI "
        "this period. Ignored when kind is friction_event.",
    )
    reason: DriverReason

    @model_validator(mode="after")
    def _kpi_identifier_required_for_brand(self) -> "Driver":
        """Enforces this field's own documented contract at construction time, so a brand
        driver missing its ``kpi_identifier`` fails with a clear message here rather than as
        an opaque "uncited" refusal from :func:`citations_within_input` downstream."""
        if self.kind is DriverKind.BRAND and not self.kpi_identifier:
            raise ValueError("kpi_identifier is required when kind is brand")
        return self


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
    friction_reason: str = Field(
        default="",
        description="Copied from friction.reason in the input, unchanged. Empty when friction_available is true.",
    )
    drivers: List[Driver] = Field(
        default_factory=list,
        description="Zero or more likely drivers, each referencing a row already present in "
        "top_movers or friction by kind and ref -- never by naming it in prose. Empty when "
        "nothing in the input supports a driver.",
    )
    snapshot_version: str = Field(
        description="The input's snapshot_version, copied unchanged."
    )
    notes: str = Field(
        default="",
        description="Empty on a clean run. Otherwise what was missing from the input.",
    )

    @model_validator(mode="after")
    def _friction_available_matches_friction(self) -> "FunnelExplanation":
        """``friction_available`` and ``friction`` must agree, per this schema's own field
        descriptions ("empty otherwise"). Without this, a model could set
        ``friction_available=False`` while still listing real friction rows (or the reverse),
        which is exactly the shape that both hides a real signal from the reader and unlocks an
        otherwise-dead branch in :func:`render_summary`."""
        if self.friction_available != bool(self.friction):
            raise ValueError(
                "friction_available must be true iff friction is non-empty "
                f"(friction_available={self.friction_available}, "
                f"friction has {len(self.friction)} row(s))"
            )
        return self


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


def citations_within_input(explanation: FunnelExplanation) -> bool:
    """Every driver's reference is a row the model was actually handed: a
    ``(kpi_identifier, brand)`` pair from ``top_movers`` when ``kind`` is ``brand`` --
    disambiguating a brand that moved on more than one brand-grain KPI this period -- or an
    ``event_name`` from ``friction`` when ``kind`` is ``friction_event``. Because a driver only
    ever carries a reference -- never free text naming an entity -- this is exact-match set
    membership, not text search: there is no prose left in which a fabricated entity could ride
    alongside a real citation, which is the gap issue #209 found (a sentence citing one real
    brand plus one invented one passed the old free-text check)."""
    allowed_brand_moves = {(m.kpi_identifier, m.brand) for m in explanation.top_movers}
    allowed_events = {f.event_name for f in explanation.friction}
    for driver in explanation.drivers:
        if driver.kind is DriverKind.BRAND:
            if (driver.kpi_identifier, driver.ref) not in allowed_brand_moves:
                return False
        elif driver.kind is DriverKind.FRICTION_EVENT:
            if driver.ref not in allowed_events:
                return False
    return True


def friction_matches_input(
    explanation: FunnelExplanation, friction_input: Mapping[str, Any]
) -> bool:
    """The friction-side analog of :func:`movers_match_input`: ``friction_available``,
    ``friction`` and ``friction_reason`` must match ``friction.{available,rows,reason}`` from
    the ``sample_rows`` input verbatim. Without this, the model's self-reported friction rows
    are the only thing :func:`citations_within_input` ever checks a friction ``Driver``
    against -- a wholly fabricated friction event (or a suppressed real one) would otherwise
    pass grounding as long as the model's own ``friction``/``friction_available`` agreed with
    itself."""
    available = bool(friction_input.get("available", False))
    if explanation.friction_available != available:
        return False
    if explanation.friction_reason != (friction_input.get("reason") or ""):
        return False
    if not available:
        return True
    expected = [
        {
            "event_name": row["event_name"],
            "rage_clicks": row.get("rage_clicks"),
            "dead_clicks": row.get("dead_clicks"),
            "script_errors": row.get("script_errors"),
        }
        for row in friction_input.get("rows") or []
    ]
    actual = [
        {
            "event_name": f.event_name,
            "rage_clicks": f.rage_clicks,
            "dead_clicks": f.dead_clicks,
            "script_errors": f.script_errors,
        }
        for f in explanation.friction
    ]
    return actual == expected


def _mover_sentence(mover: BrandMovement) -> str:
    """The one clause :func:`render_summary` writes for a mover -- wording keyed only on
    ``direction``, a closed set, so there is nothing here for a caller to have fabricated."""
    if mover.direction is Direction.NEW:
        return (
            f"{mover.brand} is new to the funnel this period at {mover.current_value}."
        )
    if mover.direction is Direction.UP:
        return (
            f"{mover.brand}'s {mover.kpi_identifier} rose to {mover.current_value} "
            f"from {mover.prior_value}."
        )
    if mover.direction is Direction.DOWN:
        return (
            f"{mover.brand}'s {mover.kpi_identifier} fell to {mover.current_value} "
            f"from {mover.prior_value}."
        )
    if mover.direction is Direction.FLAT:
        return f"{mover.brand}'s {mover.kpi_identifier} held steady at {mover.current_value}."
    if mover.direction is Direction.UNKNOWN:
        return (
            f"{mover.brand}'s {mover.kpi_identifier} could not be compared this period."
        )
    raise AssertionError(f"unreachable: unhandled Direction {mover.direction!r}")


def _friction_sentence(
    sample: FrictionSample, reason: Optional[DriverReason] = None
) -> str:
    """The one clause :func:`render_summary` writes for a friction row -- wording keyed on the
    citing driver's ``reason`` when one is known, so a row cited for a different reason than
    "elevated friction" doesn't get misdescribed as one."""
    if reason is DriverReason.CORRELATED_WITH_FRICTION:
        lead = f"{sample.event_name} correlated with elevated friction this period"
    elif reason is DriverReason.OTHER:
        lead = f"{sample.event_name} was flagged this period"
    else:
        lead = f"{sample.event_name} carried elevated friction this period"
    rage = sample.rage_clicks or 0
    dead = sample.dead_clicks or 0
    errors = sample.script_errors or 0
    return (
        f"{lead} ({rage} rage click{'' if rage == 1 else 's'}, "
        f"{dead} dead click{'' if dead == 1 else 's'}, "
        f"{errors} script error{'' if errors == 1 else 's'})."
    )


def render_summary(explanation: FunnelExplanation) -> str:
    """The one-to-three-sentence headline a person reads, built entirely from data already
    grounded elsewhere in this same output -- ``top_movers``, ``friction`` and the model's own
    ``drivers`` references -- and never authored by the model itself. This is what makes the
    gap issue #209 closes unreachable by construction rather than merely detected: there is no
    field left where free text naming an entity reaches the person reading it. Call this
    *after* :func:`citations_within_input` has passed; it defensively no-ops on any ``ref`` it
    cannot resolve rather than trusting it.
    """
    if not explanation.top_movers and not explanation.friction:
        named = ", ".join(explanation.kpi_identifiers) or "the requested KPIs"
        message = f"No gold rows were available for {named} this run."
        if explanation.friction_reason:
            message += (
                f" Friction is not attributable this run: {explanation.friction_reason}"
            )
        return message

    movers_by_key = {(m.kpi_identifier, m.brand): m for m in explanation.top_movers}
    friction_by_event = {f.event_name: f for f in explanation.friction}
    seen: Set[Any] = set()
    sentences: List[str] = []
    for driver in explanation.drivers:
        key = (driver.kind, driver.kpi_identifier, driver.ref)
        if key in seen:
            continue
        if driver.kind is DriverKind.BRAND:
            mover = movers_by_key.get((driver.kpi_identifier, driver.ref))
            if mover is not None:
                sentences.append(_mover_sentence(mover))
                seen.add(key)
        else:
            sample = friction_by_event.get(driver.ref)
            if sample is not None:
                sentences.append(_friction_sentence(sample, reason=driver.reason))
                seen.add(key)

    if not sentences:
        if explanation.top_movers:
            sentences.append(_mover_sentence(explanation.top_movers[0]))
        elif explanation.friction:
            sentences.append(_friction_sentence(explanation.friction[0]))
    return " ".join(sentences)


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
    return {
        "direction": _enumerated_values(prompt, "direction")
        == {member.value for member in Direction},
        "kind": _enumerated_values(prompt, "kind")
        == {member.value for member in DriverKind},
        "reason": _enumerated_values(prompt, "reason")
        == {member.value for member in DriverReason},
    }


async def aclosed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(closed_sets_match_prompt, prompt_path)
