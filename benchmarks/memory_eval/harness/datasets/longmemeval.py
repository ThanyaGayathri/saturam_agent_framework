"""LongMemEval loader (headline long-term-memory benchmark).

LongMemEval (Wu et al., 2024; https://github.com/xiaowu0162/LongMemEval) is 500
curated questions over five abilities: information extraction, multi-session
reasoning, temporal reasoning, knowledge update, and abstention. It is the most
discriminative of the three public sets per the research grounding, so it is the
headline benchmark here.

Real format (per item):
    question_id, question_type, question, answer, question_date,
    haystack_session_ids, haystack_dates,
    haystack_sessions:  [ [ {role, content, has_answer?}, ... ], ... ],
    answer_session_ids: [ ... ]          # gold evidence sessions

We do NOT vendor the corpus (license). ``download()`` fetches it into the
gitignored raw dir; ``build(path)`` parses the real file OR the committed
raw sample under fixtures/raw/.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from harness.datasets.base import load_fixture
from harness.schemas import (
    CAT_ABSTENTION,
    CAT_KNOWLEDGE_UPDATE,
    CAT_MULTI_SESSION,
    CAT_PREFERENCE,
    CAT_SINGLE_SESSION,
    CAT_TEMPORAL,
    Benchmark,
    Probe,
    Session,
    Turn,
)

_RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "longmemeval" / "raw"
# The oracle split (answer sessions only) is the smallest; the full haystack is
# large. Either parses identically. Override with LONGMEMEVAL_URL / LONGMEMEVAL_JSON.
_DEFAULT_URL = os.getenv(
    "LONGMEMEVAL_URL",
    "https://raw.githubusercontent.com/xiaowu0162/LongMemEval/main/data/longmemeval_oracle.json",
)
_BASE_TS = 1_700_000_000
_DAY = 86400

# question_type (raw) -> canonical category.
_TYPE_MAP = {
    "single-session-user": CAT_SINGLE_SESSION,
    "single-session-assistant": CAT_SINGLE_SESSION,
    "single-session-preference": CAT_PREFERENCE,
    "multi-session": CAT_MULTI_SESSION,
    "temporal-reasoning": CAT_TEMPORAL,
    "knowledge-update": CAT_KNOWLEDGE_UPDATE,
}


def _raw_path() -> Path:
    env = os.getenv("LONGMEMEVAL_JSON")
    return Path(env) if env else _RAW_DIR / "longmemeval_oracle.json"


def download(url: Optional[str] = None) -> Path:
    """Fetch the LongMemEval json into the gitignored raw dir. Returns the path."""
    import urllib.request

    url = url or _DEFAULT_URL
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = _RAW_DIR / "longmemeval_oracle.json"
    urllib.request.urlretrieve(url, dest)  # noqa: S310 (trusted public dataset URL)
    return dest


def _parse_date(s: Optional[str], fallback: int) -> int:
    """Best-effort 'YYYY/MM/DD ...' -> epoch seconds; fallback if unparseable."""
    if not s:
        return fallback
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if not m:
        return fallback
    import calendar
    import datetime as _dt

    y, mo, d = (int(g) for g in m.groups())
    try:
        return calendar.timegm(_dt.date(y, mo, d).timetuple())
    except ValueError:
        return fallback


def _category(item: Dict) -> str:
    # Abstention items are flagged by an '_abs' suffix on the question_id.
    qid = str(item.get("question_id", ""))
    if qid.endswith("_abs"):
        return CAT_ABSTENTION
    return _TYPE_MAP.get(item.get("question_type", ""), CAT_SINGLE_SESSION)


def build(path: Path) -> Benchmark:
    """Parse a LongMemEval-format json (real download or raw sample) into one Benchmark.

    All items share one turn-id namespace so evidence lookup is global. Each
    item's sessions are merged into the corpus; ``answer_session_ids`` (and any
    per-turn ``has_answer`` flags) become the probe's gold ``evidence_ids``.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"LongMemEval raw file not found at {path}. Fetch it once with "
            "harness.datasets.longmemeval.download(), or set LONGMEMEVAL_JSON."
        )
    items = json.loads(path.read_text())

    sessions: List[Session] = []
    probes: List[Probe] = []
    seen_sessions: set = set()

    for ii, item in enumerate(items):
        sess_ids = item.get("haystack_session_ids") or [
            f"q{ii}_s{j}" for j in range(len(item.get("haystack_sessions", [])))
        ]
        dates = item.get("haystack_dates") or []
        answer_sessions = set(item.get("answer_session_ids") or [])
        evidence_ids: List[str] = []

        for sj, turns in enumerate(item.get("haystack_sessions", [])):
            sid = str(sess_ids[sj]) if sj < len(sess_ids) else f"q{ii}_s{sj}"
            base = _parse_date(dates[sj] if sj < len(dates) else None, _BASE_TS + sj * _DAY)
            if sid in seen_sessions:
                continue
            seen_sessions.add(sid)
            batch: List[Turn] = []
            for ti, msg in enumerate(turns):
                tid = f"{sid}_t{ti}"
                batch.append(
                    Turn(
                        turn_id=tid,
                        speaker=str(msg.get("role", "user")),
                        text=str(msg.get("content", "")),
                        timestamp=base + ti,
                    )
                )
                # Prefer explicit per-turn gold flags; else all turns of an answer session.
                if msg.get("has_answer") or (not any(m.get("has_answer") for m in turns) and sid in answer_sessions):
                    evidence_ids.append(tid)
            sessions.append(Session(session_id=sid, turns=batch))

        q_ts = _parse_date(item.get("question_date"), _BASE_TS + 999 * _DAY)
        probes.append(
            Probe(
                probe_id=str(item.get("question_id", f"lme_{ii}")),
                question=str(item["question"]),
                answer=str(item.get("answer", "")),
                category=_category(item),
                evidence_ids=evidence_ids,
                as_of=q_ts,
                raw_category=item.get("question_type"),
            )
        )

    bench = Benchmark(
        name="longmemeval",
        sessions=sessions,
        probes=probes,
        meta={
            "source": "github.com/xiaowu0162/LongMemEval",
            "split": path.name,
            "license": "research use; not redistributed (fetched on demand)",
        },
    )
    bench.validate()
    return bench


def fixture() -> Benchmark:
    return load_fixture("longmemeval")
