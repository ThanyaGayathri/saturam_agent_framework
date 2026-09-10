"""The suggestion pass: gather, run, assemble, file. DB-free with stand-ins.

What is verified here is the seam the review asked for -- connect, build the layer, *then
immediately suggest* -- with the model replaced by canned drafts, so every assertion is about
what the pass does with an answer rather than about the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.routing.table import ROUTING_TABLE, Tier
from egpt_retail.semantic.proposals import ProposalKind, validate_payload
from egpt_retail.skills import kpi_suggestion as k12
from egpt_retail.skills import use_case_suggestion as k13
from egpt_retail.suggestions import (
    KPI_SUGGESTION_ID,
    USE_CASE_SUGGESTION_ID,
    build_suggestion_agents,
    gather_context,
    run_suggestions,
)
from egpt_retail.suggestions.context import render_context

# --- stand-ins ------------------------------------------------------------------------


@dataclass(frozen=True)
class _Column:
    name: str
    data_type: str
    is_key: bool = False
    description: str = ""
    sampled_values: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _Table:
    columns: Tuple[_Column, ...]


@dataclass(frozen=True)
class _Ref:
    name: str
    schema: str = "silver"

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


class FakeReader:
    """The slice of SemanticRead the pass touches."""

    def __init__(self, tables: Dict[str, _Table], concepts: Dict[str, Any]):
        self._tables = tables
        self._concepts = concepts

    def snapshot(self):
        return SimpleNamespace(version="snap-9")

    def list_tables(self, *, include_proposed: bool = False):
        return tuple(_Ref(name) for name in sorted(self._tables))

    def get_table(self, name: str, *, include_proposed: bool = False):
        return self._tables.get(name)

    def list_concepts(self, *, include_proposed: bool = False):
        return tuple(sorted(self._concepts))

    def get_concept(self, name: str, *, include_proposed: bool = False):
        return self._concepts.get(name)


class FakeRegistry:
    def __init__(self, definitions):
        self._definitions = tuple(definitions)

    def list(self, **_):
        return self._definitions


class FakeProposals:
    def __init__(self):
        self.filed: List[Any] = []

    def propose_many(self, proposals):
        for proposal in proposals:
            validate_payload(proposal.kind, proposal.payload)
        self.filed.extend(proposals)
        return len(proposals)


class FakeAgent:
    """Returns a canned draft set and records what it was asked."""

    def __init__(self, content):
        self.content = content
        self.messages: List[str] = []

    def run(self, message: str):
        self.messages.append(message)
        return SimpleNamespace(content=self.content)


def _definition(identifier: str, table: str) -> KpiDefinition:
    return KpiDefinition(
        identifier=identifier,
        name=identifier,
        description="",
        sql=f"SELECT count(*) FROM {table}",
        grain="overall",
        business_area="sales",
        source_tables=frozenset({table}),
    )


READER = FakeReader(
    tables={
        "crm_deal": _Table(
            (
                _Column("deal_id", "text", True),
                _Column("amount", "numeric", description="deal value"),
            )
        ),
        "crm_contact": _Table((_Column("contact_id", "text", True),)),
    },
    concepts={
        "QuoteRequest": SimpleNamespace(
            description="A request for a quote.", business_area="sales"
        )
    },
)
REGISTRY = FakeRegistry([_definition("k__existing", "crm_deal")])

KPI_DRAFTS = k12.KpiSuggestionDraftSet(
    suggestions=[
        k12.KpiSuggestionDraft(
            identifier="quoterequest__count",
            name="Quote requests",
            description="How many.",
            business_area="sales",
            grain="overall",
            concept="QuoteRequest",
            result_shape="scalar",
            confidence="high",
            rationale="crm_deal.deal_id counts them.",
            derived_from_tables=["silver.crm_deal"],
            proposed_sql="SELECT count(DISTINCT deal_id) FROM crm_deal",
            catalogue_source="Quote request volume",
        ),
        k12.KpiSuggestionDraft(
            identifier="contact__reachable",
            name="Reachable contacts",
            description="Contacts with an email.",
            business_area="customer",
            grain="overall",
            concept=None,
            result_shape="scalar",
            confidence="medium",
            rationale="crm_contact has contact_id.",
            derived_from_tables=["crm_contact"],
            proposed_sql=None,
            catalogue_source=None,
        ),
    ],
    notes="",
)
USE_CASE_DRAFTS = k13.UseCaseSuggestionDraftSet(
    suggestions=[
        k13.UseCaseSuggestionDraft(
            title="Weekly pipeline value",
            description="Reports open pipeline weekly.",
            business_area="sales",
            shape="inform",
            autonomy="copilot",
            confidence="high",
            rationale="k__existing over crm_deal.",
            derived_from_tables=["crm_deal"],
            kpi_identifiers=["k__existing"],
        ),
        k13.UseCaseSuggestionDraft(
            title="Promo lift alert",
            description="Alerts when a promotion under-delivers.",
            business_area="marketing",
            shape="alert",
            autonomy="copilot",
            confidence="medium",
            rationale="needs the promotions table.",
            derived_from_tables=["promotions"],
            kpi_identifiers=[],
        ),
    ],
    notes="",
)


def _run(*, dry_run=False, proposals=None, knowledge=None):
    kpi_agent, use_case_agent = FakeAgent(KPI_DRAFTS), FakeAgent(USE_CASE_DRAFTS)
    run = run_suggestions(
        RetailSettings(retail_customer_slug="t"),
        reader=READER,
        registry=REGISTRY,
        proposals=proposals,
        knowledge=knowledge,
        agents=(kpi_agent, use_case_agent),
        dry_run=dry_run,
    )
    return run, kpi_agent, use_case_agent


# --- routing and identity -------------------------------------------------------------


def test_both_agents_are_routed_on_the_frontier_tier():
    """G6: suggestion on the frontier tier. Without a row neither is a deployable agent."""
    assert ROUTING_TABLE[KPI_SUGGESTION_ID] is Tier.FRONTIER
    assert ROUTING_TABLE[USE_CASE_SUGGESTION_ID] is Tier.FRONTIER


def test_agent_ids_match_the_skill_headers():
    """One string in three places -- the routing table, the agent id, the prompt header."""
    header = k12.PROMPT_PATH.read_text().split("```")[1]
    assert f"identifier:      {KPI_SUGGESTION_ID}" in header
    header = k13.PROMPT_PATH.read_text().split("```")[1]
    assert f"identifier:      {USE_CASE_SUGGESTION_ID}" in header


def test_the_builder_routes_rather_than_pinning_a_model():
    kpi_agent, use_case_agent = build_suggestion_agents(
        RetailSettings(retail_customer_slug="t")
    )
    for agent in (kpi_agent, use_case_agent):
        assert agent.model is None
        assert getattr(agent, "model_provider", None) is not None
    # Nothing registered was rendered into section 6's "already registered" line, and the
    # rendered prompt says so rather than leaving the placeholder or a blank.
    assert "never propose these:** (none" in kpi_agent.instructions


def test_the_builder_renders_the_live_registry_into_section_six():
    kpi_agent, use_case_agent = build_suggestion_agents(
        RetailSettings(retail_customer_slug="t"),
        existing_kpis=["k__existing"],
        registry_kpis=REGISTRY.list(),
    )
    assert "k__existing" in kpi_agent.instructions
    assert "k__existing" in use_case_agent.instructions


# --- the context ----------------------------------------------------------------------


def test_context_carries_harvest_concepts_and_the_ontology_area():
    context = gather_context(READER)
    assert context.tables == ("crm_contact", "crm_deal")
    assert "silver.crm_deal" in context.harvest_text
    assert "deal_id text [key]" in context.harvest_text
    assert "QuoteRequest" in context.concepts_text
    assert context.area_of_concept == {"QuoteRequest": "sales"}
    assert context.prose == []


def test_the_message_says_when_no_prose_reached_the_agent():
    """Silence about a missing input is how an agent quietly invents the input."""
    message = render_context(gather_context(READER), ask="Go.")
    assert "(no domain prose retrieved)" in message
    assert message.endswith("Go.")


def test_prose_is_retrieved_through_k10_and_only_the_kb_leg_is_kept():
    """The review's third input. Through unified retrieval, and only what the prose leg
    said -- the ontology and semantic legs echo what the agent already has."""
    calls: List[Any] = []

    class FakeSlice:
        def fused(self):
            return [
                SimpleNamespace(origin="kb", content="A quote request is..."),
                SimpleNamespace(origin="ontology", content="QuoteRequest"),
                SimpleNamespace(origin="kb", content="A quote request is..."),
            ]

    def fake_read(scope, query, **kwargs):
        calls.append((scope, query.text, sorted(k for k, v in kwargs.items() if v)))
        return FakeSlice()

    import egpt_retail.retrieval as retrieval

    original = retrieval.retail_unified_read
    retrieval.retail_unified_read = fake_read
    try:
        context = gather_context(READER, knowledge=object(), tenant_id="t")
    finally:
        retrieval.retail_unified_read = original
    assert context.prose == [("QuoteRequest", "A quote request is...")]
    scope, text, legs = calls[0]
    assert scope.tenant_id == "t" and scope.user_id == "__scheduler__"
    assert "QuoteRequest" in text
    assert "memory" not in legs


# --- the pass -------------------------------------------------------------------------


def test_a_dry_run_assembles_everything_and_files_nothing():
    run, kpi_agent, use_case_agent = _run(dry_run=True)
    assert [s.identifier for s in run.kpis.suggestions] == [
        "quoterequest__count",
        "contact__reachable",
    ]
    assert run.kpis.catalogue_source == {
        "quoterequest__count": "Quote request volume",
        "contact__reachable": None,
    }
    assert run.kpis.without_formula == ["contact__reachable"]
    assert len(run.use_cases.suggestions) == 2
    assert run.proposals_offered == 3  # one KPI carrying SQL, two use cases
    assert run.proposals_new == 0
    assert kpi_agent.messages and use_case_agent.messages


def test_proposals_are_filed_for_a_person_to_adopt():
    """D10's adopt action, which had no producer. Every payload validates against K7."""
    store = FakeProposals()
    run, *_ = _run(proposals=store)
    kinds = sorted(p.kind.value for p in store.filed)
    assert kinds == ["kpi", "use_case", "use_case"]
    assert run.proposals_new == 3 and run.proposals_offered == 3
    kpi = next(p for p in store.filed if p.kind is ProposalKind.KPI)
    assert kpi.target == "quoterequest__count"
    assert kpi.proposer == "kpi_suggestion@1.0.0"
    assert kpi.payload["catalogue_source"] == "Quote request volume"
    assert kpi.payload["feasibility"] == "ready"


def test_a_suggestion_without_a_formula_is_shown_but_not_filed():
    """A KPI proposal is the whole definition and adoption saves it unread. A placeholder
    formula would be adopted as a formula."""
    store = FakeProposals()
    run, *_ = _run(proposals=store)
    filed = {p.target for p in store.filed if p.kind is ProposalKind.KPI}
    assert "contact__reachable" not in filed
    assert "contact__reachable" in [s.identifier for s in run.kpis.suggestions]


def test_a_blocked_use_case_is_filed_with_what_would_unlock_it():
    store = FakeProposals()
    run, *_ = _run(proposals=store)
    promo = next(p for p in store.filed if p.target == "promo_lift_alert")
    assert promo.payload["feasibility"] == "blocked"
    assert promo.payload["missing_tables"] == ["promotions"]
    assert promo.payload["shape"] == "alert"
    assert run.use_cases.unlocks == {"promotions": ["Promo lift alert"]}


def test_filing_without_a_store_is_refused_unless_dry_run():
    with pytest.raises(ValueError, match="dry_run"):
        _run(dry_run=False, proposals=None)


def test_the_run_report_reads_as_the_discovery_screen_would():
    run, *_ = _run(dry_run=True)
    text = "\n".join(run.lines())
    assert "quoterequest__count" in text and "catalogue" in text
    assert "contact__reachable" in text and "inferred" in text
    assert "connect promotions to unlock 1" in text
    assert "no domain prose reached the agents" in text


def test_example_values_reach_the_message_only_when_a_caller_read_them_from_the_warehouse():
    """The graph never holds row values, so the layer alone cannot supply examples. A caller
    that read them from the warehouse (the entrypoint, the cookbooks) passes them in, and
    they reach the prompt and nothing else."""
    from egpt_retail.suggestions.context import samples_from_harvest

    bare = gather_context(READER)
    assert "e.g." not in bare.harvest_text
    with_samples = gather_context(
        READER, samples={("crm_deal", "amount"): ("12.50", "99.00", "1500")}
    )
    assert (
        "amount numeric -- deal value e.g. 12.50, 99.00, 1500"
        in with_samples.harvest_text
    )

    harvest = SimpleNamespace(
        tables=[
            SimpleNamespace(
                qualified="silver.crm_deal",
                columns=[SimpleNamespace(name="amount", sampled_values=(1, 2))],
            )
        ]
    )
    assert samples_from_harvest(harvest) == {("crm_deal", "amount"): ("1", "2")}
