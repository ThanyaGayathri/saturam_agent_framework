"""Unit tests for the metric engine, checked against hand-computed values.

Runs under pytest, or standalone: ``python tests/test_metrics.py`` (prints OK).
The hand-computed expectations are spelled out in comments so a reviewer can
verify the formula, not just that a number is stable.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.judge import StubJudge  # noqa: E402
from harness.metrics import (  # noqa: E402
    ProbeRecord,
    aggregate,
    hit_at_k,
    ndcg_at_k,
    recall_at_k,
)
from harness.schemas import Probe, Turn  # noqa: E402
from harness.stats import p95, percentile, wilson_interval  # noqa: E402

_TOL = 1e-9


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def test_recall_and_hit_at_k() -> None:
    ev = ["a", "b"]
    r = ["x", "a", "y", "b", "z"]
    # top-3 = [x,a,y]: {a,b} ∩ = {a} -> 0.5 ; hit=1
    assert _approx(recall_at_k(ev, r, 3), 0.5)
    assert hit_at_k(ev, r, 3) == 1.0
    # top-5: both present -> recall 1.0
    assert _approx(recall_at_k(ev, r, 5), 1.0)
    # no evidence -> None (excluded, never 0)
    assert recall_at_k([], r, 3) is None
    assert hit_at_k([], r, 3) is None
    # a miss at k
    assert hit_at_k(["q"], r, 5) == 0.0


def test_ndcg_at_k() -> None:
    ev = ["a", "b"]
    r = ["x", "a", "y", "b", "z"]
    # k=3: rel at pos1 (a) only -> DCG=1/log2(3); IDCG=1/log2(2)+1/log2(3)
    dcg = 1 / math.log2(3)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert _approx(ndcg_at_k(ev, r, 3), dcg / idcg)  # ~0.386861
    # k=5: a@pos1, b@pos3 -> DCG=1/log2(3)+1/log2(5)
    dcg5 = 1 / math.log2(3) + 1 / math.log2(5)
    assert _approx(ndcg_at_k(ev, r, 5), dcg5 / idcg)  # ~0.650915
    assert ndcg_at_k([], r, 3) is None


def test_percentile() -> None:
    xs = list(range(1, 11))  # 1..10
    # p95: rank=0.95*9=8.55 -> 9 + (10-9)*0.55 = 9.55
    assert _approx(p95(xs), 9.55)
    # p50: rank=4.5 -> 5 + (6-5)*0.5 = 5.5
    assert _approx(percentile(xs, 50), 5.5)
    assert percentile([], 95) == 0.0
    assert percentile([42], 95) == 42.0


def test_wilson_interval() -> None:
    low, high = wilson_interval(8, 10)  # phat=0.8
    assert 0.0 < low < 0.8 < high < 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_stub_judge() -> None:
    j = StubJudge()
    assert j.judge("where?", "Berlin", "She moved to Berlin.").correct is True
    assert j.judge("where?", "Globex", "I think it's Acme.").correct is False
    assert j.judge("what?", "German", "She started learning German downtown.").correct is True


def _turn(tid: str, ts: int) -> Turn:
    return Turn(turn_id=tid, speaker="Alice", text=tid, timestamp=ts)


def test_aggregate_end_to_end() -> None:
    j = StubJudge()
    turn_index = {
        "a": _turn("a", 10),
        "e_new": _turn("e_new", 200),
        "e_old": _turn("e_old", 50),
    }

    def rec(pid, cat, ev, retrieved, ans, gold, **kw) -> ProbeRecord:
        probe = Probe(probe_id=pid, question="q", answer=gold, category=cat, evidence_ids=ev, **kw)
        return ProbeRecord(probe=probe, retrieved_ids=retrieved, answer=ans, verdict=j.judge("q", gold, ans))

    records = [
        rec("p1", "single_session", ["a"], ["a", "b"], "answer a", "a"),  # correct
        rec("p2", "temporal", ["c"], ["x", "y"], "nope", "c"),  # incorrect, miss
        rec("p3", "as_of_t", ["e_old"], ["e_new", "e_old"], "e_old", "e_old", as_of=100),  # correct, leak
        rec("p4", "decay", ["g"], ["g", "stale"], "g", "g", decay_target_turn_id="stale"),  # correct
    ]
    rep = aggregate(records, k=2, turn_index=turn_index)

    # overall QA: 3/4 correct
    assert _approx(rep["qa_accuracy"]["value"], 0.75)
    # per-category
    assert _approx(rep["qa_accuracy_by_category"]["single_session"]["value"], 1.0)
    assert _approx(rep["qa_accuracy_by_category"]["temporal"]["value"], 0.0)
    # temporal slice = {temporal, as_of_t}: 1/2
    assert _approx(rep["temporal_accuracy"]["value"], 0.5)
    # retrieval recall mean over all-4-with-evidence: (1+0+1+1)/4 = 0.75
    assert _approx(rep["retrieval"]["recall_at_k"]["value"], 0.75)
    # bitemporal accuracy over {as_of_t, knowledge_update} = p3 -> 1.0; leak on p3 -> 1.0
    assert _approx(rep["bitemporal"]["accuracy"]["value"], 1.0)
    assert _approx(rep["bitemporal"]["as_of_leak_rate"], 1.0)
    # decay: stale in top-2 -> not suppressed (0.0); g(0) outranks stale(1) -> 1.0
    assert rep["decay"]["n"] == 1
    assert _approx(rep["decay"]["suppression_at_k"], 0.0)
    assert _approx(rep["decay"]["outranked"], 1.0)


def test_valid_at_default_lower_bound() -> None:
    # t_valid=None must fall back to `timestamp` and still be EVALUATED (not dropped
    # the way SM-R10's valid_from-IS-NULL bug dropped rows).
    t = Turn("x", "A", "x", timestamp=10)  # t_valid/t_invalid both None
    assert t.valid_at(10) is True  # inclusive lower bound at the default (timestamp)
    assert t.valid_at(100) is True  # open upper bound (+inf)
    assert t.valid_at(5) is False  # before the default lower bound
    # explicit closed window still honored
    bounded = Turn("y", "A", "y", timestamp=10, t_valid=10, t_invalid=50)
    assert bounded.valid_at(49) is True and bounded.valid_at(50) is False


def test_bitemporal_leak_is_two_sided() -> None:
    # SM-R10: valid iff t_valid <= T < t_invalid. The leak metric must flag BOTH a
    # future fact AND a stale-superseded fact returned as current (the case a
    # one-sided future-only check misses).
    j = StubJudge()
    T = 100
    turn_index = {
        "future": Turn("future", "A", "future", timestamp=200, t_valid=200, t_invalid=None),
        "stale": Turn("stale", "A", "stale", timestamp=10, t_valid=10, t_invalid=50),  # superseded at 50 <= T
        "current": Turn("current", "A", "current", timestamp=10, t_valid=10, t_invalid=None),
    }

    def rec(pid, top1) -> ProbeRecord:
        p = Probe(probe_id=pid, question="q", answer="x", category="as_of_t", evidence_ids=[], as_of=T)
        return ProbeRecord(probe=p, retrieved_ids=[top1], answer="x", verdict=j.judge("q", "x", "x"))

    rep = aggregate([rec("pf", "future"), rec("ps", "stale"), rec("pc", "current")], k=5, turn_index=turn_index)
    # future leaks, stale-superseded leaks, current does not -> 2/3
    assert _approx(rep["bitemporal"]["as_of_leak_rate"], 2.0 / 3.0)
    # sanity: the stale case is the one a one-sided (timestamp > T) check would MISS
    # (stale.timestamp=10 is NOT > 100), so a passing 2/3 proves the two-sided fix.


def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} metric tests passed")


if __name__ == "__main__":
    _run_all()
