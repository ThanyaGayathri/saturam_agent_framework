"""The decomposition arithmetic (K25): which slice of which dimension accounts for a KPI's
change, and whether that slice is behaving unusually for itself.

Pure functions over numbers. The pattern is the published one for multi-dimensional metric
root cause (Adtributor; the "change analysis" of the BI products): per dimension, each
slice's **contribution** to the headline change -- for an additive KPI its own delta, for a
ratio the two-way split into a **rate effect** (the slice's own rate moved) and a **mix
effect** (the slice's share of the denominator moved) -- then **surprise**, the slice's
current value against its own trailing history as a robust z (median and MAD), then a
**support floor** below which a slice cannot be named, then the ranking: the dimension
whose top slice explains most of the gap wins; ties within a tenth are reported as ties.

Nothing here reads a database or calls a model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Driver",
    "DimensionResult",
    "SENSITIVITY",
    "decompose_dimension",
    "rank_dimensions",
    "robust_z",
    "support_floor",
]

#: Sensitivity in three words: the surprise bar (|z| at or above which a slice counts as
#: unusual) and the support multiplier (how many rows a slice needs before it can be named).
SENSITIVITY: Mapping[str, Dict[str, float]] = {
    "clear": {"z": 3.0, "support": 1.5},
    "usual": {"z": 2.0, "support": 1.0},
    "more": {"z": 1.5, "support": 0.5},
}
TIE_TOLERANCE = 0.10
Z_CAP = 5.0
BLANK = "(blank)"


@dataclass
class Driver:
    dimension: str
    value: str
    effect: str  # volume | rate | mix
    contribution: float
    share_of_gap: Optional[float]
    current: Optional[float]
    baseline: Optional[float]
    norm: Optional[float]
    z: Optional[float]
    support: int
    #: The rate and mix parts for a ratio slice, both kept so the report can say which.
    rate_effect: Optional[float] = None
    mix_effect: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "effect": self.effect,
            "contribution": round(self.contribution, 4),
            "share_of_gap": (
                round(self.share_of_gap, 4) if self.share_of_gap is not None else None
            ),
            "current": self.current,
            "baseline": self.baseline,
            "norm": self.norm,
            "z": round(self.z, 2) if self.z is not None else None,
            "support": self.support,
            "rate_effect": (
                round(self.rate_effect, 4) if self.rate_effect is not None else None
            ),
            "mix_effect": (
                round(self.mix_effect, 4) if self.mix_effect is not None else None
            ),
        }


@dataclass
class DimensionResult:
    dimension: str
    drivers: List[Driver] = field(default_factory=list)
    below_support: List[Tuple[str, int]] = field(default_factory=list)
    #: Share of |gap| the top slice accounts for: the dimension's explanatory power.
    power: float = 0.0
    unusual: bool = False


def robust_z(history: Sequence[float], current: float) -> Optional[float]:
    """The current value against its own history: (current - median) / (1.4826 * MAD),
    capped at ±Z_CAP. Fewer than three points is no history; a flat history with a moved
    value is the cap, not a division by zero."""
    values = [float(v) for v in history if v is not None and not math.isnan(float(v))]
    if len(values) < 3:
        return None
    centre = median(values)
    mad = median(abs(v - centre) for v in values)
    if mad == 0:
        return 0.0 if current == centre else math.copysign(Z_CAP, current - centre)
    z = (current - centre) / (1.4826 * mad)
    return max(-Z_CAP, min(Z_CAP, z))


def support_floor(rows_in_period: int, *, sensitivity: str = "usual") -> int:
    """The rows a slice needs in the period to be named: max(20, 5 % of the period),
    scaled by sensitivity."""
    factor = SENSITIVITY.get(sensitivity, SENSITIVITY["usual"])["support"]
    return int(math.ceil(max(20, 0.05 * rows_in_period) * factor))


def _rate(num: float, den: float) -> Optional[float]:
    return num / den if den else None


def _slice_name(value: Any) -> str:
    return BLANK if value is None or str(value).strip() == "" else str(value)


def decompose_dimension(
    dimension: str,
    kind: str,
    current: Mapping[Any, Tuple[float, Optional[float]]],
    baseline: Mapping[Any, Tuple[float, Optional[float]]],
    history: Mapping[Any, Sequence[float]],
    *,
    scale: float = 1.0,
    floor: int = 20,
    z_bar: float = 2.0,
) -> DimensionResult:
    """One dimension's slices, each as a :class:`Driver`.

    ``current`` and ``baseline`` map a slice value to ``(numerator, denominator)`` --
    denominator ``None`` for an additive KPI. ``history`` maps a slice to its own past
    values (the KPI's value for that slice at each earlier period), for surprise.
    """
    keys = set(current) | set(baseline)
    result = DimensionResult(dimension=dimension)
    if kind == "ratio":
        n1 = sum(v[0] for v in current.values())
        d1 = sum(v[1] or 0.0 for v in current.values())
        n0 = sum(v[0] for v in baseline.values())
        d0 = sum(v[1] or 0.0 for v in baseline.values())
        r1, r0 = _rate(n1, d1), _rate(n0, d0)
        gap = (r1 - r0) * scale if r1 is not None and r0 is not None else None
        for key in keys:
            cn, cd = current.get(key, (0.0, 0.0))
            bn, bd = baseline.get(key, (0.0, 0.0))
            cd, bd = cd or 0.0, bd or 0.0
            w1, w0 = (cd / d1 if d1 else 0.0), (bd / d0 if d0 else 0.0)
            s1, s0 = _rate(cn, cd), _rate(bn, bd)
            # Rate effect: the slice's own rate moved, weighted by its current share.
            # Mix effect: its share moved, weighted by how far its old rate sat from the
            # whole. The two sum to the headline change across slices.
            rate_effect = w1 * ((s1 or 0.0) - (s0 if s0 is not None else (r0 or 0.0)))
            mix_effect = (w1 - w0) * (
                (s0 if s0 is not None else (r0 or 0.0)) - (r0 or 0.0)
            )
            contribution = (rate_effect + mix_effect) * scale
            support = int(cd)
            name = _slice_name(key)
            if support < floor:
                result.below_support.append((name, support))
                continue
            cur = s1 * scale if s1 is not None else None
            base = s0 * scale if s0 is not None else None
            past = list(history.get(key, ()))
            z = robust_z(past, cur) if cur is not None else None
            effect = "rate" if abs(rate_effect) >= abs(mix_effect) else "mix"
            result.drivers.append(
                Driver(
                    dimension=dimension,
                    value=name,
                    effect=effect,
                    contribution=contribution,
                    share_of_gap=(contribution / gap) if gap else None,
                    current=round(cur, 4) if cur is not None else None,
                    baseline=round(base, 4) if base is not None else None,
                    norm=round(median(past), 4) if len(past) >= 3 else None,
                    z=z,
                    support=support,
                    rate_effect=rate_effect * scale,
                    mix_effect=mix_effect * scale,
                )
            )
    else:
        t1 = sum(v[0] for v in current.values())
        t0 = sum(v[0] for v in baseline.values())
        gap = t1 - t0
        for key in keys:
            cn = current.get(key, (0.0, None))[0]
            bn = baseline.get(key, (0.0, None))[0]
            contribution = cn - bn
            support = int(cn)
            name = _slice_name(key)
            if support < floor:
                result.below_support.append((name, support))
                continue
            past = list(history.get(key, ()))
            result.drivers.append(
                Driver(
                    dimension=dimension,
                    value=name,
                    effect="volume",
                    contribution=contribution,
                    share_of_gap=(contribution / gap) if gap else None,
                    current=cn,
                    baseline=bn,
                    norm=round(median(past), 4) if len(past) >= 3 else None,
                    z=robust_z(past, cn),
                    support=support,
                )
            )
    # Largest contribution first, but a slice that moved *with* the headline ahead of one
    # that pulled against it: "Refrigeration drove the drop" is the finding; "Ovens rose
    # while everything fell" is the contrast, reported under it, never as the driver.
    result.drivers.sort(
        key=lambda d: ((d.share_of_gap or 0.0) < 0, -abs(d.contribution))
    )
    if result.drivers and gap:
        top = result.drivers[0]
        result.power = abs(top.share_of_gap or 0.0)
        result.unusual = top.z is not None and abs(top.z) >= z_bar
    return result


def rank_dimensions(
    results: Sequence[DimensionResult], *, z_bar: float = 2.0
) -> Tuple[List[DimensionResult], List[str], bool]:
    """Dimensions best first, the ones tied with the winner, and whether the ranking was
    decided by surprise (an unusual top slice) or fell back to size alone."""
    scored = [r for r in results if r.drivers]
    unusual = [r for r in scored if r.unusual]
    pool = unusual or scored
    by_surprise = bool(unusual)
    pool = sorted(pool, key=lambda r: -r.power)
    rest = sorted((r for r in scored if r not in pool), key=lambda r: -r.power)
    ordered = pool + rest
    ties: List[str] = []
    if len(pool) > 1 and pool[0].power:
        ties = [
            r.dimension
            for r in pool[1:]
            if abs(pool[0].power - r.power) <= TIE_TOLERANCE * pool[0].power
        ]
    return ordered, ties, by_surprise
