"""G21 -- the semantic layer rendered into chat-with-data's instructions.

`build-plan.md` section 08's first bullet: the sub-agent's instructions carry a schema name and
nothing else, so the model sees raw column names with no concepts, synonyms, scoping filters or
authoritative source. These assert what reaches the prompt.

Pure: the renderer takes a `SemanticRead` and returns text, so every case here runs against the
contract fixture with no graph, no warehouse and no model.
"""

import asyncio

from egpt_retail.contracts.fixture_builder import CONTRACT_FIXTURE_PATH
from egpt_retail.contracts.semantic import Concept
from egpt_retail.contracts.semantic_stub import StubSemanticRead
from egpt_retail.playground.grounding import (
    DEFAULT_MAX_CONCEPTS,
    agrounded_read_instructions,
    agrounding_block,
    grounded_read_instructions,
    grounding_block,
    render_concept,
)


def _reader() -> StubSemanticRead:
    return StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)


def test_the_block_carries_what_a_schema_name_cannot():
    """Synonyms, the scoping filter and the table list -- the four things G21 names."""
    block = grounding_block(_reader())
    assert "CustomerSegment" in block
    assert "segment, cohort" in block, (
        "synonyms are the vocabulary the customer actually uses"
    )
    assert "c.segment = {segment_name}" in block, (
        "the scoping filter must reach the prompt"
    )
    assert "silver.orders" in block


def test_a_concept_renders_its_scoping_filter_and_authoritative_table():
    concept = Concept(
        name="QuoteRequest",
        description="A request for a price.",
        synonyms=("RFQ", "enquiry"),
        sql_filter="d.stage <> 'test'",
        personal_data_fields=("crm_contact.email",),
    )
    rendered = render_concept(concept, authoritative_table="silver.crm_deal")
    assert "Also called: RFQ, enquiry" in rendered
    assert "Always scope with: d.stage <> 'test'" in rendered
    assert "Authoritative table: silver.crm_deal" in rendered
    assert "Personal data, do not select: crm_contact.email" in rendered


def test_an_empty_field_is_omitted_rather_than_rendered_empty():
    """ "Synonyms: none" costs tokens on every request and tells the model nothing."""
    rendered = render_concept(Concept(name="Bare", description=""))
    assert rendered == "- Bare"


def test_instructions_keep_the_schema_placeholder_and_the_read_only_rule():
    """Appended, not replaced: the provider substitutes `{schema}` and the base text carries
    the read-only posture G21 does not revisit."""
    instructions = grounded_read_instructions(_reader())
    assert "{schema}" in instructions, "the provider substitutes this itself"
    assert "read-only" in instructions, (
        "replacing the base text would drop this silently"
    )
    assert "## The semantic layer" in instructions


def test_a_reader_with_nothing_accepted_renders_no_block():
    """A heading with nothing under it reads as "the semantic layer says nothing about this
    database", which is a stronger and more wrong claim than saying nothing."""

    class _Empty:
        def list_concepts(self, *, include_proposed: bool = False):
            return ()

        def list_tables(self, *, include_proposed: bool = False):
            return ()

        def get_concept(self, name, *, include_proposed: bool = False):
            return None

        def authoritative_source(self, concept):
            return None

    assert grounding_block(_Empty()) == ""


def test_truncation_says_so_in_the_text_the_model_reads():
    """A model that knows its grounding is partial can say "I am not sure that is the right
    table"; one that does not, cannot."""
    reader = _reader()
    block = grounding_block(reader, max_concepts=1)
    assert "This list is partial" in block
    assert len(block) < len(grounding_block(reader))


def test_the_default_cap_is_not_hit_by_the_shipped_fixture():
    """A cap that bites on ordinary input is a cap set wrong."""
    assert len(_reader().list_concepts()) < DEFAULT_MAX_CONCEPTS


def test_output_is_byte_identical_across_runs():
    """Prompt caching needs a stable prefix, and a block nobody can diff is one nobody reviews."""
    assert grounding_block(_reader()) == grounding_block(_reader())


def test_async_twins_agree_with_the_sync_ones():
    """The repository's standing rule is both variants; this is what makes it more than a
    signature. `asyncio.run` rather than a plugin marker, matching test_k1_memory_wiring.py."""
    reader = _reader()

    async def _both():
        return await agrounding_block(reader), await agrounded_read_instructions(reader)

    block, instructions = asyncio.run(_both())
    assert block == grounding_block(reader)
    assert instructions == grounded_read_instructions(reader)
