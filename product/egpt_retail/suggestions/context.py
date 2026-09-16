"""What the suggestion agents are given: the layer, the ontology, and the domain prose.

The review is specific about the inputs -- *"KPI suggestion should take the domain knowledge
(doc and graph) and the semantic layer graph as inputs"* -- and the first cut supplied two of
the three. The harvest and the concepts came from the graph; the prose never arrived, because
nothing asked K10 for it. This module asks.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt.memory.types import MemoryQuery, Scope

from egpt_retail.contracts.semantic import SemanticRead, available_tables
from egpt_retail.memory.scopes import SYSTEM_USER_ID

__all__ = [
    "SuggestionContext",
    "agather_context",
    "gather_context",
    "render_context",
    "samples_from_harvest",
]


@dataclass
class SuggestionContext:
    """Everything both agents read, gathered once."""

    snapshot: str
    tables: Tuple[str, ...]
    harvest_text: str
    concepts_text: str
    #: Ontology concept -> the business area the ontology places it in. K9's lesson: an agent
    #: asked for this answers differently on different runs; the ontology has one answer.
    area_of_concept: Dict[str, str] = field(default_factory=dict)
    #: Domain prose passages K10 retrieved for this deployment's concepts, with their origin.
    prose: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def available_tables(self) -> frozenset:
        return frozenset(self.tables)


#: ``(table, column) -> example values``, read from the warehouse for this pass only.
Samples = Mapping[Tuple[str, str], Sequence[str]]


def _harvest_text(reader: SemanticRead, samples: Samples) -> str:
    """The layer's tables and columns, with example values where a caller supplied them.

    The graph never holds example values -- *rows stay in the warehouse* is one of the five
    constraints, and ``write_harvest`` strips ``sampled_values`` on every write to enforce it.
    So the layer alone tells an agent that ``quote_request_source_url`` is ``text``, and not
    that it holds URLs or nulls. K12's first baseline was measured with examples in the
    message; run without them the same agent proposed 2, 10 and 18 KPIs on three runs. The
    examples come from a transient read of the warehouse, the same way K4 samples at harvest,
    and reach the prompt and nothing else.
    """
    lines: List[str] = []
    for ref in reader.list_tables():
        table = reader.get_table(ref.name)
        if table is None:
            continue
        lines.append(f"{ref.qualified} ({len(table.columns)} columns)")
        for column in table.columns:
            bits = [f"    {column.name} {column.data_type}"]
            if column.is_key:
                bits.append("[key]")
            if column.description:
                bits.append(f"-- {column.description}")
            values = samples.get((ref.name, column.name)) or column.sampled_values
            if values:
                bits.append(f"e.g. {', '.join(str(v) for v in values[:3])}")
            lines.append(" ".join(bits))
    return "\n".join(lines)


def samples_from_harvest(harvest: Any) -> Dict[Tuple[str, str], Tuple[str, ...]]:
    """``(table, column) -> examples`` from a K4 ``Harvest`` taken with ``sample=True``."""
    out: Dict[Tuple[str, str], Tuple[str, ...]] = {}
    for table in getattr(harvest, "tables", ()):
        name = str(getattr(table, "qualified", "") or getattr(table, "name", "")).split(
            "."
        )[-1]
        for column in getattr(table, "columns", ()):
            values = tuple(
                str(v) for v in (getattr(column, "sampled_values", ()) or ())
            )
            if values:
                out[(name.lower(), column.name)] = values
    return out


def _concepts(reader: SemanticRead) -> Tuple[str, Dict[str, str]]:
    lines: List[str] = []
    areas: Dict[str, str] = {}
    for name in reader.list_concepts():
        concept = reader.get_concept(name)
        if concept is None:
            continue
        lines.append(f"- {name}: {getattr(concept, 'description', '') or ''}")
        area = getattr(concept, "business_area", None)
        if area:
            areas[name] = area
    return "\n".join(lines), areas


def _prose_for(
    reader: SemanticRead,
    *,
    knowledge: Any,
    ontology: Any,
    tenant_id: str,
    per_concept: int,
) -> List[Tuple[str, str]]:
    """One K10 read per concept, keeping the passages that came from the prose leg.

    Through ``retail_unified_read`` rather than the knowledge store directly, because that is
    the rule every capability in both lanes follows and because fusion is what ranks a prose
    passage that the ontology corroborates above one nothing else mentions. Memory is not
    read: a suggestion pass has no person present and its own prior runs are not context for
    what the data supports.
    """
    from egpt_retail.retrieval import retail_unified_read

    scope = Scope(
        tenant_id=tenant_id, agent_id="kpi_suggestion", user_id=SYSTEM_USER_ID
    )
    passages: List[Tuple[str, str]] = []
    seen: set = set()
    for name in reader.list_concepts():
        concept = reader.get_concept(name)
        question = f"{name}: {getattr(concept, 'description', '') or ''}".strip(": ")
        result = retail_unified_read(
            scope,
            MemoryQuery(text=question, limit=per_concept),
            knowledge=knowledge,
            ontology=ontology,
            semantic=reader,
        )
        for hit in result.fused():
            if hit.origin != "kb" or not hit.content.strip():
                continue
            key = hit.content.strip()[:200]
            if key in seen:
                continue
            seen.add(key)
            passages.append((name, hit.content.strip()))
    return passages


def gather_context(
    reader: SemanticRead,
    *,
    knowledge: Any = None,
    ontology: Any = None,
    tenant_id: str = "",
    prose_per_concept: int = 3,
    samples: Optional[Samples] = None,
) -> SuggestionContext:
    """Gather the three inputs. Prose is skipped, and said to be, when no store is given."""
    concepts_text, areas = _concepts(reader)
    prose: List[Tuple[str, str]] = []
    if knowledge is not None and tenant_id:
        prose = _prose_for(
            reader,
            knowledge=knowledge,
            ontology=ontology,
            tenant_id=tenant_id,
            per_concept=prose_per_concept,
        )
    return SuggestionContext(
        snapshot=reader.snapshot().version,
        tables=tuple(sorted(available_tables(reader))),
        harvest_text=_harvest_text(reader, samples or {}),
        concepts_text=concepts_text,
        area_of_concept=areas,
        prose=prose,
    )


async def agather_context(
    reader: SemanticRead,
    *,
    knowledge: Any = None,
    ontology: Any = None,
    tenant_id: str = "",
    prose_per_concept: int = 3,
    samples: Optional[Samples] = None,
) -> SuggestionContext:
    """Async variant. The graph and vector reads happen off the event loop."""
    return await asyncio.to_thread(
        gather_context,
        reader,
        knowledge=knowledge,
        ontology=ontology,
        tenant_id=tenant_id,
        prose_per_concept=prose_per_concept,
        samples=samples,
    )


def render_focus(focus_areas: Optional[Sequence[str]]) -> str:
    """The paragraph that tells an agent where the customer's attention is. Empty when no
    focus was given, so the message is exactly what it was before focus existed.

    Worded as a priority, not a filter: the agent still proposes anywhere the data clearly
    supports a metric, but spends its output on the named areas first. What the customer
    chose on the discovery page is the only thing that varies here; everything above it in
    the message is the deployment's data, the same on every run."""
    areas = [area for area in dict.fromkeys(focus_areas or ()) if area]
    if not areas:
        return ""
    return (
        "focus: the customer cares most about these business areas: "
        f"{', '.join(areas)}. Propose for them first and most thoroughly; propose for "
        "another area only when the data clearly supports a metric there.\n\n"
    )


def render_request(request: Optional[str]) -> str:
    """The paragraph that carries what the customer asked for, in their own words (contract
    1.2.3, the discovery page's "Add KPI" -> AI generated). Empty when there is none, so the
    message is exactly what it was before requests existed.

    Like :func:`render_focus`, a steer and not a filter: the agent answers the request first,
    but everything it proposes is still judged, stamped and checked downstream as an
    unprompted proposal would be. Quoted so the agent reads it as the customer's words, not
    as an instruction from the deployment; the skill's own rules say what to do with it."""
    text = " ".join((request or "").split())
    if not text:
        return ""
    return (
        "request: the customer asked, in their own words: "
        f'"{text}". Propose what answers this first and most thoroughly, and say in each '
        "rationale how it does. If the harvest cannot support what was asked, do not invent "
        "a metric for it: say so in notes, and still propose what the data does support.\n\n"
    )


def render_context(
    context: SuggestionContext,
    *,
    ask: str,
    focus_areas: Optional[Sequence[str]] = None,
    request: Optional[str] = None,
) -> str:
    """The message both agents receive: harvest, concepts, prose, the focus, the customer's
    request (if any), then the ask."""
    prose = (
        "\n\n".join(f"[{concept}] {text}" for concept, text in context.prose)
        or "(no domain prose retrieved)"
    )
    return (
        f"harvest:\n{context.harvest_text or '(no tables)'}\n\n"
        f"concepts in this deployment:\n{context.concepts_text or '(none)'}\n\n"
        f"domain prose, retrieved for those concepts:\n{prose}\n\n"
        f"{render_focus(focus_areas)}"
        f"{render_request(request)}"
        f"{ask}"
    )
