"""KG-quality measurement: does OpenIE actually recover the gold reasoning chain?

`0/285 extraction failures` (the number SPIKE.md originally reported) is **JSON-parse
success**, not triple quality — it says the model returned parseable output, not that
the output was right. PPR's entire advantage is a function of the KG substrate, so the
substrate needs measuring (PR #15 review).

2WikiMultihopQA ships a gold `evidences` field per question — the (subject, relation,
object) chain a correct answer must traverse — so a chain-level check is possible on
this corpus without authoring any labels ourselves.

What is and is not measurable here
----------------------------------
**Recall is well-defined.** The gold chain is exactly the set of edges PPR must be able
to walk, so "did extraction recover the gold chain" is the question that matters.

**Precision against `evidences` is NOT well-defined, and we do not report it.** 2Wiki's
`evidences` lists only the *chain* triples, not every fact in the passage. A passage
legitimately yields many correct triples absent from the gold, so scoring them as false
positives would measure gold sparsity, not extraction error. Instead we report a
**grounding rate** — the fraction of extracted triples whose subject and object strings
actually occur in the source passage — which is a real (if partial) faithfulness signal:
it catches hallucinated entities, though not wrong relations between real ones.

Granularity is chosen to match what PPR consumes
------------------------------------------------
`kg.py` builds relational edges with ``_bump_edge(s, o, "relation", 1.0)`` — **the
relation label is discarded** — and ``personalized_pagerank`` runs over adjacency and
edge weight only. So a relation-strict triple score would understate the substrate PPR
actually uses. We report three nested levels:

  - ``entity``  — both endpoints of a gold triple exist as KG entity nodes.
  - ``edge``    — the two endpoints are *directly connected* (relation-agnostic). **This
                  is the level PPR actually depends on** and the headline number.
  - ``triple``  — subject, relation and object all match (strict; reported for
                  completeness, and expected to be much lower because relation phrasing
                  is free-form).

Both sync and async variants are provided by the callers in ``run_kg_quality.py``; the
scoring itself is pure and synchronous.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from harness.openie import Triple, normalize_entity


def _norm_relation(text: str) -> str:
    return " ".join(text.strip().lower().replace("_", " ").split())


def _entity_variants(text: str) -> Set[str]:
    """Normalized surface plus a token set, so 'Ealhswith' matches 'Queen Ealhswith'."""
    key = normalize_entity(text)
    out = {key}
    toks = [t for t in key.split() if t]
    if toks:
        out.add(" ".join(toks))
    return {v for v in out if v}


def _entity_match(gold: str, candidates: Set[str]) -> bool:
    """Gold entity is present if some KG entity equals it or contains it as a phrase.

    Containment is deliberately one-directional and phrase-based: a KG entity
    'queen ealhswith of mercia' satisfies gold 'ealhswith', but the bare token 'of'
    never satisfies anything, because gold keys are whole normalized phrases.
    """
    g = normalize_entity(gold)
    if not g:
        return False
    if g in candidates:
        return True
    return any(g == c or f" {g} " in f" {c} " for c in candidates)


@dataclass
class ChainScore:
    """Per-level counts over the gold evidence triples."""

    n_gold: int = 0
    entity_hits: int = 0
    edge_hits: int = 0
    triple_hits: int = 0
    #: gold triples whose two endpoints both exist but are not directly connected
    missing_edges: List[Tuple[str, str, str]] = field(default_factory=list)

    def rate(self, level: str) -> float:
        if not self.n_gold:
            return 0.0
        return getattr(self, f"{level}_hits") / self.n_gold

    def to_dict(self) -> Dict:
        return {
            "n_gold_triples": self.n_gold,
            "entity_recall": round(self.rate("entity"), 4),
            "edge_recall": round(self.rate("edge"), 4),
            "triple_recall": round(self.rate("triple"), 4),
            "n_missing_edges": len(self.missing_edges),
        }


def score_chain_recall(
    gold_evidences: Sequence[Sequence[str]],
    kg,
    extracted_by_entity_pair: Optional[Dict[Tuple[str, str], Set[str]]] = None,
) -> ChainScore:
    """Score how much of the gold chain the built KG can actually walk.

    ``kg`` is a ``KnowledgeGraph``; ``extracted_by_entity_pair`` maps a normalized
    (subject, object) pair to the set of normalized relations extracted for it, used
    only for the strict ``triple`` level (the KG itself discards relation labels).
    """
    score = ChainScore()
    entity_keys = set(kg.entity_display.keys())
    for ev in gold_evidences:
        if len(ev) < 3:
            continue
        subj, rel, obj = str(ev[0]), str(ev[1]), str(ev[2])
        score.n_gold += 1

        s_ok = _entity_match(subj, entity_keys)
        o_ok = _entity_match(obj, entity_keys)
        if s_ok and o_ok:
            score.entity_hits += 1
        else:
            continue

        s_key, o_key = _resolve(subj, entity_keys), _resolve(obj, entity_keys)
        if s_key and o_key and kg.graph.has_edge(s_key, o_key):
            score.edge_hits += 1
            rels = (extracted_by_entity_pair or {}).get(_pair(s_key, o_key), set())
            if _norm_relation(rel) in rels:
                score.triple_hits += 1
        else:
            score.missing_edges.append((subj, rel, obj))
    return score


def _resolve(gold: str, entity_keys: Set[str]) -> Optional[str]:
    g = normalize_entity(gold)
    if g in entity_keys:
        return g
    for c in entity_keys:
        if g == c or f" {g} " in f" {c} ":
            return c
    return None


def _pair(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def index_relations(
    triples_by_pid: Dict[str, Sequence[Triple]],
) -> Dict[Tuple[str, str], Set[str]]:
    """Map normalized entity pair -> set of normalized relations seen for it."""
    out: Dict[Tuple[str, str], Set[str]] = {}
    for triples in triples_by_pid.values():
        for t in triples:
            s, o = normalize_entity(t.subject), normalize_entity(t.object)
            if s and o and s != o:
                out.setdefault(_pair(s, o), set()).add(_norm_relation(t.relation))
    return out


def grounding_rate(triples_by_pid: Dict[str, Sequence[Triple]], texts: Dict[str, str]) -> Dict:
    """Fraction of extracted triples whose subject AND object occur in their passage.

    A faithfulness proxy, not precision: it catches invented entities, not wrong
    relations between real ones. See the module docstring for why precision against
    2Wiki `evidences` is not computable.
    """
    total = grounded = 0
    for pid, triples in triples_by_pid.items():
        hay = normalize_entity(texts.get(pid, ""))
        for t in triples:
            total += 1
            s, o = normalize_entity(t.subject), normalize_entity(t.object)
            if s and o and s in hay and o in hay:
                grounded += 1
    return {
        "n_triples": total,
        "grounded": grounded,
        "grounding_rate": round(grounded / total, 4) if total else 0.0,
    }


def aggregate_scores(scores: Iterable[ChainScore]) -> Dict:
    scores = list(scores)
    total = ChainScore()
    for s in scores:
        total.n_gold += s.n_gold
        total.entity_hits += s.entity_hits
        total.edge_hits += s.edge_hits
        total.triple_hits += s.triple_hits
        total.missing_edges.extend(s.missing_edges)
    out = total.to_dict()
    out["n_questions"] = len(scores)
    # Per-question "whole chain walkable" — the multi-hop analogue of all-hops-hit.
    out["full_chain_recall"] = (
        round(
            sum(1 for s in scores if s.n_gold and s.edge_hits == s.n_gold) / len(scores),
            4,
        )
        if scores
        else 0.0
    )
    return out
