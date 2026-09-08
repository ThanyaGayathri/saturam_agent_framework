"""The shipped retail ontology as an artifact: concepts, relations and synonyms.

Layer 2 of ``docs/plan/domain-knowledge-layers.md`` -- retail concepts, the relations between
them, and the words people actually use for each. **Types only, never instances**: the graph
holds what a Quote *is*, never a row from a quote table. That boundary is what keeps the graph
small enough to traverse and keeps records in the system that already governs them.

Built by us, once, and judged by a retail practitioner reading it (D9). Customers extend it
with an overlay; they do not fork it. Every node and edge therefore carries an ``origin``, so
a core upgrade can re-apply an overlay and know which is which (G10).

Everything here is pure -- an artifact and the rules for combining two of them. Loading from
disk has both variants; :mod:`egpt_retail.ontology.store` is where Neo4j is touched.

The one design point worth stating up front, because it is the step usually skipped: a concept
is embedded over its **name, description and synonyms together**
(:meth:`Ontology.embedding_text`). Embedding the name alone makes the ontology reachable only
by exact match, and the synonym behaviour the whole layer exists for quietly disappears.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from egpt_retail.contracts.vocabulary import TermOrigin, VocabularySet

__all__ = [
    "ARTIFACT_PATH",
    "Concept",
    "Ontology",
    "Relation",
    "aload_ontology",
    "load_ontology",
    "merge_ontology",
]

#: The shipped artifact. Versioned, delivered with every deployment (D9).
ARTIFACT_PATH = Path(__file__).parent / "retail.json"


@dataclass(frozen=True)
class Concept:
    """One retail concept. A type, never an instance."""

    name: str
    description: str
    business_area: str
    synonyms: Tuple[str, ...] = ()
    #: Plain-language questions this concept should answer. Distinct from synonyms: a synonym
    #: is another name for the thing, a question pattern is how somebody asks about it.
    question_patterns: Tuple[str, ...] = ()
    origin: TermOrigin = TermOrigin.CORE

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(
                f"concept name must be alphanumeric with underscores, got {self.name!r}"
            )
        if not self.description.strip():
            raise ValueError(
                f"{self.name}: a concept with no description cannot be embedded over anything but its "
                "name, which makes it reachable only by exact match."
            )

    @property
    def embedding_text(self) -> str:
        """What this concept is embedded over: name, description and synonyms together.

        Not the name alone. A question about "computers" has to reach the Product concept, and
        it only does so if the vector carries the words a person would actually use.
        """
        parts = [self.name, self.description]
        if self.synonyms:
            parts.append("Also called: " + ", ".join(self.synonyms))
        if self.question_patterns:
            parts.append("Questions: " + " ".join(self.question_patterns))
        return "\n".join(parts)

    @property
    def lexical_text(self) -> str:
        """What the full-text index sees. Names and synonyms carry the exact-match weight."""
        return " ".join([self.name, *self.synonyms, self.description])


@dataclass(frozen=True)
class Relation:
    """A directed edge between two concepts.

    ``type`` becomes the Neo4j relationship type, so it is upper snake case by convention and
    validated as an identifier -- a relationship type is interpolated into Cypher (it cannot be
    parameterised), and this is the check that keeps that safe.
    """

    type: str
    source: str
    target: str
    description: str = ""
    cardinality: str = "many-to-one"
    origin: TermOrigin = TermOrigin.CORE

    def __post_init__(self) -> None:
        if not self.type.isidentifier() or not self.type.isupper():
            raise ValueError(
                f"relation type must be an UPPER_SNAKE_CASE identifier, got {self.type!r}. "
                "It is interpolated into Cypher as a relationship type, which cannot be bound "
                "as a parameter, so it is validated rather than escaped."
            )


@dataclass(frozen=True)
class Ontology:
    """A versioned set of concepts and the relations between them."""

    version: str
    concepts: Tuple[Concept, ...]
    relations: Tuple[Relation, ...] = ()

    def __post_init__(self) -> None:
        names = [concept.name for concept in self.concepts]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate concept names: {sorted(duplicates)}")

    def validate_relations(self) -> None:
        """Every relation must join two concepts this ontology holds.

        Deliberately **not** in ``__post_init__``. An overlay is a *fragment*: a customer
        adding a RepGroup concept that ``REPRESENTS`` a core Brand is writing a perfectly
        valid overlay, and it references a concept the fragment itself does not contain.
        Refusing that at construction would make the common overlay shape unexpressible.

        So completeness is checked where it actually matters -- on the merged result, and
        again in :meth:`OntologyStore.write` before anything reaches the graph. That is the
        point at which a dangling edge does damage: ``MERGE`` on a missing endpoint creates it
        as an empty node, so the ontology silently grows a concept with no description, no
        business area and no embedding.
        """
        known = self.names
        dangling = [
            f"{relation.type} {relation.source}->{relation.target}"
            for relation in self.relations
            if relation.source not in known or relation.target not in known
        ]
        if dangling:
            raise ValueError(
                f"relations reference concepts that do not exist: {dangling}. "
                "A MERGE on a missing endpoint creates it as an empty node, so the edge does "
                "not fail -- it silently invents a concept."
            )

    def __iter__(self) -> Iterator[Concept]:
        return iter(self.concepts)

    def __len__(self) -> int:
        return len(self.concepts)

    @property
    def names(self) -> FrozenSet[str]:
        return frozenset(concept.name for concept in self.concepts)

    def concept(self, name: str) -> Optional[Concept]:
        """One concept by exact name, or ``None``."""
        for concept in self.concepts:
            if concept.name == name:
                return concept
        return None

    def of_origin(self, origin: TermOrigin) -> Tuple[Concept, ...]:
        """Concepts contributed by the core, or by a customer's overlay."""
        return tuple(concept for concept in self.concepts if concept.origin is origin)

    def relations_of_origin(self, origin: TermOrigin) -> Tuple[Relation, ...]:
        return tuple(
            relation for relation in self.relations if relation.origin is origin
        )

    def synonym_index(self) -> Mapping[str, str]:
        """Every synonym mapped to the concept it names, lower-cased.

        A synonym claimed by two concepts is a defect the practitioner review should catch, so
        this raises rather than silently letting the later one win.
        """
        index: Dict[str, str] = {}
        for concept in self.concepts:
            for synonym in concept.synonyms:
                key = synonym.lower()
                if key in index and index[key] != concept.name:
                    raise ValueError(
                        f"synonym {synonym!r} is claimed by both {index[key]!r} and "
                        f"{concept.name!r}. A term that means two things routes questions to "
                        "whichever concept was written first."
                    )
                index[key] = concept.name
        return index

    def validate_against(self, vocabularies: VocabularySet) -> None:
        """Every concept's ``business_area`` must be a term this deployment holds.

        The same closed-set rule the KPI registry applies, for the same reason: an area that is
        not in the vocabulary cannot be rendered into a prompt or grouped by a screen.
        """
        for concept in self.concepts:
            vocabularies.validate("business_area", concept.business_area)


def merge_ontology(core: Ontology, overlay: Ontology) -> Ontology:
    """Apply a customer overlay to the shipped core.

    An overlay may **add** a concept, and may **annotate** an existing one by contributing
    extra synonyms or question patterns. It may not remove a concept and it may not redefine
    one: a mapping or KPI validated against the core meaning of ``Supplier`` would still claim
    to be valid after a customer quietly changed what ``Supplier`` means, and nothing
    downstream would notice (G10).

    Annotation is additive on purpose. It is the common case -- a customer calls a quote an
    "RFQ" and nothing else about the concept differs -- and forcing that through a whole new
    concept would fragment the ontology exactly where it needs to stay shared.
    """
    merged: List[Concept] = []
    by_name = {concept.name: concept for concept in overlay.concepts}

    for concept in core.concepts:
        addition = by_name.pop(concept.name, None)
        if addition is None:
            merged.append(concept)
            continue
        if addition.description.strip() != concept.description.strip():
            raise ValueError(
                f"overlay redefines the description of core concept {concept.name!r}. "
                "An overlay may add and annotate, never redefine (G10) -- raise it as a proposal."
            )
        if addition.business_area != concept.business_area:
            raise ValueError(
                f"overlay moves core concept {concept.name!r} from business area "
                f"{concept.business_area!r} to {addition.business_area!r}. That is a redefinition."
            )
        merged.append(
            replace(
                concept,
                synonyms=concept.synonyms
                + tuple(s for s in addition.synonyms if s not in concept.synonyms),
                question_patterns=concept.question_patterns
                + tuple(
                    q
                    for q in addition.question_patterns
                    if q not in concept.question_patterns
                ),
            )
        )

    # Whatever the overlay named that the core did not is a genuine addition.
    for concept in overlay.concepts:
        if concept.name in by_name:
            merged.append(replace(concept, origin=TermOrigin.OVERLAY))

    core_edges = {(r.type, r.source, r.target) for r in core.relations}
    added_relations = tuple(
        replace(relation, origin=TermOrigin.OVERLAY)
        for relation in overlay.relations
        if (relation.type, relation.source, relation.target) not in core_edges
    )
    return Ontology(
        version=core.version,
        concepts=tuple(merged),
        relations=core.relations + added_relations,
    )


def _parse(payload: Mapping[str, Any], origin: TermOrigin) -> Ontology:
    concepts = tuple(
        Concept(
            name=item["name"],
            description=item.get("description", ""),
            business_area=item.get("business_area", ""),
            synonyms=tuple(item.get("synonyms", ())),
            question_patterns=tuple(item.get("question_patterns", ())),
            origin=origin,
        )
        for item in payload.get("concepts", ())
    )
    relations = tuple(
        Relation(
            type=item["type"],
            source=item["source"],
            target=item["target"],
            description=item.get("description", ""),
            cardinality=item.get("cardinality", "many-to-one"),
            origin=origin,
        )
        for item in payload.get("relations", ())
    )
    return Ontology(
        version=str(payload.get("version", "unversioned")),
        concepts=concepts,
        relations=relations,
    )


def _build(
    core_payload: Mapping[str, Any], overlay_payload: Optional[Mapping[str, Any]]
) -> Ontology:
    core = _parse(core_payload, TermOrigin.CORE)
    if overlay_payload is None:
        core.validate_relations()
        return core
    merged = merge_ontology(core, _parse(overlay_payload, TermOrigin.OVERLAY))
    merged.validate_relations()
    return merged


def load_ontology(
    core_path: Path = ARTIFACT_PATH,
    overlay_path: Optional[Path] = None,
) -> Ontology:
    """Load the shipped ontology, with a customer overlay applied on top if there is one."""
    core_payload = json.loads(Path(core_path).read_text())
    overlay_payload = (
        json.loads(Path(overlay_path).read_text()) if overlay_path is not None else None
    )
    return _build(core_payload, overlay_payload)


async def aload_ontology(
    core_path: Path = ARTIFACT_PATH,
    overlay_path: Optional[Path] = None,
) -> Ontology:
    """Async variant. Reads happen off the event loop."""

    def _read(path: Path) -> str:
        return Path(path).read_text()

    core_payload = json.loads(await asyncio.to_thread(_read, core_path))
    overlay_payload = (
        json.loads(await asyncio.to_thread(_read, overlay_path))
        if overlay_path is not None
        else None
    )
    return _build(core_payload, overlay_payload)


def embedding_texts(concepts: Sequence[Concept]) -> List[str]:
    """The texts to embed, in concept order. Batched by the store."""
    return [concept.embedding_text for concept in concepts]
