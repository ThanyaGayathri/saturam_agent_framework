"""Offline self-tests for the harness.

These use a scripted fake LLM so the whole pipeline (extraction -> merge ->
provenance/bi-temporal ops -> metrics) can be exercised deterministically without
Azure credentials or network. Run with:

    .venv/bin/python -m pytest tests/ -q      # if pytest available
    .venv/bin/python tests/test_harness.py    # plain-run fallback

The fake returns plausible structured outputs keyed on the system prompt, so it
validates plumbing and provenance, not answer quality (that needs the real LLM).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.datasets.synthetic import build_synthetic  # noqa: E402
from harness.llm import LLMClient  # noqa: E402
from harness.metrics import label_metrics  # noqa: E402
from harness.runner import run_strategy  # noqa: E402
from harness.strategies import STRATEGIES  # noqa: E402
from harness.strategies.ops import apply_merge  # noqa: E402


class FakeLLM(LLMClient):
    """Scripted LLM: extracts one atomic fact per episode, never merges."""

    def __init__(self):
        # no model, no cache writes needed
        super().__init__(model=object(), mode="live")

    def _fake_extract(self, user: str):
        facts = []
        for line in user.splitlines():
            line = line.strip()
            if not line.startswith("["):
                continue
            idx = int(line[1 : line.index("]")])
            text = line[line.index("]") + 1 :].strip()
            facts.append({"statement": text, "source_indices": [idx], "importance": 6, "topics": ["t"]})
        return facts

    def _charge(self, usage, out_text):
        entry_in, entry_out = 50, max(1, len(out_text) // 4)
        usage.add(entry_in, entry_out, 0.01, 0.0)

    def complete(self, system, user, usage, json_mode=False):
        self._charge(usage, user)
        if "single rolling profile" in system or "compact paragraph" in system:
            return "Profile summary: " + user[:120]
        if "currently true" in system:  # assertion judge
            stmt = user.split("Statement:")[-1].split("Is this")[0].lower()
            ctx = user.split("Statement:")[0].lower()
            key = [w for w in stmt.split() if len(w) > 4]
            return "YES" if sum(w in ctx for w in key) >= max(1, len(key) // 2) else "NO"
        return "I don't know"

    async def acomplete(self, system, user, usage, json_mode=False):
        return self.complete(system, user, usage, json_mode)

    def complete_json(self, system, user, usage):
        if "extract" in system.lower() and "insight" not in system.lower():
            out = self._fake_extract(user)
        elif "reflecting" in system.lower() or "insight" in system.lower():
            out = [{"insight": "User has stable long-term preferences.", "source_facts": [0, 1], "importance": 7}]
        elif "one operation" in system:
            out = {"op": "ADD", "target_id": None, "statement": user.split("\n")[1] if "\n" in user else user}
        else:
            out = []
        self._charge(usage, json.dumps(out))
        return out

    async def acomplete_json(self, system, user, usage):
        return self.complete_json(system, user, usage)


def test_all_strategies_run():
    ds = build_synthetic()
    llm = FakeLLM()
    for name in STRATEGIES:
        report = run_strategy(name, ds, llm)
        assert report["strategy"] == name
        assert report["n_episodes"] == ds.n_episodes
        if name != "baseline":
            assert report["usage"]["llm_calls"] > 0
        print(f"OK {name}: facts={report['label_metrics']['active_fact_count']} quality={report['composite_quality']}")


def test_provenance_and_bitemporal_on_update():
    from harness.schemas import SemanticFact

    facts = [
        SemanticFact(fact_id="f1", statement="Priya drinks tea", source_episode_ids=["e21"], created_at=1, valid_from=1)
    ]
    rec = apply_merge(
        facts=facts,
        decision={"op": "UPDATE", "target_id": "f1", "statement": "Priya drinks coffee"},
        source_episode_ids=["e41"],
        timestamp=100,
        strategy_name="test",
    )
    old = next(f for f in facts if f.fact_id == "f1")
    new = next(f for f in facts if f.fact_id == rec["new_id"])
    assert old.valid_to == 100 and old.superseded_by == new.fact_id  # history preserved
    assert not old.is_active and new.is_active
    assert set(new.source_episode_ids) == {"e21", "e41"}  # provenance unioned forward
    print("OK provenance+bitemporal UPDATE")


def test_delete_retires_without_erasing():
    from harness.schemas import SemanticFact

    facts = [
        SemanticFact(
            fact_id="f1", statement="Priya lives in Bangalore", source_episode_ids=["e11"], created_at=1, valid_from=1
        )
    ]
    apply_merge(
        facts=facts,
        decision={"op": "DELETE", "target_id": "f1", "statement": ""},
        source_episode_ids=[],
        timestamp=50,
        strategy_name="test",
    )
    assert len(facts) == 1 and not facts[0].is_active and facts[0].valid_to == 50
    print("OK delete retires without erasing")


def test_label_metrics_shape():
    ds = build_synthetic()
    from harness.schemas import SemanticFact

    # Construct a 'perfect' memory: one fact per active target with gold provenance.
    facts = []
    for i, t in enumerate([t for t in ds.targets if t.should_be_active]):
        facts.append(
            SemanticFact(
                fact_id=f"f{i}",
                statement=t.statement,
                source_episode_ids=list(t.source_episode_ids),
                created_at=1,
                valid_from=1,
            )
        )
    m = label_metrics(facts, ds.targets, ds.episode_ids)
    assert m["fact_recall"] == 1.0, m
    assert m["conflict_accuracy"] == 1.0, m
    assert m["provenance_recall"] == 1.0, m
    print(f"OK label metrics on perfect memory: {m}")


if __name__ == "__main__":
    test_all_strategies_run()
    test_provenance_and_bitemporal_on_update()
    test_delete_retires_without_erasing()
    test_label_metrics_shape()
    print("\nAll offline self-tests passed.")
