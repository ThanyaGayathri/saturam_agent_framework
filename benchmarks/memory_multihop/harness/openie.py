"""LLM OpenIE + query NER for the HippoRAG-style index.

This is the *write-side* cost the spike is measuring: one LLM extraction pass per
passage turns free text into (subject, relation, object) triples, from which the
KG is built. Query time uses a cheaper NER call to find seed entities.

Both sync and async variants (house rule). All calls go through the record/replay
``LLMClient`` so the extraction pass is paid once, then cached.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional

from harness.llm import LLMClient, Usage

# Extraction is best-effort: an occasional passage yields malformed JSON. We must
# never let one bad passage crash a 285-passage index, so we salvage what parses
# and count the misses. This module-level counter is a data-quality signal
# surfaced in the run summary.
EXTRACTION_FAILURES: List[str] = []

#: Passages the provider's content filter refused outright. Distinct from
#: EXTRACTION_FAILURES (malformed JSON): the model never got to answer. A refused
#: passage contributes no triples, so it is a silent hole in the KG — counted and
#: surfaced rather than swallowed. Seen on real 2Wiki passages at n=949.
CONTENT_FILTERED: List[str] = []


def _is_content_filter(exc: Exception) -> bool:
    t = str(exc).lower()
    return "content management policy" in t or "content_filter" in t or "contentfiltered" in t


_OPENIE_SYSTEM = (
    "You are an information-extraction engine. Given a passage, extract every factual "
    "relation as a triple [subject, relation, object]. Use concise noun-phrase entities "
    "(people, places, works, organisations, dates). Keep surface forms from the text. "
    "Return ONLY a JSON list of [subject, relation, object] triples, no prose."
)

_NER_SYSTEM = (
    "You are a named-entity extractor. Given a question, list the named entities "
    "(people, places, works, organisations, dates) that the answer depends on. "
    "Return ONLY a JSON list of entity strings, no prose."
)


@dataclass(frozen=True)
class Triple:
    subject: str
    relation: str
    object: str


def normalize_entity(text: str) -> str:
    """Canonical key for an entity node: lowercase, trim punctuation/quotes/articles."""
    text = text.strip().strip('"').strip("'").lower()
    text = re.sub(r"^\s*(a|an|the)\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,:;")


def _coerce_triples(raw) -> List[Triple]:
    out: List[Triple] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            s, r, o = str(item[0]), str(item[1]), str(item[2])
        elif isinstance(item, dict):
            s = str(item.get("subject") or item.get("s") or "")
            r = str(item.get("relation") or item.get("r") or item.get("predicate") or "")
            o = str(item.get("object") or item.get("o") or "")
        else:
            continue
        if s.strip() and o.strip():
            out.append(Triple(s.strip(), r.strip(), o.strip()))
    return out


def _coerce_entities(raw) -> List[str]:
    if isinstance(raw, dict):
        raw = raw.get("entities") or raw.get("named_entities") or []
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


# A JSON triple array element, e.g. ["Ada Lovelace", "daughter of", "Lord Byron"].
_TRIPLE_RE = re.compile(r'\[\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\]')


def _salvage_triples(text: str) -> List[Triple]:
    """Regex-recover triples when json.loads fails (usually an unescaped quote)."""
    out: List[Triple] = []
    for s, r, o in _TRIPLE_RE.findall(text):
        if s.strip() and o.strip():
            out.append(Triple(s.strip(), r.strip(), o.strip()))
    return out


def _parse_triples(raw_text: str, tag: str) -> List[Triple]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return _coerce_triples(json.loads(text.strip()))
    except (json.JSONDecodeError, ValueError):
        salvaged = _salvage_triples(text)
        if not salvaged:
            EXTRACTION_FAILURES.append(tag)
        return salvaged


def extract_triples(text: str, llm: LLMClient, usage: Optional[Usage] = None) -> List[Triple]:
    try:
        raw = llm.complete(_OPENIE_SYSTEM, f"Passage:\n{text}", usage=usage, json_mode=True)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a content filter
        if not _is_content_filter(exc):
            raise
        CONTENT_FILTERED.append(text[:40])
        return []
    return _parse_triples(raw, tag=text[:40])


async def aextract_triples(text: str, llm: LLMClient, usage: Optional[Usage] = None) -> List[Triple]:
    try:
        raw = await llm.acomplete(_OPENIE_SYSTEM, f"Passage:\n{text}", usage=usage, json_mode=True)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a content filter
        if not _is_content_filter(exc):
            raise
        CONTENT_FILTERED.append(text[:40])
        return []
    return _parse_triples(raw, tag=text[:40])


_STRING_RE = re.compile(r'"([^"]+)"')


def _parse_entities(raw_text: str) -> List[str]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return _coerce_entities(json.loads(text.strip()))
    except (json.JSONDecodeError, ValueError):
        return [s.strip() for s in _STRING_RE.findall(text) if s.strip()]


def extract_query_entities(question: str, llm: LLMClient, usage: Optional[Usage] = None) -> List[str]:
    try:
        raw = llm.complete(_NER_SYSTEM, f"Question:\n{question}", usage=usage, json_mode=True)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a content filter
        if not _is_content_filter(exc):
            raise
        CONTENT_FILTERED.append(question[:40])
        return []
    return _parse_entities(raw)


async def aextract_query_entities(question: str, llm: LLMClient, usage: Optional[Usage] = None) -> List[str]:
    raw = await llm.acomplete(_NER_SYSTEM, f"Question:\n{question}", usage=usage, json_mode=True)
    return _parse_entities(raw)
