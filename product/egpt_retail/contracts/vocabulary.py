"""Closed sets that a deployment can extend without a code change.

The alignment rule is that agent output draws its values from closed sets, because an agent
that can invent a metric name will, and it will sound plausible. The obvious way to enforce
that is a Python ``Enum``. It is also wrong here, and the reason is D9 and G10: one retail
ontology ships with every deployment, customers *extend* it with an overlay, and the overlay
has to survive a core upgrade. A frozen enum enforces the closed-set rule by breaking the
extension rule.

So a closed set is closed **at runtime** and open **at deploy time**. The terms come from the
shipped ontology plus the customer's overlay, loaded once at startup. An agent still cannot
invent a value: it is handed the enumerated list in its prompt and its output is checked
against the same source the list was rendered from. What changes is that adding a business
area for a customer whose business has one we did not think of is configuration rather than
a release.

Two consequences worth stating, because both are load-bearing:

* **The prompt and the validator read the same source.** Rendering section 6 of a skill from
  the vocabulary rather than hand-typing it is what keeps the two from drifting after an
  overlay is applied. See :meth:`Vocabulary.render_for_prompt`.
* **An overlay may add and annotate, never remove or redefine** (G10). :func:`merge` refuses
  a redefinition rather than silently letting a customer's overlay change what a core term
  means, because every KPI validated against the core meaning would still claim to be valid.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, Mapping, Optional, Tuple

__all__ = [
    "OverlayConflict",
    "Term",
    "TermOrigin",
    "Vocabulary",
    "VocabularySet",
    "aload_vocabulary_set",
    "build_vocabulary_set",
    "load_vocabulary_set",
    "merge",
]

#: Shipped with the product. One retail ontology, versioned, delivered with every
#: deployment (D9). Customers extend it; they do not fork it.
CORE_PATH = Path(__file__).resolve().parents[1] / "ontology" / "vocabularies.json"


class TermOrigin(str, Enum):
    """Where a term came from. Every node and edge carries this, per G10, so a core upgrade
    can re-apply an overlay and know which is which."""

    CORE = "core"
    OVERLAY = "overlay"


class OverlayConflict(ValueError):
    """The overlay tried to remove or redefine a core term (G10).

    A ``ValueError`` still, so every existing ``except ValueError`` keeps working; its own
    type so a caller that has to answer differently -- the API's 409 for a G10 conflict
    against its 422 for a malformed term -- can tell the two apart without parsing the
    message.
    """


@dataclass(frozen=True)
class Term:
    """One permitted value in a closed set."""

    value: str
    description: str
    origin: TermOrigin = TermOrigin.CORE

    def __post_init__(self) -> None:
        if not self.value or not self.value.replace("_", "").isalnum():
            raise ValueError(
                f"term value must be alphanumeric with underscores, got {self.value!r}"
            )
        if self.value != self.value.lower():
            raise ValueError(f"term values are lower case, got {self.value!r}")


@dataclass(frozen=True)
class Vocabulary:
    """One named closed set -- ``grain``, ``business_area``, and so on."""

    name: str
    terms: Tuple[Term, ...]

    def __contains__(self, value: object) -> bool:
        return any(term.value == value for term in self.terms)

    def __iter__(self) -> Iterator[Term]:
        return iter(self.terms)

    def __len__(self) -> int:
        return len(self.terms)

    @property
    def values(self) -> FrozenSet[str]:
        """Every permitted value."""
        return frozenset(term.value for term in self.terms)

    def of_origin(self, origin: TermOrigin) -> Tuple[Term, ...]:
        """The terms contributed by the core, or by the overlay."""
        return tuple(term for term in self.terms if term.origin is origin)

    def render_for_prompt(self) -> str:
        """The enumerated list, as a skill's section 6 needs it.

        Rendered rather than hand-typed so that a deployment which added an overlay term
        gets a prompt that mentions it. A hand-typed list is correct exactly until the first
        customer extends the ontology, and then it is silently short.
        """
        return ", ".join(
            f"`{term.value}`" for term in sorted(self.terms, key=lambda t: t.value)
        )

    def describe(self) -> str:
        """One line per term, for a prompt that needs the meanings and not just the values."""
        return "\n".join(
            f"- `{term.value}` -- {term.description}"
            for term in sorted(self.terms, key=lambda t: t.value)
        )


@dataclass(frozen=True)
class VocabularySet:
    """Every closed set a deployment holds, keyed by name."""

    vocabularies: Mapping[str, Vocabulary]
    version: str = "unversioned"

    def __getitem__(self, name: str) -> Vocabulary:
        try:
            return self.vocabularies[name]
        except KeyError:
            raise KeyError(
                f"no vocabulary named {name!r}; deployment holds {sorted(self.vocabularies)}"
            ) from None

    def __contains__(self, name: object) -> bool:
        return name in self.vocabularies

    def validate(self, vocabulary_name: str, value: str) -> None:
        """Raise unless ``value`` is a permitted term. The runtime half of the closed set."""
        vocabulary = self[vocabulary_name]
        if value not in vocabulary:
            raise ValueError(
                f"{value!r} is not a permitted {vocabulary_name}. "
                f"This deployment permits: {', '.join(sorted(vocabulary.values))}. "
                "Add it to the customer overlay if the business genuinely has it -- do not "
                "pick the nearest term."
            )


def merge(core: Vocabulary, overlay: Vocabulary) -> Vocabulary:
    """Apply an overlay to a core vocabulary.

    An overlay may add terms. It may not remove one, and it may not redefine one: a KPI
    validated against the core meaning of ``supplier`` would still claim to be valid after a
    customer quietly changed what ``supplier`` means, and nothing downstream would notice.
    Under G10 a genuine conflict belongs in the proposals table, not in a silent overwrite.
    """
    if core.name != overlay.name:
        raise ValueError(f"cannot merge vocabulary {overlay.name!r} into {core.name!r}")
    core_values = {term.value: term for term in core.terms}
    added = []
    for term in overlay.terms:
        existing = core_values.get(term.value)
        if existing is None:
            added.append(
                Term(
                    value=term.value,
                    description=term.description,
                    origin=TermOrigin.OVERLAY,
                )
            )
            continue
        if existing.description != term.description:
            raise OverlayConflict(
                f"overlay redefines core term {term.value!r} in {core.name!r}. "
                "An overlay may add and annotate, never redefine (G10) -- raise it as a proposal."
            )
    return Vocabulary(name=core.name, terms=core.terms + tuple(added))


def _parse(payload: Mapping[str, Any], origin: TermOrigin) -> Dict[str, Vocabulary]:
    vocabularies: Dict[str, Vocabulary] = {}
    for name, terms in payload.get("vocabularies", {}).items():
        vocabularies[name] = Vocabulary(
            name=name,
            terms=tuple(
                Term(
                    value=t["value"],
                    description=t.get("description", ""),
                    origin=origin,
                )
                for t in terms
            ),
        )
    return vocabularies


def build_vocabulary_set(
    core_payload: Mapping[str, Any], overlay_payload: Optional[Mapping[str, Any]]
) -> VocabularySet:
    """The vocabularies from two already-parsed payloads -- the pure half of
    :func:`load_vocabulary_set`, for a caller that holds the JSON rather than a path."""
    merged = _parse(core_payload, TermOrigin.CORE)
    if overlay_payload:
        for name, overlay in _parse(overlay_payload, TermOrigin.OVERLAY).items():
            if name in merged:
                merged[name] = merge(merged[name], overlay)
            else:
                merged[name] = overlay
    return VocabularySet(
        vocabularies=merged, version=str(core_payload.get("version", "unversioned"))
    )


def load_vocabulary_set(
    core_path: Path = CORE_PATH,
    overlay_path: Optional[Path] = None,
) -> VocabularySet:
    """Load the shipped vocabularies, with a customer overlay applied on top if there is one."""
    core_payload = json.loads(core_path.read_text())
    overlay_payload = (
        json.loads(overlay_path.read_text()) if overlay_path is not None else None
    )
    return build_vocabulary_set(core_payload, overlay_payload)


async def aload_vocabulary_set(
    core_path: Path = CORE_PATH,
    overlay_path: Optional[Path] = None,
) -> VocabularySet:
    """Async variant. Reads are done off the event loop."""

    def _read(path: Path) -> str:
        return path.read_text()

    core_payload = json.loads(await asyncio.to_thread(_read, core_path))
    overlay_payload = (
        json.loads(await asyncio.to_thread(_read, overlay_path))
        if overlay_path is not None
        else None
    )
    return build_vocabulary_set(core_payload, overlay_payload)
