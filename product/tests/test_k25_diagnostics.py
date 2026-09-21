"""K25: the diagnostic kind's arithmetic and formula reading, over synthetic numbers.
Nothing here touches a warehouse or a model.
"""

from __future__ import annotations

import pytest

from egpt_retail.diagnostics.decompose import (
    decompose_dimension,
    rank_dimensions,
    robust_z,
    support_floor,
)
from egpt_retail.diagnostics.parts import FormulaShapeError, parts_of

WON_RATE = (
    "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE stage = 'Won') / NULLIF(COUNT(*), 0), 1) "
    "AS value FROM silver.quotes WHERE stage IN ('Won', 'Lost') AND closed_date > "
    "CAST({as_of} AS date) - 90 AND closed_date <= CAST({as_of} AS date)"
)


class TestParts:
    def test_a_filtered_count_rate_is_a_ratio_with_its_winning_predicate(self):
        p = parts_of(WON_RATE)
        assert p.kind == "ratio" and p.scale == 100.0
        assert p.numerator == "COUNT(*) FILTER (WHERE stage = 'Won')"
        assert p.denominator == "COUNT(*)" and p.numerator_predicate == "stage = 'Won'"
        assert p.table == "silver.quotes" and "{as_of}" in p.where

    def test_an_average_is_sum_over_count(self):
        p = parts_of(
            "SELECT ROUND(AVG(days), 1) AS value FROM silver.q WHERE d <= CAST({as_of} AS date)"
        )
        assert (p.kind, p.numerator, p.denominator, p.measure_column) == (
            "ratio",
            "SUM(days)",
            "COUNT(days)",
            "days",
        )

    def test_a_count_by_grain_is_additive_with_its_grain(self):
        p = parts_of(
            "SELECT brand, COUNT(*) AS value FROM silver.q WHERE d > CAST({as_of} AS date) - 28 "
            "GROUP BY brand ORDER BY value DESC"
        )
        assert (
            p.kind == "additive"
            and p.grain_column == "brand"
            and p.numerator == "COUNT(*)"
        )

    def test_a_formula_outside_the_shape_is_refused_not_guessed(self):
        with pytest.raises(FormulaShapeError):
            parts_of("SELECT 1")
        with pytest.raises(FormulaShapeError):
            parts_of("SELECT COUNT(*) AS value FROM silver.q WHERE x = 1")  # no as_of


class TestArithmetic:
    def test_rate_and_mix_effects_sum_to_the_headline_change(self):
        # Two slices; A's own rate falls, B's share of the closes rises.
        current = {"A": (20.0, 50.0), "B": (40.0, 50.0)}  # 60/100 = 60 %
        baseline = {"A": (30.0, 50.0), "B": (20.0, 30.0)}  # 50/80 = 62.5 %
        result = decompose_dimension(
            "cat", "ratio", current, baseline, {}, scale=100.0, floor=1, z_bar=2.0
        )
        total = sum(d.contribution for d in result.drivers)
        assert round(total, 6) == round(60.0 - 62.5, 6)
        a = next(d for d in result.drivers if d.value == "A")
        assert a.effect == "rate" and a.contribution < 0

    def test_additive_contribution_is_the_slices_own_delta(self):
        result = decompose_dimension(
            "brand",
            "additive",
            {"X": (120.0, None), "Y": (80.0, None)},
            {"X": (100.0, None), "Y": (90.0, None)},
            {},
            floor=1,
        )
        by = {d.value: d for d in result.drivers}
        assert by["X"].contribution == 20.0 and by["Y"].contribution == -10.0
        assert by["X"].share_of_gap == 2.0 and result.drivers[0].value == "X"

    def test_the_support_floor_folds_small_slices_away(self):
        result = decompose_dimension(
            "state",
            "additive",
            {"big": (500.0, None), "tiny": (3.0, None)},
            {"big": (400.0, None), "tiny": (0.0, None)},
            {},
            floor=20,
        )
        assert [d.value for d in result.drivers] == ["big"]
        assert result.below_support == [("tiny", 3)]

    def test_surprise_is_against_the_slices_own_history(self):
        assert robust_z([50, 51, 49, 50, 52, 48], 50.5) is not None
        assert abs(robust_z([50, 51, 49, 50, 52, 48], 50.5)) < 1
        assert robust_z([50, 51, 49, 50, 52, 48], 70) == 5.0  # capped
        assert robust_z([50, 50, 50], 50) == 0.0 and robust_z([50, 50, 50], 60) == 5.0
        assert robust_z([50, 51], 70) is None  # too little history

    def test_ranking_prefers_an_unusual_slice_and_reports_ties(self):
        loud = decompose_dimension(
            "a",
            "additive",
            {"x": (200.0, None)},
            {"x": (100.0, None)},
            {"x": [100, 101, 99, 100, 102]},
            floor=1,
        )
        quiet = decompose_dimension(
            "b",
            "additive",
            {"y": (200.0, None)},
            {"y": (100.0, None)},
            {"y": [100, 150, 200, 250, 300]},
            floor=1,
        )
        ordered, ties, by_surprise = rank_dimensions([quiet, loud])
        assert ordered[0].dimension == "a" and by_surprise and ties == []

    def test_support_floor_scales_with_the_period_and_sensitivity(self):
        assert support_floor(100) == 20 and support_floor(1000) == 50
        assert support_floor(100, sensitivity="more") == 10
        assert support_floor(100, sensitivity="clear") == 30

    def test_a_slice_that_pulled_the_other_way_is_never_the_driver(self):
        # The headline fell by 10; A fell by 30 while B rose by 20. A is the driver; B,
        # the larger mover in absolute terms had it been a rise, is the contrast.
        result = decompose_dimension(
            "cat",
            "additive",
            {"A": (70.0, None), "B": (120.0, None)},
            {"A": (100.0, None), "B": (100.0, None)},
            {},
            floor=1,
        )
        assert [d.value for d in result.drivers] == ["A", "B"]
        assert (
            result.drivers[0].share_of_gap == 3.0
            and result.drivers[1].share_of_gap == -2.0
        )
