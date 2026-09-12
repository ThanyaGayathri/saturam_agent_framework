"""K2 prose chunking and K3 ontology: the pure, dependency-free logic (round-2 review finding).

No database, no embedder, no Neo4j -- everything here is a plain function or dataclass. Before
this file the only verification for this surface was ``cookbook/94_retail/K02_domain_prose.py``
and ``K03_ontology.py``, neither of which runs in CI (checked directly: not in ``ci.yml`` or
``integration.yml``). This does not replace those cookbooks -- they still prove the DB/embedder-
dependent paths (Neo4j writes, pgvector ingest, real embedding-space routing) that a unit test
cannot reach without infrastructure. It closes the part of the gap that needed no infrastructure
at all.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from egpt_retail.knowledge.prose import (
    CHUNK_SIZE,
    SPLIT_ON_HEADINGS,
    BodiedChunking,
    has_body,
    read_corpus,
    starts_at_heading,
)
from egpt_retail.ontology.model import Concept, Ontology, Relation, merge_ontology
from egpt_retail.contracts.vocabulary import TermOrigin
from egpt_retail.ontology.model import embedding_texts as _embedding_texts


# ---------------------------------------------------------------------------
# prose.py: has_body / starts_at_heading
# ---------------------------------------------------------------------------


def test_has_body_true_for_a_heading_with_content_beneath_it():
    assert (
        has_body("## 4. The Category Captain Model\n\nSome real content here.") is True
    )


def test_has_body_false_for_a_bare_heading_shell():
    """The exact failure mode this function exists to catch (module docstring): a chunk
    whose entire content is subsections' heading lines, none of which is body text."""
    assert has_body("## 12. Key Metrics and Benchmarks") is False


def test_has_body_false_for_heading_plus_only_more_headings():
    assert has_body("## Section\n### Subsection A\n### Subsection B") is False


def test_starts_at_heading_true_for_a_chunk_beginning_with_a_heading():
    assert starts_at_heading("## First Heading\n\nBody text.") is True


def test_starts_at_heading_false_for_prose_before_any_heading():
    """The gap this function closes: a chunk with real content (has_body would keep it) that
    nonetheless does not start at a heading -- the exact shape a customer document with
    preamble text could produce, per this PR's own round-2 review finding."""
    content = "Some introductory prose with no heading above it at all."
    assert has_body(content) is True
    assert starts_at_heading(content) is False


def test_starts_at_heading_false_for_empty_content():
    assert starts_at_heading("") is False
    assert starts_at_heading("   \n  ") is False


def test_starts_at_heading_false_for_an_indented_hash_inside_a_code_block():
    """CommonMark makes four-space-indented text a code block, so this is a Python comment,
    not a heading. The heading regex used to carry a leading ``\\s*`` and ``starts_at_heading``
    used to ``lstrip()``, between them accepting a chunk that starts mid-code-block as
    heading-led (review round 1 of PR #204)."""
    assert starts_at_heading("    # this is a python comment\n    print(1)\n") is False


def test_starts_at_heading_true_after_leading_blank_lines():
    """Blank lines before the heading are not content, and the splitter can leave them."""
    assert starts_at_heading("\n\n## Section\n\nBody.") is True


def test_has_body_keeps_indented_code_that_only_looks_like_headings():
    """The same regex strips the chunk's headings before asking whether anything is left. With
    the old leading ``\\s*`` an indented comment was stripped as a heading, so a chunk whose
    body is entirely commented code read as an empty shell and was dropped."""
    assert has_body("## Example\n\n    # count the rows first\n") is True


# ---------------------------------------------------------------------------
# prose.py: BodiedChunking.chunk -- the production entry point, not the predicates
# ---------------------------------------------------------------------------


class _FakeInner:
    """Stands in for ``MarkdownChunking`` so ``.chunk()`` is testable with no ``unstructured``
    and no corpus. ``BodiedChunking`` only ever calls ``inner.chunk(document)`` and reads
    ``chunk.content``, so this is the whole contract it depends on."""

    def __init__(self, contents):
        self.contents = list(contents)

    def chunk(self, document):
        return [SimpleNamespace(content=content) for content in self.contents]


def test_bodied_chunking_keeps_only_chunks_that_are_bodied_and_heading_led():
    """The two predicates are tested individually above; this pins that ``chunk()`` applies
    **both** to **each** chunk. An ``or`` where the code has ``and`` keeps the shell and the
    pre-heading prose, and only this test sees it."""
    heading_with_body = "## 4. Category Captains\n\nReal prose about captains."
    shell = "## 12. Key Metrics and Benchmarks"
    preamble = "Introductory prose with no heading above it at all."
    chunker = BodiedChunking(_FakeInner([preamble, heading_with_body, shell]))

    kept = chunker.chunk(SimpleNamespace(content="ignored"))

    assert [chunk.content for chunk in kept] == [heading_with_body]
    assert chunker.dropped == 2


def test_bodied_chunking_dropped_accumulates_across_documents():
    """``dropped`` is a per-instance running total, and ``build_chunker()`` returns one
    instance used for the whole corpus -- so a caller that wants to report how much was
    dropped reads it once at the end, not per document."""
    chunker = BodiedChunking(_FakeInner(["## Shell"]))
    chunker.chunk(SimpleNamespace(content="a"))
    chunker.chunk(SimpleNamespace(content="b"))
    assert chunker.dropped == 2


def test_bodied_chunking_keeps_everything_when_nothing_is_droppable():
    chunker = BodiedChunking(_FakeInner(["# Title\n\nBody.", "## Two\n\nMore body."]))
    assert len(chunker.chunk(SimpleNamespace(content="x"))) == 2
    assert chunker.dropped == 0


# ---------------------------------------------------------------------------
# prose.py: the corpus measurements the module docstring asserts in prose
# ---------------------------------------------------------------------------

CORPUS = Path(__file__).resolve().parents[1] / "egpt_retail" / "knowledge" / "corpus"


def test_the_size_cap_claim_in_the_prose_docstring_is_true_of_the_real_corpus():
    """``prose.py``'s docstring says the 12,000-character cap binds: 18 raw sections across 13
    of the 31 corpus files exceed it, up to 22,604 characters. It said *8* files for two review
    rounds, because nothing executed the claim. This does.

    Measured through ``MarkdownChunking._split_by_headings`` -- the real splitter, deliberately,
    not a re-implementation of it: a re-implementation can agree with the docstring while both
    disagree with production.
    """
    pytest.importorskip(
        "unstructured", reason="MarkdownChunking imports unstructured at module scope"
    )
    from egpt.knowledge.chunking.markdown import MarkdownChunking

    documents = read_corpus(CORPUS)
    assert len(documents) == 31, "the docstring's denominator"

    splitter = MarkdownChunking(
        split_on_headings=SPLIT_ON_HEADINGS, chunk_size=CHUNK_SIZE
    )
    oversized = [
        (document.name, len(section))
        for document in documents
        for section in splitter._split_by_headings(document.path.read_text())
        if len(section) > CHUNK_SIZE
    ]

    assert len(oversized) == 18
    assert len({name for name, _ in oversized}) == 13
    assert max(length for _, length in oversized) == 22_604


def test_raising_the_cap_to_20k_really_does_change_the_corpus_chunk_count():
    """The other half of the same claim: 1,457 chunks become 1,436 at a 20,000 cap (1,049 and
    1,028 respectively once ``BodiedChunking`` has dropped the shells). If the cap never bound,
    both numbers would be equal -- which is precisely what the claim this PR corrects asserted
    before it was measured."""
    pytest.importorskip(
        "unstructured", reason="MarkdownChunking imports unstructured at module scope"
    )
    from egpt.knowledge.document import Document

    from egpt_retail.knowledge.prose import build_chunker

    documents = [
        Document(content=document.path.read_text(), name=document.name)
        for document in read_corpus(CORPUS)
    ]

    counts = {}
    for cap in (CHUNK_SIZE, 20_000):
        chunker = build_chunker(chunk_size=cap)
        kept = sum(len(chunker.chunk(document)) for document in documents)
        counts[cap] = (kept + chunker.dropped, kept)

    assert counts[CHUNK_SIZE] == (1_457, 1_049)
    assert counts[20_000] == (1_436, 1_028)


# ---------------------------------------------------------------------------
# ontology/model.py: Concept.embedding_text / embedding_texts
# ---------------------------------------------------------------------------


def _concept(**overrides):
    defaults = dict(
        name="Product",
        description="A thing a customer can buy.",
        business_area="merchandising",
    )
    defaults.update(overrides)
    return Concept(**defaults)


def test_embedding_text_is_name_and_description_alone_with_no_synonyms():
    concept = _concept(synonyms=(), question_patterns=())
    assert concept.embedding_text == "Product\nA thing a customer can buy."


def test_embedding_text_includes_synonyms_but_name_alone_would_not():
    """The mechanism the PR's own mutation-testing claim relies on (RFQ -> Channel, SKU ->
    Location): a synonym reaches the embedding only through embedding_text, never through the
    name alone. This does not reproduce the claimed embedding-space misrouting itself (that
    needs a live embedder -- see K03_ontology.py) but does prove the necessary precondition:
    the mutated ("name alone") input genuinely differs from the real one."""
    concept = _concept(synonyms=("RFQ", "Quote Request"))
    full_text = concept.embedding_text
    name_alone = concept.name
    assert "RFQ" in full_text
    assert "RFQ" not in name_alone


def test_embedding_text_includes_question_patterns():
    concept = _concept(question_patterns=("what items can we sell",))
    assert "what items can we sell" in concept.embedding_text


def test_embedding_texts_preserves_concept_order():
    concepts = [
        _concept(name="Product", description="A thing sold."),
        _concept(name="Order", description="A request to buy."),
        _concept(name="Customer", description="Who buys."),
    ]
    assert _embedding_texts(concepts) == [c.embedding_text for c in concepts]


# ---------------------------------------------------------------------------
# ontology/model.py: Ontology.synonym_index
# ---------------------------------------------------------------------------


def test_synonym_index_maps_synonyms_to_their_concept():
    ontology = Ontology(
        version="1",
        concepts=(
            _concept(name="Product", synonyms=("Item", "SKU")),
            _concept(name="Order", description="A request to buy.", synonyms=("PO",)),
        ),
    )
    index = ontology.synonym_index()
    assert index["item"] == "Product"
    assert index["sku"] == "Product"
    assert index["po"] == "Order"


def test_synonym_index_reserves_a_camel_case_names_spaced_form_too():
    """ "ProductCategory reserves 'product category' as well" (docstring): a synonym on a
    *different* concept that collides with either the written or the spaced form of an
    existing name is refused the same way an exact-name collision is -- this is a reservation
    the name makes, not a key ``synonym_index()`` returns for the concept's own name."""
    ontology = Ontology(
        version="1",
        concepts=(
            _concept(name="ProductCategory", description="A grouping."),
            _concept(
                name="Merchandise",
                description="Stuff for sale.",
                synonyms=("product category",),
            ),
        ),
    )
    with pytest.raises(ValueError, match="is the name of concept"):
        ontology.synonym_index()


def test_synonym_index_refuses_a_synonym_claimed_by_two_concepts():
    """Two concepts naming the same word -- the RFQ/SKU-style collision class this PR's
    description says review caught (`source`, `offer`, `range`, `promotion`)."""
    ontology = Ontology(
        version="1",
        concepts=(
            _concept(
                name="Supplier", description="Who sells to us.", synonyms=("source",)
            ),
            _concept(
                name="Channel", description="Where we sell.", synonyms=("source",)
            ),
        ),
    )
    with pytest.raises(ValueError, match="claimed by both"):
        ontology.synonym_index()


def test_synonym_index_refuses_a_synonym_that_is_another_concepts_name():
    ontology = Ontology(
        version="1",
        concepts=(
            _concept(
                name="Campaign",
                description="A marketing push.",
                synonyms=("promotion",),
            ),
            _concept(name="Promotion", description="A price discount.", synonyms=()),
        ),
    )
    with pytest.raises(ValueError, match="is the name of concept"):
        ontology.synonym_index()


# ---------------------------------------------------------------------------
# ontology/model.py: merge_ontology
# ---------------------------------------------------------------------------


def _ontology(*concepts, relations=(), version="1"):
    return Ontology(version=version, concepts=tuple(concepts), relations=relations)


def test_merge_ontology_adds_a_new_overlay_concept_tagged_overlay():
    core = _ontology(_concept(name="Product"))
    overlay = _ontology(
        _concept(
            name="RepGroup", description="A sales rep group.", business_area="sales"
        )
    )
    merged = merge_ontology(core, overlay)
    added = merged.concept("RepGroup")
    assert added is not None
    assert added.origin is TermOrigin.OVERLAY
    assert merged.concept("Product").origin is TermOrigin.CORE


def test_merge_ontology_annotates_by_unioning_synonyms_without_duplicating():
    core = _ontology(_concept(name="Product", synonyms=("Item",)))
    overlay = _ontology(_concept(name="Product", synonyms=("Item", "SKU")))
    merged = merge_ontology(core, overlay)
    product = merged.concept("Product")
    assert product.synonyms == ("Item", "SKU")
    assert product.origin is TermOrigin.CORE  # annotation, not a re-add


def test_merge_ontology_annotates_question_patterns_additively():
    core = _ontology(_concept(name="Product", question_patterns=("what do we sell",)))
    overlay = _ontology(
        _concept(name="Product", question_patterns=("what items exist",))
    )
    merged = merge_ontology(core, overlay)
    assert merged.concept("Product").question_patterns == (
        "what do we sell",
        "what items exist",
    )


def test_merge_ontology_refuses_a_redefined_description():
    core = _ontology(_concept(name="Supplier", description="Who sells to us."))
    overlay = _ontology(
        _concept(name="Supplier", description="A totally different thing.")
    )
    with pytest.raises(ValueError, match="redefines the description"):
        merge_ontology(core, overlay)


def test_merge_ontology_refuses_a_moved_business_area():
    core = _ontology(_concept(name="Supplier", business_area="merchandising"))
    overlay = _ontology(
        _concept(
            name="Supplier",
            description="A thing a customer can buy.",
            business_area="sales",
        )
    )
    with pytest.raises(ValueError, match="from business area"):
        merge_ontology(core, overlay)


def test_merge_ontology_unions_new_relations_tagged_overlay_and_keeps_core_relations_untouched():
    core_relation = Relation(type="SELLS", source="Supplier", target="Product")
    overlay_relation = Relation(type="REPRESENTS", source="RepGroup", target="Product")
    core = _ontology(
        _concept(name="Supplier"),
        _concept(name="Product", description="A thing a customer can buy."),
        relations=(core_relation,),
    )
    overlay = _ontology(
        _concept(
            name="RepGroup", description="A sales rep group.", business_area="sales"
        ),
        relations=(overlay_relation,),
    )
    merged = merge_ontology(core, overlay)
    assert core_relation in merged.relations
    assert core_relation.origin is TermOrigin.CORE
    added = [r for r in merged.relations if r.type == "REPRESENTS"][0]
    assert added.origin is TermOrigin.OVERLAY


def test_merge_ontology_does_not_duplicate_a_relation_the_core_already_has():
    shared = Relation(type="SELLS", source="Supplier", target="Product")
    core = _ontology(
        _concept(name="Supplier"),
        _concept(name="Product", description="A thing a customer can buy."),
        relations=(shared,),
    )
    overlay = _ontology(
        _concept(name="Supplier"),
        _concept(name="Product", description="A thing a customer can buy."),
        relations=(shared,),
    )
    merged = merge_ontology(core, overlay)
    assert len([r for r in merged.relations if r.type == "SELLS"]) == 1
