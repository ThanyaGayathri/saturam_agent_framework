"""Unit tests for R11's DB-free halves: the playground contracts' own validation, the
deterministic mode router's refusal paths that need no database, and the shared scope-check
dependency. The stores and the live tool calls (gold reads, provenance lookups, K10 retrieval)
are exercised only by the cookbook (cookbook/94_retail/R11_playground.py), the same posture
test_r7_provenance_adapter.py documents for record_provenance's own database write.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from egpt_retail.app.scopes import require_scopes
from egpt_retail.contracts.playground import MAX_CUSTOM_RANGE_DAYS, ChatFilters, Period
from egpt_retail.playground.modes import (
    PlaygroundMode,
    aresolve_mode_scope,
    resolve_mode_scope,
)
from egpt_retail.skills.playground import AnswerBlockKind, closed_sets_match_prompt

# ---------------------------------------------------------------------------
# ChatFilters -- the 730-day cap, from the spec's own 422 example
# ---------------------------------------------------------------------------


def test_custom_range_within_cap_is_accepted():
    filters = ChatFilters(
        owner="u1",
        period=Period.CUSTOM,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 6, 1),
    )
    assert filters.period is Period.CUSTOM


def test_custom_range_over_cap_is_rejected():
    with pytest.raises(ValueError, match=str(MAX_CUSTOM_RANGE_DAYS)):
        ChatFilters(
            owner="u1",
            period=Period.CUSTOM,
            from_date=date(2020, 1, 1),
            to_date=date(2026, 1, 1),
        )


def test_custom_period_without_dates_is_rejected():
    with pytest.raises(ValueError):
        ChatFilters(owner="u1", period=Period.CUSTOM)


def test_non_custom_period_needs_no_dates():
    filters = ChatFilters(owner="u1", period=Period.WEEK)
    assert filters.from_date is None


# ---------------------------------------------------------------------------
# resolve_mode_scope -- refusal before any tool exists
# ---------------------------------------------------------------------------


def test_use_case_mode_without_scope_id_is_refused():
    scope = resolve_mode_scope(PlaygroundMode.USE_CASE, None)
    assert scope.refused is not None
    assert scope.tools == ()


def test_business_area_mode_without_scope_id_is_refused():
    scope = resolve_mode_scope(PlaygroundMode.BUSINESS_AREA, None)
    assert scope.refused is not None
    assert scope.tools == ()


def test_business_area_mode_with_unknown_area_is_refused():
    """No database or graph touched -- the ontology is a local artifact
    (ontology/model.py's ARTIFACT_PATH)."""
    scope = resolve_mode_scope(PlaygroundMode.BUSINESS_AREA, "not_a_real_area")
    assert scope.refused is not None
    assert "not_a_real_area" in scope.refused


def test_business_area_mode_with_known_area_resolves_a_lookup_tool():
    scope = resolve_mode_scope(PlaygroundMode.BUSINESS_AREA, "inventory")
    assert scope.refused is None
    assert len(scope.tools) == 1
    assert scope.tools[0].__name__ == "lookup_concept"


def test_business_area_lookup_declines_a_concept_outside_the_area():
    """The structural guarantee: the tool refuses, it does not merely instruct the model to."""
    scope = resolve_mode_scope(PlaygroundMode.BUSINESS_AREA, "inventory")
    lookup = scope.tools[0]
    in_area = lookup("Inventory")
    out_of_area = lookup("Customer")
    assert "declined" not in in_area
    assert "declined" in out_of_area
    assert "inventory" in out_of_area["declined"]


def test_data_mode_needs_no_scope_id_and_resolves_one_tool():
    scope = resolve_mode_scope(PlaygroundMode.DATA, None)
    assert scope.refused is None
    assert [t.__name__ for t in scope.tools] == ["read_kpi_data"]


def test_knowledge_mode_needs_no_scope_id_and_resolves_one_tool():
    scope = resolve_mode_scope(PlaygroundMode.KNOWLEDGE, None)
    assert scope.refused is None
    assert [t.__name__ for t in scope.tools] == ["search_knowledge"]


def test_use_case_mode_with_scope_id_resolves_two_tools():
    scope = resolve_mode_scope(PlaygroundMode.USE_CASE, "uc_1")
    assert scope.refused is None
    assert {t.__name__ for t in scope.tools} == {"list_runs", "get_run"}


# ---------------------------------------------------------------------------
# aresolve_mode_scope -- the async twin app/components.py::build_playground_agent
# actually calls (round-1 review: this was previously unused, dead code)
# ---------------------------------------------------------------------------


def test_aresolve_mode_scope_agrees_with_the_sync_version():
    async def _both():
        return (
            await aresolve_mode_scope(PlaygroundMode.BUSINESS_AREA, "inventory"),
            resolve_mode_scope(PlaygroundMode.BUSINESS_AREA, "inventory"),
        )

    async_scope, sync_scope = asyncio.run(_both())
    assert async_scope.refused == sync_scope.refused
    assert [t.__name__ for t in async_scope.tools] == [
        t.__name__ for t in sync_scope.tools
    ]


def test_aresolve_mode_scope_refuses_an_unknown_area_too():
    scope = asyncio.run(
        aresolve_mode_scope(PlaygroundMode.BUSINESS_AREA, "not_a_real_area")
    )
    assert scope.refused is not None


# ---------------------------------------------------------------------------
# skills/playground.py -- the closed set the prompt and the schema must agree on
# ---------------------------------------------------------------------------


def test_answer_block_kind_is_fully_enumerated_in_the_prompt():
    result = closed_sets_match_prompt()
    assert result == {"kind": True}


def test_answer_block_kind_values_are_the_five_from_the_contract():
    assert {member.value for member in AnswerBlockKind} == {
        "text",
        "finding",
        "table",
        "chart",
        "kpi",
        "recommendation",
    }


# ---------------------------------------------------------------------------
# require_scopes -- the shared dependency, without a real request
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeRequest:
    def __init__(self, **state_kwargs):
        self.state = _FakeState(**state_kwargs)


def test_require_scopes_noops_when_authorization_is_off():
    dependency = require_scopes("playground:write")
    dependency(_FakeRequest(authorization_enabled=False))  # must not raise


def test_require_scopes_allows_a_caller_with_every_scope():
    dependency = require_scopes("playground:write")
    dependency(
        _FakeRequest(
            authorization_enabled=True, scopes=["playground:write", "other:read"]
        )
    )


def test_require_scopes_refuses_a_caller_missing_a_scope():
    from fastapi import HTTPException

    dependency = require_scopes("playground:write")
    with pytest.raises(HTTPException) as excinfo:
        dependency(_FakeRequest(authorization_enabled=True, scopes=["other:read"]))
    assert excinfo.value.status_code == 403


# ---------------------------------------------------------------------------
# _owner -- round-1 review: a valid, scoped token with no `sub` claim must not
# collapse onto the shared "local" owner
# ---------------------------------------------------------------------------


def test_owner_is_local_when_authorization_is_off():
    from egpt_retail.api.routers.playground import _owner

    assert _owner(_FakeRequest(authorization_enabled=False)) == "local"


def test_owner_returns_the_authenticated_subject():
    from egpt_retail.api.routers.playground import _owner

    assert _owner(_FakeRequest(authorization_enabled=True, user_id="u1")) == "u1"


def test_owner_refuses_a_scoped_token_with_no_subject_claim():
    """JWTMiddleware sets user_id=None explicitly for a token that decodes and passes
    scope checks but carries no `sub` claim -- falling back to a shared "local" here would
    let every such caller read and mutate every other's saved prompts and reports."""
    from fastapi import HTTPException

    from egpt_retail.api.routers.playground import _owner

    with pytest.raises(HTTPException) as excinfo:
        _owner(_FakeRequest(authorization_enabled=True, user_id=None))
    assert excinfo.value.status_code == 401
