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
from harness.schemas import UsageStats  # noqa: E402
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


class _ScriptedPipelineLLM(LLMClient):
    """Scripts extraction and merge decisions per call, in order -- for exercising
    ``PipelineStrategy``'s retirement handling deterministically (round-2 review, PR #178:
    ``FakeLLM`` above never scripts anything but ADD, so it cannot exercise UPDATE/DELETE through
    ``PipelineStrategy._apply``)."""

    def __init__(self, extract_script, merge_script):
        super().__init__(model=object(), mode="live")
        self._extract_script = list(extract_script)
        self._merge_script = list(merge_script)

    def complete(self, system, user, usage, json_mode=False):
        raise NotImplementedError

    async def acomplete(self, system, user, usage, json_mode=False):
        raise NotImplementedError

    def complete_json(self, system, user, usage):
        if "one operation" in system:
            return self._merge_script.pop(0)
        return self._extract_script.pop(0)

    async def acomplete_json(self, system, user, usage):
        return self.complete_json(system, user, usage)


def test_pipeline_strategy_carries_retirement_through_apply():
    """``PipelineStrategy._apply`` must fold an UPDATE's retirement write in as retired, not as an
    ordinary active fact -- the bug that structurally pinned ``conflict_accuracy`` (round-2 review,
    PR #178). Two passes: pass 1 ADDs "ada lives in berlin"; pass 2 UPDATEs it to "ada lives in
    lisbon". The berlin fact must come back with ``valid_to``/``superseded_by`` set and
    ``is_active is False``, exactly what ``_apply``'s docstring now documents.
    """
    from harness.schemas import Episode
    from harness.strategies.pipeline import PipelineStrategy

    # Pass 1 has no existing facts, so `FactExtractionPipeline.distil` never calls the merge
    # decider at all (no neighbors to compare against -- `extract.py`'s own short-circuit): the
    # first, and only, merge-script entry belongs to pass 2.
    llm = _ScriptedPipelineLLM(
        extract_script=[
            [{"statement": "ada lives in berlin", "source_indices": [0]}],
            [{"statement": "ada lives in lisbon", "source_indices": [0]}],
        ],
        merge_script=[
            {"op": "UPDATE", "target_index": 0, "statement": "ada lives in lisbon"},
        ],
    )
    strategy = PipelineStrategy(llm)
    usage = UsageStats()

    episode1 = [Episode(episode_id="e1", session_id="s1", user_id="ada", text="ada moved to berlin", timestamp=1000)]
    result1 = strategy.consolidate(episode1, [], usage)
    assert [f.statement for f in result1.facts] == ["ada lives in berlin"]
    berlin_id = result1.facts[0].fact_id

    episode2 = [Episode(episode_id="e2", session_id="s1", user_id="ada", text="ada moved to lisbon", timestamp=2000)]
    result2 = strategy.consolidate(episode2, result1.facts, usage)

    by_id = {f.fact_id: f for f in result2.facts}
    berlin = by_id[berlin_id]
    lisbon = next(f for f in result2.facts if f.fact_id != berlin_id)

    assert berlin.is_active is False, "the retired berlin fact must not read as active"
    # `record.expired_at` is transaction time (wall-clock "now" the retirement write was built at,
    # extract.py's own `now_epoch_s()`), not the scripted episode `timestamp` -- so this pins
    # "set to something", not a specific value.
    assert berlin.valid_to is not None
    assert berlin.superseded_by == lisbon.fact_id
    assert lisbon.is_active is True
    print("OK PipelineStrategy._apply carries retirement through")


def test_pipeline_strategy_search_excludes_retired_facts():
    """``_HarnessVector.search`` must not hand the pipeline an already-retired fact as a merge
    candidate (round-2 review, PR #178) -- latent until retirement round-trips correctly, which the
    test above now proves it does."""
    from egpt.memory.types import MemoryQuery, Scope
    from harness.schemas import SemanticFact
    from harness.strategies.pipeline import _HarnessVector

    active = SemanticFact(fact_id="f1", statement="ada lives in lisbon", created_at=1, valid_from=1)
    retired = SemanticFact(
        fact_id="f0", statement="ada lives in berlin", created_at=1, valid_from=1, valid_to=100, superseded_by="f1"
    )
    vector = _HarnessVector([retired, active])

    hits = vector.search(Scope(tenant_id="t"), MemoryQuery(text="ada lives", limit=10))

    assert [h.id for h in hits] == ["f1"], "a retired fact must not be offered as a merge candidate"
    print("OK _HarnessVector.search excludes retired facts")


def test_extractor_shim_accepts_a_bare_list_and_rejects_unparseable_payloads():
    """``_HarnessFactExtractor._parse`` silently returned no facts for a bare top-level JSON array
    (round-2 review, PR #178) -- a shape ``strategies/base.py``'s own ``_coerce_extractions``
    already handles. Also pins the other half: a genuinely unparseable payload must raise, not
    read as "zero facts"."""
    from harness.strategies.pipeline import _HarnessFactExtractor

    bare_list = [{"statement": "ada lives in berlin", "source_indices": [0]}]
    parsed = _HarnessFactExtractor._parse(bare_list)
    assert [c.statement for c in parsed] == ["ada lives in berlin"]

    wrapped = {"facts": bare_list}
    assert [c.statement for c in _HarnessFactExtractor._parse(wrapped)] == ["ada lives in berlin"]

    for bad in ["not json", {"no_facts_key": []}, 42, None]:
        try:
            _HarnessFactExtractor._parse(bad)
        except RuntimeError:
            continue
        raise AssertionError(f"expected _parse to raise for unparseable payload {bad!r}")
    print("OK extractor shim accepts a bare list and rejects unparseable payloads")


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
    test_pipeline_strategy_carries_retirement_through_apply()
    test_pipeline_strategy_search_excludes_retired_facts()
    test_extractor_shim_accepts_a_bare_list_and_rejects_unparseable_payloads()
    test_label_metrics_shape()
    print("\nAll offline self-tests passed.")
