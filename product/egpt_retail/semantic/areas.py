"""K9 -- which business areas this deployment can actually answer about. A query, not a judgment.

Two facts, deliberately kept apart:

**Areas exist before any source is connected.** They come from the shipped ontology, where
every concept carries the area it belongs to. That is what lets a customer configure areas up
front -- pick the ones their business has, before a single connector runs -- and it is why
this package cannot be built on the warehouse alone.

**An area is supported when a confirmed mapping backs it.** Some concept in that area has at
least one table mapped to it, and only an accepted proposal writes that edge (D10). A concept
nobody has mapped contributes nothing however well described it is: there is no data behind
it, and marking its area supported would promise answers this deployment cannot compute.

**Deterministic, and that is the point of the package.** No model is consulted, nothing is
inferred from a name, and two runs over unchanged data return identical results -- which the
cookbook asserts by comparing them field for field. The moment "is inventory supported" became
a judgment, the badge would stop meaning anything, exactly as with feasibility (K11).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "AreaStatus",
    "adetect_areas",
    "areas_in_ontology",
    "detect_areas",
    "supported_areas",
]


@dataclass(frozen=True)
class AreaStatus:
    """One business area, and whether this deployment can answer about it."""

    name: str
    supported: bool
    #: The accepted concepts in this area that have a table mapped to them.
    concepts: Tuple[str, ...] = ()
    #: The tables backing them, qualified.
    tables: Tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        """Why it is or is not supported, in a sentence a configuration screen can render."""
        if not self.supported:
            return (
                "No table is mapped to any concept in this area, so nothing here can be "
                "answered from data yet."
            )
        return (
            f"{len(self.concepts)} concept(s) mapped to {len(self.tables)} table(s): "
            f"{', '.join(self.concepts)}."
        )

    def __str__(self) -> str:
        return f"{self.name}: {'supported' if self.supported else 'not supported'}"


def areas_in_ontology(ontology: Any) -> Tuple[str, ...]:
    """Every area the shipped ontology covers, sorted.

    Read from the concepts rather than from the ``business_area`` vocabulary. The vocabulary
    is the set of *permitted terms*; this is the set of areas the ontology actually has
    concepts for, and a term nobody has modelled would appear on a configuration screen as an
    area a customer could pick and then never see supported.
    """
    return tuple(
        sorted(
            {
                area
                for concept in ontology.concepts
                if (area := getattr(concept, "business_area", None))
            }
        )
    )


def detect_areas(
    ontology: Any,
    evidence: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    only: Optional[Sequence[str]] = None,
) -> Tuple[AreaStatus, ...]:
    """Every area the ontology covers, each marked supported or not, in a stable order.

    Pure: takes the ontology and one graph query's output and returns data. Nothing here
    reaches a database, so the same inputs always give the same answer -- which is what makes
    "two runs agree exactly" a property of the code rather than a hope about the environment.

    **Every area is returned, including the unsupported ones.** A screen rendering only what
    works cannot show a customer what connecting one more source would buy them, and the
    unsupported list is the more useful half during onboarding.
    """
    names = areas_in_ontology(ontology)
    if only is not None:
        wanted = set(only)
        names = tuple(name for name in names if name in wanted)
    return tuple(
        AreaStatus(
            name=name,
            supported=bool(evidence.get(name, {}).get("concepts")),
            concepts=tuple(evidence.get(name, {}).get("concepts", ())),
            tables=tuple(evidence.get(name, {}).get("tables", ())),
        )
        for name in names
    )


async def adetect_areas(
    ontology: Any,
    evidence: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    only: Optional[Sequence[str]] = None,
) -> Tuple[AreaStatus, ...]:
    """Async variant. Pure, so it only exists to keep the calling convention uniform."""
    return await asyncio.to_thread(detect_areas, ontology, evidence, only=only)


def supported_areas(statuses: Sequence[AreaStatus]) -> Tuple[str, ...]:
    """Just the names of the supported ones, for a caller that wants the short answer."""
    return tuple(status.name for status in statuses if status.supported)


def summary(statuses: Sequence[AreaStatus]) -> Dict[str, Any]:
    """Counts and names, in the shape a configuration screen renders without transformation."""
    supported: List[str] = [s.name for s in statuses if s.supported]
    unsupported: List[str] = [s.name for s in statuses if not s.supported]
    return {
        "total": len(statuses),
        "supported": len(supported),
        "unsupported": len(unsupported),
        "supported_areas": supported,
        "unsupported_areas": unsupported,
    }
