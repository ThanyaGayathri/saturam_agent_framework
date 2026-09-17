"""G21 -- the semantic layer, rendered into the instructions chat-with-data runs under.

``DatabaseContextProvider`` answers natural language over a schema through a sub-agent bound
to a read-only engine, and its instructions carry **a schema name and nothing else**
(``DEFAULT_READ_INSTRUCTIONS``: "You answer questions about data in ``{schema}``"). So the
model sees ``crm_deal`` and two columns called ``amount`` and has no way to learn that one is
a quote and the other a won deal, that this customer says "RFQ" for a quote request, or that
every quote query is supposed to carry a scoping filter. The build plan's G21 is that gap, and
names it "the difference between a text-to-SQL demo and a system that answers in the
customer's own vocabulary".

**Why this is product and not framework.** The framework already has the seam: the provider
takes ``read_instructions`` and substitutes ``{schema}`` into whatever it is handed, so
nothing in ``libs/agno/`` has to change for a deployment to ground its sub-agent. What does
not generalise is the *source* of the grounding -- concepts, synonyms, scoping filters and an
authoritative source are the retail semantic layer's own vocabulary (``contracts/semantic.py``,
D11). A non-retail deployment would want the seam, which it already has, not this renderer.

**Accepted rows only.** Every read here leaves ``include_proposed`` at its default, per the
K-1 decision: a proposed synonym is one nobody has reviewed, and the whole point of the
proposal gate is that an unreviewed enrichment must not silently change the meaning of an
answer. The review screen is the one caller that passes ``include_proposed=True``, and it is
not this one.

**Deterministic output.** Concepts and their fields render in a stable order, so re-running
against an unchanged graph produces a byte-identical block. Two reasons, both practical: a
prompt that reshuffles on every request defeats prompt caching, and a grounding block nobody
can diff is one nobody will review when an answer starts coming out wrong.

**Bounded, and it says when it truncated.** The block is capped (:data:`DEFAULT_MAX_CONCEPTS`,
:data:`DEFAULT_MAX_CHARS`) because it is *instructions*, not retrieved context: it rides on
every request in the mode. R8 routes chat-with-data to the small tier and R8's escalation
reads the request's shape **before** the call, so an instruction block that grows with the
customer's schema would quietly push every question onto the frontier tier -- the cost
decision made by accident rather than by the routing table. When the cap bites, the block says
so in the text the model reads, rather than trailing off, because a model that knows its
grounding is partial can say "I am not sure that is the right table"; one that does not, cannot.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from egpt_retail.contracts.semantic import Concept, ProposalStatus, SemanticRead

__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_CONCEPTS",
    "agrounded_read_instructions",
    "agrounding_block",
    "grounded_read_instructions",
    "grounding_block",
    "render_concept",
]

#: How many concepts may reach the block. Enough for a business area's worth of vocabulary,
#: small enough that the instructions stay a preamble rather than the payload.
DEFAULT_MAX_CONCEPTS = 40

#: Hard ceiling on the rendered block, applied after the concept cap. A single concept with
#: forty synonyms would otherwise clear the count cap and blow the size budget on its own.
DEFAULT_MAX_CHARS = 12_000

_HEADING = "## The semantic layer"

_PREAMBLE = (
    "The tables below have reviewed meanings attached. Use them: answer in the "
    "vocabulary named here, apply the scoping filter a concept declares, and prefer "
    "the authoritative table when two could serve. If a question names something "
    "absent from this list, say so rather than guessing at a column."
)

_TRUNCATION_NOTE = (
    "This list is partial -- more concepts exist than are shown. If a question names "
    "something that is not here, do not assume it is absent from the database; say the "
    "grounding did not cover it."
)


def _bullet(label: str, values: Sequence[str]) -> Optional[str]:
    """One rendered bullet, or ``None`` when there is nothing to say.

    An empty field is omitted rather than rendered as "Synonyms: none". A line saying a
    concept has no synonyms costs tokens on every request and tells the model nothing it
    could act on.
    """
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return None
    return "  - " + label + ": " + ", ".join(cleaned)


def _accepted_source(source: Any) -> Optional[str]:
    """The authoritative table, but only when review accepted it.

    Unlike ``list_concepts``/``get_concept``/``list_tables``, ``authoritative_source`` takes no
    ``include_proposed`` and its Cypher does not filter on status -- it returns the status as
    data instead. So a *proposed*, unreviewed ``AUTHORITATIVE_SOURCE`` edge would otherwise
    steer which table the SQL sub-agent prefers, against this module's own "accepted rows only"
    claim and the alignment rule behind it. One table name is a small blast radius until the
    proposed edge points somewhere it should not.
    """
    if source is None:
        return None
    status = getattr(source, "status", None)
    if status is not None and status != ProposalStatus.ACCEPTED:
        return None
    return getattr(source, "table", None)


def render_concept(concept: Concept, authoritative_table: Optional[str] = None) -> str:
    """One concept as the model sees it.

    Pure: no I/O, so the sync/async rule does not apply (the construction exemption in
    ``CLAUDE.md``). ``authoritative_table`` is passed in rather than looked up, because
    resolving it is a graph read and this function is the part that has to stay testable
    without one.

    Personal-data fields and compliance tags are rendered as a caution rather than withheld.
    Withholding them would leave the model free to select a column it was never meant to put
    in an answer; naming them is what lets it avoid them.
    """
    lines: List[str] = ["- " + concept.name]
    description = (concept.description or "").strip()
    if description:
        lines.append("  - Means: " + description)

    for label, values in (
        ("Also called", concept.synonyms),
        ("Typical questions", concept.question_patterns),
        ("Metrics", concept.metrics),
    ):
        bullet = _bullet(label, values)
        if bullet is not None:
            lines.append(bullet)

    scoping_filter = (concept.sql_filter or "").strip()
    if scoping_filter:
        lines.append("  - Always scope with: " + scoping_filter)

    if authoritative_table:
        lines.append("  - Authoritative table: " + authoritative_table)

    caution = _bullet("Personal data, do not select", concept.personal_data_fields)
    if caution is not None:
        lines.append(caution)
    tags = _bullet("Compliance tags", concept.compliance_tags)
    if tags is not None:
        lines.append(tags)

    return "\n".join(lines)


def _assemble(
    table_names: Sequence[str],
    rendered: Sequence[str],
    truncated: bool,
    max_chars: int,
) -> str:
    """Join the parts, then enforce the character ceiling by dropping whole concepts.

    Dropping whole concepts rather than slicing the string is deliberate: a block cut
    mid-bullet can leave a concept showing a synonym list that reads as complete and is not,
    which is a worse failure than the concept being absent.
    """
    header = [_HEADING, "", _PREAMBLE, ""]
    if table_names:
        header.append("Tables: " + ", ".join(sorted(table_names)))
        header.append("")

    kept: List[str] = []
    budget = max_chars - len("\n".join(header)) - len(_TRUNCATION_NOTE) - 2
    for block in rendered:
        if budget - (len(block) + 1) < 0:
            truncated = True
            break
        kept.append(block)
        budget -= len(block) + 1

    body = header + kept
    if truncated:
        body += ["", _TRUNCATION_NOTE]
    return "\n".join(body).rstrip() + "\n"


def grounding_block(
    reader: SemanticRead,
    *,
    max_concepts: int = DEFAULT_MAX_CONCEPTS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """The semantic layer as an instruction block. Empty string when there is nothing to say.

    An empty return is the honest answer for a deployment whose graph holds no accepted
    concepts yet -- a heading with no content under it reads to the model as "the semantic
    layer says nothing about this database", which is a stronger and more wrong claim than
    saying nothing at all.
    """
    concept_names = list(reader.list_concepts())
    truncated = len(concept_names) > max_concepts
    selected = sorted(concept_names)[:max_concepts]

    rendered: List[str] = []
    for name in selected:
        concept = reader.get_concept(name)
        if concept is None:
            continue
        source = _accepted_source(reader.authoritative_source(name))
        rendered.append(render_concept(concept, source))

    table_names = [ref.qualified for ref in reader.list_tables()]
    if not rendered and not table_names:
        return ""
    return _assemble(table_names, rendered, truncated, max_chars)


async def agrounding_block(
    reader: SemanticRead,
    *,
    max_concepts: int = DEFAULT_MAX_CONCEPTS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Async twin of :func:`grounding_block`."""
    concept_names = list(await reader.alist_concepts())
    truncated = len(concept_names) > max_concepts
    selected = sorted(concept_names)[:max_concepts]

    rendered: List[str] = []
    for name in selected:
        concept = await reader.aget_concept(name)
        if concept is None:
            continue
        source = _accepted_source(await reader.aauthoritative_source(name))
        rendered.append(render_concept(concept, source))

    table_names = [ref.qualified for ref in await reader.alist_tables()]
    if not rendered and not table_names:
        return ""
    return _assemble(table_names, rendered, truncated, max_chars)


def _compose(base: str, block: str) -> str:
    """Append the block to the provider's own instructions.

    Appended rather than replacing: ``DEFAULT_READ_INSTRUCTIONS`` carries the read-only
    posture and the "say so plainly when the data does not exist" rule, which G21 does not
    revisit and which a replacement would silently drop. ``{schema}`` is left in place for
    the provider to substitute -- this function never sees the schema name.
    """
    if not block:
        return base
    return base.rstrip() + "\n\n" + block


def grounded_read_instructions(
    reader: SemanticRead,
    base_instructions: Optional[str] = None,
    *,
    max_concepts: int = DEFAULT_MAX_CONCEPTS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """What to pass as ``DatabaseContextProvider(read_instructions=...)``.

    Still carries ``{schema}``; the provider substitutes it at sub-agent construction.
    """
    from egpt.integrations.context.database.provider import DEFAULT_READ_INSTRUCTIONS

    base = DEFAULT_READ_INSTRUCTIONS if base_instructions is None else base_instructions
    return _compose(
        base, grounding_block(reader, max_concepts=max_concepts, max_chars=max_chars)
    )


async def agrounded_read_instructions(
    reader: SemanticRead,
    base_instructions: Optional[str] = None,
    *,
    max_concepts: int = DEFAULT_MAX_CONCEPTS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Async twin of :func:`grounded_read_instructions`."""
    from egpt.integrations.context.database.provider import DEFAULT_READ_INSTRUCTIONS

    base = DEFAULT_READ_INSTRUCTIONS if base_instructions is None else base_instructions
    block = await agrounding_block(
        reader, max_concepts=max_concepts, max_chars=max_chars
    )
    return _compose(base, block)
