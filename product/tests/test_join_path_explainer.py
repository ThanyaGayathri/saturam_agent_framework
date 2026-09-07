"""Deterministic-rule tests for K14's join path explainer skill (R3, second half of K14).

``verdict_for_path`` is the ground truth a live agent's structured output must never
contradict -- the module's own docstring says a disagreement is the agent doing the one
thing its identity section forbids. It shipped with zero test coverage: inverting its two
branches survived the full contract suite, because nothing else in this repository calls it.
"""

from __future__ import annotations

from egpt_retail.skills.join_path_explainer import (
    JoinVerdict,
    closed_sets_match_prompt,
    verdict_for_path,
)


def test_null_path_is_not_joinable():
    assert verdict_for_path(None) == JoinVerdict.NOT_JOINABLE


def test_path_with_edges_is_joinable():
    assert (
        verdict_for_path({"edges": [{"left_table": "a", "right_table": "b"}]})
        == JoinVerdict.JOINABLE
    )
    assert verdict_for_path({"edges": []}) == JoinVerdict.JOINABLE, (
        "a zero-hop path (from_table == to_table) still has an edges list -- joinable"
    )


def test_unusable_input_is_unknown():
    assert verdict_for_path({"no_edges_key": True}) == JoinVerdict.UNKNOWN
    assert verdict_for_path("not-a-path") == JoinVerdict.UNKNOWN
    assert verdict_for_path(42) == JoinVerdict.UNKNOWN


def test_closed_sets_match_prompt():
    """The prompt enumerates every JoinVerdict value in full -- what stops the two drifting."""
    assert closed_sets_match_prompt() == {"verdict": True}
