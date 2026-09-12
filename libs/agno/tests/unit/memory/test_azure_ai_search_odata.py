"""Every ``$filter`` fragment the Azure AI Search memory substrate can emit (AZ-B).

Mirrors ``test_pgvector_memory_statements.py``'s reason for existing: a scope filter is only
missing on the clause nobody happened to exercise, and a missing tenant clause is a cross-tenant
read that a single-tenant test passes cleanly. So this asserts over **every** builder in
``odata.py`` with scopes chosen so no slot's value could be mistaken for another slot's, plus a
dedicated injection check -- the property a bind-parameter mechanism buys pgvector for free and
this module has to earn explicitly, because Azure AI Search's ``$filter`` is a single string with
no separate bind channel.

``odata.py`` itself has no Azure SDK dependency, but importing any submodule of the
``azure_ai_search`` package runs that package's ``__init__.py``, which eagerly imports
``AzureAISearchMemorySubstrate`` -- and that import requires ``azure-search-documents``. Gated
with ``pytest.importorskip`` for the same reason ``test_azure_ai_search_memory_substrate.py`` is,
so the ``unit-drivers-absent`` job (which does not install the optional extra) skips rather than
fails.
"""

import pytest

azure_search_documents = pytest.importorskip(
    "azure.search.documents", reason="azure-search-documents is an optional integration dependency"
)

from egpt.integrations.memory.vector.azure_ai_search import odata  # noqa: E402
from egpt.memory.types import MemoryScopeRequiredError, Scope, ValidWindow  # noqa: E402

FULL = Scope(tenant_id="acme", agent_id="ag", user_id="ada", team_id="tm", session_id="se")
TENANT_ONLY = Scope(tenant_id="acme")
WINDOW = ValidWindow(valid_from=100, valid_to=900)
#: Values that cannot appear inside another slot's value, so a leak test that asserts one slot's
#: value is present cannot pass by accident on a different slot's clause.
UNMISTAKABLE = Scope(
    tenant_id="zztenantzz",
    agent_id="zzagentzz",
    user_id="zzuserzz",
    team_id="zzteamzz",
    session_id="zzsessionzz",
)


# -- scope_filter -------------------------------------------------------------


def test_scope_filter_includes_only_the_set_slots():
    clause = odata.scope_filter(TENANT_ONLY, method="test")
    assert clause == "tenant_id eq 'acme'"


def test_scope_filter_includes_every_set_slot_for_a_full_scope():
    clause = odata.scope_filter(FULL, method="test")
    for slot, value in (
        ("tenant_id", "acme"),
        ("agent_id", "ag"),
        ("user_id", "ada"),
        ("team_id", "tm"),
        ("session_id", "se"),
    ):
        assert f"{slot} eq '{value}'" in clause


def test_every_scope_slot_reaches_the_filter_with_unmistakable_values():
    """Mirrors ``test_pgvector_memory_statements.py``'s own leak test: a slot whose value cannot be
    confused with any other slot's, so this cannot pass by reading the wrong clause."""
    clause = odata.scope_filter(UNMISTAKABLE, method="test")
    assert "zztenantzz" in clause
    assert "zzagentzz" in clause
    assert "zzuserzz" in clause
    assert "zzteamzz" in clause
    assert "zzsessionzz" in clause


def test_scope_filter_requires_a_real_scope():
    with pytest.raises(MemoryScopeRequiredError):
        odata.scope_filter(None, method="test")  # type: ignore[arg-type]


# -- injection --------------------------------------------------------------


def test_a_quote_in_a_scope_value_cannot_splice_a_second_clause():
    """The property a SQL bind parameter buys pgvector for free: a value with no separate bind
    channel must not be able to terminate the OData string literal early and add a clause of its
    own. A caller-supplied tenant is not attacker-controlled in this codebase's threat model, but a
    filter that could be broken by an apostrophe in *any* scope slot is broken by accident too."""
    hostile = Scope(tenant_id="acme", user_id="ada' or user_id eq 'bob")
    clause = odata.scope_filter(hostile, method="test")
    # The literal is escaped to a doubled quote, not left to close early.
    assert "ada'' or user_id eq ''bob" in clause
    # And critically: exactly two top-level ` and `-separated clauses -- tenant_id and user_id,
    # the two slots this scope actually sets. An unescaped value would have spliced a *third*,
    # attacker-controlled clause in via its embedded ` or user_id eq '...`, changing the predicate's
    # structure rather than just its string contents.
    assert clause.split(" and ") == ["tenant_id eq 'acme'", "user_id eq 'ada'' or user_id eq ''bob'"]


def test_a_quote_in_tenant_id_cannot_splice_a_second_clause():
    """``tenant_id`` is the one slot on every request this backend issues, and the test above only
    ever put a hostile value in ``user_id``: dropping the ``escape_odata_literal`` call around just
    the ``tenant_id`` clause left the whole suite green. Asserted here on the same structural
    property -- clause count and shape, not substring containment -- so the escaping of the slot
    that carries tenant isolation is covered by a test that fails when it is removed."""
    hostile = Scope(tenant_id="acme' or tenant_id eq 'globex", user_id="ada")
    clause = odata.scope_filter(hostile, method="test")
    assert clause.split(" and ") == [
        "tenant_id eq 'acme'' or tenant_id eq ''globex'",
        "user_id eq 'ada'",
    ]
    # Two clauses, not three: the spliced ` or tenant_id eq '...` stayed inside the literal rather
    # than becoming a predicate of its own.
    assert len(clause.split(" and ")) == 2


def test_escape_odata_literal_doubles_single_quotes():
    assert odata.escape_odata_literal("O'Brien") == "O''Brien"
    assert odata.escape_odata_literal("plain") == "plain"


# -- tier_filter --------------------------------------------------------------


def test_tier_filter_is_a_disjunction_over_every_tier():
    clause = odata.tier_filter(["episodic", "semantic"])
    assert clause == "(tier eq 'episodic' or tier eq 'semantic')"


def test_tier_filter_refuses_an_empty_sequence():
    with pytest.raises(ValueError, match="tier"):
        odata.tier_filter([])


# -- window_filter ------------------------------------------------------------


def test_window_filter_is_none_when_no_window_is_asked_for():
    assert odata.window_filter(None) is None


def test_window_filter_both_bounds():
    clause = odata.window_filter(WINDOW)
    assert clause == "(t_invalid eq null or t_invalid gt 100) and (t_valid eq null or t_valid lt 900)"


def test_window_filter_open_start():
    clause = odata.window_filter(ValidWindow(valid_to=900))
    assert clause == "(t_valid eq null or t_valid lt 900)"


def test_window_filter_open_end():
    clause = odata.window_filter(ValidWindow(valid_from=100))
    assert clause == "(t_invalid eq null or t_invalid gt 100)"


# -- search_filter: every ranked-read builder inherits scope + tier + belief + window ----------


def test_search_filter_composes_scope_tier_belief_and_window():
    clause = odata.search_filter(FULL, ["episodic"], WINDOW, method="test")
    assert clause == (
        "tenant_id eq 'acme' and agent_id eq 'ag' and user_id eq 'ada' and team_id eq 'tm' "
        "and session_id eq 'se' and (tier eq 'episodic') and expired_at eq null and "
        "(t_invalid eq null or t_invalid gt 100) and (t_valid eq null or t_valid lt 900)"
    )


def test_search_filter_without_a_window_has_no_dangling_and():
    clause = odata.search_filter(TENANT_ONLY, ["episodic"], None, method="test")
    assert clause == "tenant_id eq 'acme' and (tier eq 'episodic') and expired_at eq null"
    assert " and  and" not in clause
    assert not clause.endswith(" and")
