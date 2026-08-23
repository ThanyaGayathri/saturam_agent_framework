"""Provenance validity: the anti-lossiness check SM-L7 (#128) adds on top of SM-L3's promotion.

The ratified CI gate (ADR 0003 section A.7) requires ``provenance_validity == 1.0`` -- every fact's
``source_episode_ids`` must resolve. These tests pin the referential check, not a labelled-recall
one: no gold facts are needed, only a live-episode-id set, so the acceptance criterion this pins is
"a fact citing a real episode counts as valid; a fact citing a made-up id does not".
"""

import dataclasses

import pytest

from egpt.memory.consolidation.provenance import (
    ProvenanceReport,
    dangling_source_episode_ids,
    provenance_report,
)
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import FactWrite, MemoryRecord, Scope

ACME = Scope(tenant_id="acme", user_id="ada")


def a_fact(fact_id, source_episode_ids=(), scope=ACME) -> FactWrite:
    return FactWrite(
        record=MemoryRecord(id=fact_id, tier=MemoryTier.SEMANTIC, content="x", scope=scope),
        source_episode_ids=list(source_episode_ids),
    )


# -- dangling_source_episode_ids ---------------------------------------------------------------


def test_dangling_source_episode_ids_empty_when_every_citation_resolves():
    fact = a_fact("f1", source_episode_ids=["e1", "e2"])
    assert dangling_source_episode_ids(fact, {"e1", "e2", "e3"}) == []


def test_dangling_source_episode_ids_names_only_the_unresolved_ones():
    fact = a_fact("f1", source_episode_ids=["e1", "e2", "ghost"])
    assert dangling_source_episode_ids(fact, {"e1", "e2"}) == ["ghost"]


def test_dangling_source_episode_ids_empty_citation_list_is_not_dangling():
    fact = a_fact("f1", source_episode_ids=[])
    assert dangling_source_episode_ids(fact, {"e1"}) == []


def test_dangling_source_episode_ids_preserves_order_and_duplicates():
    fact = a_fact("f1", source_episode_ids=["ghost", "e1", "ghost"])
    assert dangling_source_episode_ids(fact, {"e1"}) == ["ghost", "ghost"]


# -- provenance_report --------------------------------------------------------------------------


def test_provenance_report_all_valid():
    facts = [a_fact("f1", ["e1"]), a_fact("f2", ["e1", "e2"])]
    report = provenance_report(facts, {"e1", "e2"})
    assert report.checked == 2
    assert report.dangling == ()
    assert report.validity == 1.0
    assert report.is_valid is True


def test_provenance_report_flags_a_dangling_citation():
    facts = [a_fact("f1", ["e1"]), a_fact("f2", ["ghost"])]
    report = provenance_report(facts, {"e1"})
    assert report.dangling == ("f2",)
    assert report.validity == 0.5
    assert report.is_valid is False


def test_provenance_report_a_fact_is_dangling_if_any_one_citation_is_missing():
    facts = [a_fact("f1", ["e1", "ghost"])]
    report = provenance_report(facts, {"e1"})
    assert report.dangling == ("f1",)
    assert report.validity == 0.0


def test_provenance_report_ungrounded_fact_with_no_citations_is_valid_not_dangling():
    # An empty `source_episode_ids` is FactExtractionPipeline's own "ungrounded, dropped" outcome
    # (extract.py), not a dangling reference -- this report must not double-count it.
    facts = [a_fact("f1", [])]
    report = provenance_report(facts, set())
    assert report.dangling == ()
    assert report.validity == 1.0


def test_provenance_report_empty_batch_is_vacuously_valid():
    report = provenance_report([], {"e1"})
    assert report.checked == 0
    assert report.validity == 1.0
    assert report.is_valid is True


def test_provenance_report_archived_episode_still_resolves():
    # forget.py archives via row-level expired_at and never hard-deletes -- an archived episode's
    # id is still in the caller's live-episode-id set (it still exists as a row), so citing it is
    # still valid provenance, not a dangling one.
    facts = [a_fact("f1", ["archived-episode"])]
    report = provenance_report(facts, {"archived-episode"})
    assert report.is_valid is True


def test_provenance_report_is_a_frozen_dataclass():
    report = ProvenanceReport(checked=1, dangling=("f1",))
    assert report.checked == 1
    assert report.dangling == ("f1",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.checked = 2  # type: ignore[misc]
