"""The real golden sets: one per agent skill that exists in ``egpt_retail.skills`` today.

G13's framing is explicit about scope: "a golden set per agent skill, run on publish of that
agent component, using the same scorers" -- not only the RFQ use case. ``product/egpt_retail/
skills/`` holds nine agent-backed skills as of this package:

- **The seven named in SKILL_TEMPLATE.md** as "the agents in the system": ``enrichment_proposer``
  and ``mapping_proposer`` (K8), ``kpi_suggestion`` (K12), ``use_case_suggestion`` (K13),
  ``spec_extractor`` (R9), ``funnel_explanation`` (R10), ``playground`` (R11). Every one of
  these is scored here.
- **``synonym_proposer`` and ``join_path_explainer``**, K14's own template-verification agents
  (``cookbook/94_retail/K14_skill_template.py`` / ``K14_join_path_explainer.py``). Real, shipped
  skills with real prompts and real schemas -- not hypothetical -- so they get golden sets too;
  ``join_path_explainer``'s is also this package's Verify scenario (see the R15 cookbook).

Not scored here: ``step_prompt`` (K15). It authors *other* skills' role-template prompts at
compile time rather than answering a retail question, and it ships no worked "correct answer"
a golden question could check against -- its own verification is K15's compiler diff, not a
golden set. If it grows a stable input/output shape later, it gets one like any other skill.

Each skill exposes ``build_<skill>_agent(model) -> Agent`` (never called in a loop -- built
once per golden-set run, exactly like every cookbook in this repository) and
``<skill>_golden_cases(agent) -> Tuple[Case, ...]``. Every case sets ``criteria`` (judged by
``AgentAsJudgeEval``, via ``egpt.eval``) against the standing rules in SKILL_TEMPLATE.md section
5 -- closed sets, traceability, never deciding feasibility or permission -- plus whatever is
specific to the skill. Where the skill ships its own deterministic checker
(``verdict_for_path``, ``movers_match_input``, ``citations_within_input``,
``closed_sets_match_prompt``), a ``teardown`` re-runs it against the real output and fails the
case on disagreement -- the same two-layer check (model-graded criteria plus a hard oracle)
K14's own cookbooks used by hand, now wired through the reusable runner instead.

Inputs are built from the shipped retail ontology and the R3 contract fixture
(``egpt_retail.contracts.fixture_builder.CONTRACT_FIXTURE_PATH``) -- never from a customer's
harvest, per the template's own worked-example rule (section 7): product fixtures are fair
game, a customer's warehouse is not.

**Closed-set claims get a deterministic backstop, not just judge criteria.** A model asked
whether a term "sounds like" real vocabulary is not reliable evidence -- a live 7B local judge,
tried against ``enrichment_proposer``, hallucinated that ``'pii'`` was invented when the
vocabulary said otherwise. :func:`assert_terms_in_vocabulary` is the reusable check this
package uses instead, and every skill whose schema claims closed-set-only output
(``enrichment_proposer``, ``mapping_proposer``, ``kpi_suggestion``, ``use_case_suggestion``,
``spec_extractor``) wires it (or, where the skill already ships its own oracle over the same
claim -- ``spec_extractor``'s ``validate_spec`` -- reuses that instead) into a ``teardown``
rather than trusting criteria text alone. :func:`_response_content` is the other shared
helper: the "was there anything to check" guard every teardown below opens with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from egpt.agent import Agent
from egpt.eval.suite import Case, CaseResult
from egpt.models.base import Model

from egpt_retail.contracts.fixture_builder import CONTRACT_FIXTURE_PATH
from egpt_retail.contracts.semantic_stub import StubSemanticRead
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.skills import enrichment_proposer as _enrichment
from egpt_retail.skills import funnel_explanation as _funnel
from egpt_retail.skills import kpi_suggestion as _kpi
from egpt_retail.skills import mapping_proposer as _mapping
from egpt_retail.skills import playground as _playground
from egpt_retail.skills import spec_extractor as _spec
from egpt_retail.skills import synonym_proposer as _synonym
from egpt_retail.skills import use_case_suggestion as _use_case
from egpt_retail.skills.join_path_explainer import (
    JoinExplanation,
    JoinVerdict,
    verdict_for_path,
)

__all__ = [
    "SKILL_BUILDERS",
    "assert_terms_in_vocabulary",
    "build_join_path_explainer_agent",
    "join_path_explainer_golden_cases",
    "build_synonym_proposer_agent",
    "synonym_proposer_golden_cases",
    "build_enrichment_proposer_agent",
    "enrichment_proposer_golden_cases",
    "build_mapping_proposer_agent",
    "mapping_proposer_golden_cases",
    "build_kpi_suggestion_agent",
    "kpi_suggestion_golden_cases",
    "build_use_case_suggestion_agent",
    "use_case_suggestion_golden_cases",
    "build_spec_extractor_agent",
    "spec_extractor_golden_cases",
    "build_funnel_explanation_agent",
    "funnel_explanation_golden_cases",
    "build_playground_agent",
    "playground_golden_cases",
]

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _append_case_error(result: CaseResult, message: str) -> None:
    """Mirrors ``egpt.eval.suite``'s own private ``_append_error``: join rather than clobber,
    so a deterministic teardown failure and a judge failure both survive in ``result.error``."""
    result.error = "; ".join(part for part in (result.error, message) if part)


def _response_content(result: CaseResult) -> Optional[Any]:
    """The model's structured output to check, or ``None`` when the run produced nothing to
    check (an error, a timeout, a skip) -- shared by every teardown below rather than
    copy-pasted, since the case's own run failure already covers that outcome and a teardown
    has nothing to add."""
    response = result.response
    if response is None or response.content is None:
        return None
    return response.content


def assert_terms_in_vocabulary(
    result: CaseResult, terms: Any, vocabulary: Any, *, label: str
) -> None:
    """Deterministic closed-set backstop: fails ``result`` for any non-empty term in ``terms``
    absent from ``vocabulary``.

    Membership is checked here, not left to the judge criteria, for the reason
    ``enrichment_proposer``'s own teardown was added: a live 7B local judge, asked whether a
    term "sounds like" real vocabulary, hallucinated that ``'pii'`` was invented when it is
    actually real. A model's opinion about a closed set is never as reliable as checking the
    closed set itself, so every golden case that claims closed-set-only output gets this same
    check rather than trusting judge criteria text alone.
    """
    allowed = set(vocabulary)
    for term in terms:
        if term and term not in allowed:
            _append_case_error(
                result, f"deterministic: {label} {term!r} is not in the vocabulary"
            )


# --- join_path_explainer (K14, second half) -------------------------------------------------

_JOIN_PROMPT = (SKILLS_DIR / "join_path_explainer.md").read_text()
_FIXTURE_READER = StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)
_SNAPSHOT_VERSION = _FIXTURE_READER.snapshot().version


def _join_payload(from_table: str, to_table: str) -> Optional[Dict[str, Any]]:
    path = _FIXTURE_READER.find_join_path(from_table, to_table)
    if path is None:
        return None
    return {
        "from_table": path.from_table,
        "to_table": path.to_table,
        "hops": path.hops,
        "intermediate_tables": list(path.intermediate_tables),
        "edges": [
            {
                "left_table": edge.left_table,
                "right_table": edge.right_table,
                "on": edge.on,
                "cardinality": edge.cardinality,
            }
            for edge in path.edges
        ],
    }


def _join_input(from_table: str, to_table: str, path: Optional[Dict[str, Any]]) -> str:
    import json

    return (
        f"from_table: {from_table}\n"
        f"to_table: {to_table}\n"
        f"snapshot: {json.dumps({'version': _SNAPSHOT_VERSION})}\n"
        f"join_path: {json.dumps(path) if path is not None else '(missing)'}"
    )


def build_join_path_explainer_agent(model: Model) -> Agent:
    """Built once per golden-set run; every case below reuses this same instance."""
    return Agent(
        name="join_path_explainer",
        model=model,
        instructions=_JOIN_PROMPT,
        output_schema=JoinExplanation,
    )


def join_path_explainer_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    """Three cases, lifted from ``K14_join_path_explainer.py``'s own hand-run checks: a real
    path, a null path, and a missing input. Each carries a deterministic teardown against
    ``verdict_for_path`` -- the one thing this agent's identity section forbids it from
    disagreeing with -- on top of the judge criteria."""
    found_path = _join_payload("customers", "products")
    found_expected = verdict_for_path(found_path)

    def _teardown_found(context: Any, result: CaseResult) -> None:
        explanation = _response_content(result)
        if explanation is None:
            return
        if explanation.verdict is not found_expected:
            _append_case_error(
                result,
                f"deterministic: verdict {explanation.verdict.value!r} disagrees with "
                f"verdict_for_path's {found_expected.value!r}",
            )
        if explanation.route != ["customers", "orders", "order_items", "products"]:
            _append_case_error(
                result, f"deterministic: unexpected route {explanation.route!r}"
            )

    null_path_tables = ("supplier_deliveries", "product_reviews")
    null_path = _join_payload(*null_path_tables)

    def _teardown_null(context: Any, result: CaseResult) -> None:
        explanation = _response_content(result)
        if explanation is None:
            return
        if explanation.verdict is not JoinVerdict.NOT_JOINABLE:
            _append_case_error(
                result,
                f"deterministic: expected not_joinable, got {explanation.verdict.value!r}",
            )
        if explanation.route or explanation.caveats:
            _append_case_error(
                result, "deterministic: invented a route or caveat for a null path"
            )

    def _teardown_refusal(context: Any, result: CaseResult) -> None:
        explanation = _response_content(result)
        if explanation is None:
            return
        if (
            explanation.verdict is not JoinVerdict.UNKNOWN
            or not explanation.notes.strip()
        ):
            _append_case_error(
                result, "deterministic: missing input must refuse as unknown with notes"
            )

    return (
        Case(
            name="join_path_explainer-found-path",
            input=_join_input("customers", "products", found_path),
            agent=agent,
            criteria=(
                "The response explains a real join path from customers to products, citing "
                "the tables and predicate the path input actually carries. It invents no table "
                "or join column absent from the input."
            ),
            teardown=_teardown_found,
        ),
        Case(
            name="join_path_explainer-null-path",
            input=_join_input(*null_path_tables, null_path),
            agent=agent,
            criteria=(
                "The response says the two tables are not joinable and explains why without "
                "inventing a join column or a path that the input never provided."
            ),
            teardown=_teardown_null,
        ),
        Case(
            name="join_path_explainer-missing-input",
            input=_join_input("customers", "products", None),
            agent=agent,
            criteria=(
                "The response refuses: it does not guess a verdict for a join path it was "
                "never given, and it says in notes what was missing."
            ),
            teardown=_teardown_refusal,
        ),
    )


# --- synonym_proposer (K14, first half) -----------------------------------------------------

_SYNONYM_PROMPT = (SKILLS_DIR / "EXAMPLE.md").read_text()


def build_synonym_proposer_agent(model: Model) -> Agent:
    return Agent(
        name="synonym_proposer",
        model=model,
        instructions=_SYNONYM_PROMPT,
        output_schema=_synonym.SynonymProposalSet,
    )


def synonym_proposer_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    def _teardown_proposal(context: Any, result: CaseResult) -> None:
        proposal_set = _response_content(result)
        if proposal_set is None:
            return
        if len(proposal_set.proposals) > 8:
            _append_case_error(result, "deterministic: more than eight proposals")
        if any(
            p.value.lower() == "on-time delivery rate" for p in proposal_set.proposals
        ):
            _append_case_error(result, "deterministic: re-proposed an existing synonym")

    def _teardown_refusal(context: Any, result: CaseResult) -> None:
        proposal_set = _response_content(result)
        if proposal_set is None:
            return
        if proposal_set.proposals or not proposal_set.notes.strip():
            _append_case_error(
                result, "deterministic: refusal must be an empty list plus notes"
            )

    return (
        Case(
            name="synonym_proposer-supplier-reliability",
            input=(
                "concept_name: SupplierReliability\n"
                "concept_description: A measure of whether a supplier delivers what was "
                "ordered, when it was promised. Built from delivery outcomes over a trailing "
                "window. Used to decide which suppliers to keep, which to put on notice, and "
                "where to dual-source.\n"
                "existing_synonyms: ['on-time delivery rate']"
            ),
            agent=agent,
            criteria=(
                "Proposes at most eight synonyms for SupplierReliability, none duplicating "
                "'on-time delivery rate', each with a rationale that names a specific part of "
                "the concept description it derives from -- not a generic restatement."
            ),
            teardown=_teardown_proposal,
        ),
        Case(
            name="synonym_proposer-empty-description",
            input="concept_name: SupplierReliability\nconcept_description: \nexisting_synonyms: []",
            agent=agent,
            criteria=(
                "Refuses: returns no proposals and explains in notes that the description was "
                "too thin to derive a synonym from, rather than padding the list with guesses."
            ),
            teardown=_teardown_refusal,
        ),
    )


# --- enrichment_proposer (K8) ----------------------------------------------------------------


def build_enrichment_proposer_agent(model: Model) -> Agent:
    return Agent(
        name="enrichment_proposer",
        model=model,
        instructions=_enrichment.render_prompt(vocabularies=load_vocabulary_set()),
        output_schema=_enrichment.EnrichmentProposalSet,
    )


def enrichment_proposer_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    harvest = (
        "harvest:\n"
        "  table: supplier_deliveries\n"
        "  columns:\n"
        "    - supplier_id (text, fk -> suppliers.id)\n"
        "    - promised_date (date)\n"
        "    - delivered_date (date, nullable)\n"
        "    - on_time (boolean) -- comment: 'true when delivered_date <= promised_date'\n"
        "  row_count: 148000\n"
        "taken_terms: [OnTimeDeliveryRate, Contact, Quote]\n"
    )

    # Closed-set membership is deterministic, not a judgment call -- checking it by asking a
    # judge model whether a term "sounds like" real retail vocabulary is exactly the mistake
    # the alignment rules exist to rule out (a 7B local judge, tried live, guessed 'pii' was
    # invented; the vocabulary says otherwise). So this is a teardown against the real
    # vocabulary, not a clause in the judge criteria.
    vocab = load_vocabulary_set()

    def _teardown_vocab(context: Any, result: CaseResult) -> None:
        proposal_set = _response_content(result)
        if proposal_set is None:
            return
        assert_terms_in_vocabulary(
            result,
            [proposal_set.business_area],
            vocab["business_area"].values,
            label="business_area",
        )
        assert_terms_in_vocabulary(
            result,
            (
                proposal.value
                for proposal in proposal_set.proposals
                if proposal.kind is _enrichment.EnrichmentKind.COMPLIANCE_TAG
            ),
            vocab["compliance_tag"].values,
            label="compliance_tag",
        )

    return (
        Case(
            name="enrichment_proposer-supplier-reliability",
            input=(
                "concept: SupplierReliability -- whether a supplier delivers what was ordered, "
                "when it was promised\n" + harvest
            ),
            agent=agent,
            criteria=(
                "Every proposal's evidence names the supplier_deliveries table or one of its "
                "columns (promised_date, delivered_date, on_time) -- nothing traceable to a "
                "table or column absent from the harvest. It never states or implies that a "
                "proposal is accepted, feasible or safe to use -- only a person decides that."
            ),
            teardown=_teardown_vocab,
        ),
    )


# --- mapping_proposer (K8) --------------------------------------------------------------------

#: The closed set this agent's prompt is rendered with (``render_prompt(concepts=...)``) --
#: shared between the builder and the golden case's teardown so the two cannot drift apart.
_MAPPING_CONCEPTS = ("Quote", "Contact", "Supplier")


def build_mapping_proposer_agent(model: Model) -> Agent:
    return Agent(
        name="mapping_proposer",
        model=model,
        instructions=_mapping.render_prompt(concepts=_MAPPING_CONCEPTS),
        output_schema=_mapping.MappingProposalSet,
    )


def mapping_proposer_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    # crm_deal -> Quote is the module's own worked example (mapping_proposer.py's docstring):
    # "The ontology says what a Quote is, once, for everyone; this agent says that here it
    # lives in crm_deal." Reused rather than invented, per the template's fixture rule.
    harvest = (
        "harvest:\n"
        "  table: crm_deal\n"
        "  columns:\n"
        "    - id (text, pk)\n"
        "    - requested_by_contact_id (text, fk -> crm_contact.id)\n"
        "    - requested_amount (numeric) -- comment: 'the amount quoted to the customer'\n"
        "    - stage (text) -- comment: 'pipeline stage: requested, quoted, won, lost'\n"
        "concepts: [Quote, Contact, Supplier]\n"
        "collisions: []\n"
    )

    # Deterministic closed-set backstop (id=3988596568): the criteria already says "from the
    # supplied concept list only", but a judge model's opinion about that is exactly the weak
    # link enrichment_proposer's own teardown exists to route around.
    def _teardown_concepts(context: Any, result: CaseResult) -> None:
        proposal_set = _response_content(result)
        if proposal_set is None:
            return
        assert_terms_in_vocabulary(
            result,
            (proposal.concept for proposal in proposal_set.proposals),
            _MAPPING_CONCEPTS,
            label="concept",
        )

    return (
        Case(
            name="mapping_proposer-crm-deal-is-a-quote",
            input=harvest,
            agent=agent,
            criteria=(
                "Proposes crm_deal as an instance of the Quote concept (from the supplied "
                "concept list only -- Quote, Contact or Supplier, never a fourth name), citing "
                "the requested_amount or stage column as evidence. It never claims this mapping "
                "is accepted or authoritative -- that is a person's decision, not this agent's."
            ),
            teardown=_teardown_concepts,
        ),
    )


# --- kpi_suggestion (K12) ----------------------------------------------------------------------


def build_kpi_suggestion_agent(model: Model) -> Agent:
    return Agent(
        name="kpi_suggestion",
        model=model,
        instructions=_kpi.render_prompt(
            vocabularies=load_vocabulary_set(), catalogue=(), existing=()
        ),
        output_schema=_kpi.KpiSuggestionDraftSet,
    )


def kpi_suggestion_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    harvest = (
        "harvest:\n"
        "  table: supplier_deliveries\n"
        "  columns: [supplier_id, promised_date, delivered_date, on_time]\n"
        "ontology_concepts: [SupplierReliability]\n"
        "prose_excerpts: []\n"
    )

    # Deterministic closed-set backstop (id=3988596568): business_area and grain are each
    # claimed to be "a term from the ... vocabulary in section 6" (kpi_suggestion.py's own
    # field descriptions) -- checked against the real vocabulary, not a judge's opinion of it.
    vocab = load_vocabulary_set()

    def _teardown_vocab(context: Any, result: CaseResult) -> None:
        draft_set = _response_content(result)
        if draft_set is None:
            return
        assert_terms_in_vocabulary(
            result,
            (draft.business_area for draft in draft_set.suggestions),
            vocab["business_area"].values,
            label="business_area",
        )
        assert_terms_in_vocabulary(
            result,
            (draft.grain for draft in draft_set.suggestions),
            vocab["grain"].values,
            label="grain",
        )

    return (
        Case(
            name="kpi_suggestion-supplier-on-time-rate",
            input=harvest,
            agent=agent,
            criteria=(
                "Proposes a candidate KPI grounded in the on_time column of "
                "supplier_deliveries (an on-time-rate style metric), naming the source table it "
                "derives from. It never states a feasibility verdict, a ready/blocked/partial "
                "status, or any other judgement of whether the KPI can be computed -- that is a "
                "deterministic set difference computed elsewhere, and this schema has no field "
                "for it."
            ),
            teardown=_teardown_vocab,
        ),
    )


# --- use_case_suggestion (K13) ------------------------------------------------------------------


def build_use_case_suggestion_agent(model: Model) -> Agent:
    return Agent(
        name="use_case_suggestion",
        model=model,
        instructions=_use_case.render_prompt(
            vocabularies=load_vocabulary_set(), kpis=(), templates=()
        ),
        output_schema=_use_case.UseCaseSuggestionDraftSet,
    )


def use_case_suggestion_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    harvest = (
        "harvest:\n"
        "  table: supplier_deliveries\n"
        "  columns: [supplier_id, promised_date, delivered_date, on_time]\n"
        "business_context: A buying team wants to know which suppliers are slipping.\n"
    )

    # Deterministic closed-set backstop (id=3988596568), same rationale as kpi_suggestion's:
    # business_area is claimed to be "a term from the business_area vocabulary in section 6".
    vocab = load_vocabulary_set()

    def _teardown_vocab(context: Any, result: CaseResult) -> None:
        draft_set = _response_content(result)
        if draft_set is None:
            return
        assert_terms_in_vocabulary(
            result,
            (draft.business_area for draft in draft_set.suggestions),
            vocab["business_area"].values,
            label="business_area",
        )

    return (
        Case(
            name="use_case_suggestion-supplier-watch",
            input=harvest,
            agent=agent,
            criteria=(
                "Suggests a use case grounded in supplier_deliveries's on_time column (a "
                "supplier reliability watch, or similar), naming one of the four shapes "
                "(inform, alert, recommend, act) it is. It does not claim the shape is ready to "
                "run or that anything is wired up to deliver it -- readiness is a deterministic "
                "check this agent's schema does not carry."
            ),
            teardown=_teardown_vocab,
        ),
    )


# --- spec_extractor (R9) -----------------------------------------------------------------------

#: The closed sets the agent's prompt is rendered with -- shared between the builder and the
#: golden case's teardown so the two cannot drift apart.
_SPEC_SETS = _spec.ClosedSets(
    business_areas=("supply_chain", "customer"),
    grains=("brand", "supplier", "overall"),
    concepts=("SupplierReliability", "Quote", "Contact"),
    kpi_identifiers=("supplier__on_time_rate_30d",),
)


def build_spec_extractor_agent(model: Model) -> Agent:
    agent = _spec.build_spec_extractor(_SPEC_SETS)
    agent.model = model
    return agent


def spec_extractor_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    clean = _spec.spec_prompt(
        use_case_id="uc_golden_0001",
        question="Which suppliers are slipping on on-time delivery this quarter?",
        snapshot_version=_SNAPSHOT_VERSION,
    )
    unknown_metric = _spec.spec_prompt(
        use_case_id="uc_golden_0002",
        question="What is our average basket size by supplier this quarter?",
        snapshot_version=_SNAPSHOT_VERSION,
    )

    # Deterministic closed-set backstop (id=3988596568). spec_extractor.py already ships its
    # own oracle for exactly this claim -- validate_spec(spec, sets) -- so it is reused directly
    # rather than reimplemented as a second assert_terms_in_vocabulary call per field, the same
    # "re-run the skill's own deterministic checker" pattern join_path_explainer's teardown uses
    # for verdict_for_path.
    def _teardown_closed_sets(context: Any, result: CaseResult) -> None:
        spec = _response_content(result)
        if spec is None:
            return
        for problem in _spec.validate_spec(spec, _SPEC_SETS):
            _append_case_error(result, f"deterministic: {problem}")

    return (
        Case(
            name="spec_extractor-plain-question",
            input=clean,
            agent=agent,
            criteria=(
                "Extracts a spec whose business_area, grain, entities[].concept and "
                "kpi_identifiers are drawn only from the rendered lists (supply_chain or "
                "customer; brand, supplier or overall; SupplierReliability, Quote or Contact; "
                "supplier__on_time_rate_30d) -- never a coined value. kpi_identifiers names "
                "supplier__on_time_rate_30d given the question is about on-time delivery."
            ),
            teardown=_teardown_closed_sets,
        ),
        Case(
            name="spec_extractor-unknown-metric",
            input=unknown_metric,
            agent=agent,
            criteria=(
                "The question asks for a metric (average basket size) that is not in the "
                "rendered kpi_identifiers list. The response leaves kpi_identifiers empty for "
                "it and adds a question naming the missing metric, rather than coining a new "
                "kpi_identifier or silently substituting a different one."
            ),
            teardown=_teardown_closed_sets,
        ),
    )


# --- funnel_explanation (R10) ------------------------------------------------------------------

_FUNNEL_PROMPT = (SKILLS_DIR / "funnel_explanation.md").read_text()


def build_funnel_explanation_agent(model: Model) -> Agent:
    return Agent(
        name="funnel_explanation",
        model=model,
        instructions=_FUNNEL_PROMPT,
        output_schema=_funnel.FunnelExplanation,
    )


def funnel_explanation_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    import json

    movers_input = {
        "quote_to_deal_rate": [
            {
                "brand": "northwind",
                "current_value": "18%",
                "prior_value": "23%",
                "delta": "-5pp",
                "direction": "down",
            },
            {
                "brand": "acme",
                "current_value": "31%",
                "prior_value": "29%",
                "delta": "+2pp",
                "direction": "up",
            },
        ]
    }
    friction_available = {
        "available": True,
        "rows": [
            {
                "event_name": "checkout_error",
                "rage_clicks": 4,
                "dead_clicks": 1,
                "script_errors": 2,
            }
        ],
    }
    payload = (
        "kpi_identifiers: [quote_to_deal_rate]\n"
        f"rows: {json.dumps({'quote_to_deal_rate': [{'brand': 'northwind', 'value': '18%', 'as_of': '2026-09-06'}]})}\n"
        f"movers: {json.dumps(movers_input)}\n"
        f"friction: {json.dumps(friction_available)}\n"
        f"snapshot_version: {_SNAPSHOT_VERSION}"
    )

    def _teardown(context: Any, result: CaseResult) -> None:
        explanation = _response_content(result)
        if explanation is None:
            return
        if not _funnel.movers_match_input(explanation, movers_input):
            _append_case_error(
                result,
                "deterministic: top_movers does not match the movers input verbatim",
            )
        if not _funnel.citations_within_input(explanation):
            _append_case_error(
                result,
                "deterministic: cited a brand or friction event absent from the input",
            )
        if not _funnel.friction_matches_input(explanation, friction_available):
            _append_case_error(
                result,
                "deterministic: friction_available/friction/friction_reason does not match "
                "the friction input verbatim",
            )

    return (
        Case(
            name="funnel_explanation-northwind-drop",
            input=payload,
            agent=agent,
            criteria=(
                "Identifies that northwind's quote-to-deal rate fell from 23% to 18% and "
                "acme's rose, and that checkout_error carries elevated friction -- referencing "
                "only northwind, acme and checkout_error in `drivers` by kind and ref, never "
                "by naming them in free text (there is no summary or commentary field to "
                "write). It copies top_movers and friction through unchanged and references "
                "no brand, number or event the input did not carry."
            ),
            teardown=_teardown,
        ),
    )


# --- playground (R11) --------------------------------------------------------------------------

_PLAYGROUND_PROMPT = (SKILLS_DIR / "playground.md").read_text()


def build_playground_agent(model: Model) -> Agent:
    # No tools attached: the golden question here is specifically the no-grounding path
    # (template section 4, phase 3 -- "a tool call comes back declined... is the answer to
    # give"). A tooled golden set belongs to R11 once its toolkit is wired; this agent's own
    # schema and refusal shape are what R15 can check without one.
    return Agent(
        name="playground_probe",
        model=model,
        instructions=_PLAYGROUND_PROMPT,
        output_schema=_playground.PlaygroundAnswer,
    )


def playground_golden_cases(agent: Agent) -> Tuple[Case, ...]:
    def _teardown(context: Any, result: CaseResult) -> None:
        answer = _response_content(result)
        if answer is None:
            return
        if answer.blocks and not answer.notes.strip():
            _append_case_error(
                result,
                "deterministic: fabricated blocks with no tool to cite and no notes",
            )

    return (
        Case(
            name="playground-no-tools-available",
            input="mode: use_case\nquestion: Which suppliers are slipping on on-time delivery?",
            agent=agent,
            criteria=(
                "With no tool available to gather grounding, the response does not fabricate "
                "an answer block claiming a source it never called. It either returns no blocks "
                "with notes explaining nothing could be gathered, or every block's source names "
                "a tool call that was actually made."
            ),
            teardown=_teardown,
        ),
    )


#: Every real agent skill, enumerated for the cookbook and any future caller that wants "all of
#: them" rather than naming each. Order matches SKILL_TEMPLATE.md's own listing, then K14's two.
SKILL_BUILDERS: Dict[
    str, Tuple[Callable[[Model], Agent], Callable[[Agent], Tuple[Case, ...]], str]
] = {
    "enrichment_proposer": (
        build_enrichment_proposer_agent,
        enrichment_proposer_golden_cases,
        "enrichment_proposer.md",
    ),
    "mapping_proposer": (
        build_mapping_proposer_agent,
        mapping_proposer_golden_cases,
        "mapping_proposer.md",
    ),
    "kpi_suggestion": (
        build_kpi_suggestion_agent,
        kpi_suggestion_golden_cases,
        "kpi_suggestion.md",
    ),
    "use_case_suggestion": (
        build_use_case_suggestion_agent,
        use_case_suggestion_golden_cases,
        "use_case_suggestion.md",
    ),
    "spec_extractor": (
        build_spec_extractor_agent,
        spec_extractor_golden_cases,
        "spec_extractor.md",
    ),
    "funnel_explanation": (
        build_funnel_explanation_agent,
        funnel_explanation_golden_cases,
        "funnel_explanation.md",
    ),
    "playground": (build_playground_agent, playground_golden_cases, "playground.md"),
    "synonym_proposer": (
        build_synonym_proposer_agent,
        synonym_proposer_golden_cases,
        "EXAMPLE.md",
    ),
    "join_path_explainer": (
        build_join_path_explainer_agent,
        join_path_explainer_golden_cases,
        "join_path_explainer.md",
    ),
}
