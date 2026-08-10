"""LOCOMO loader (multi-session dialogue memory benchmark).

LOCOMO (Maharana et al., 2024; https://github.com/snap-research/locomo) is 10
very long multi-session dialogues with QA probes categorized single-hop,
multi-hop, temporal, open-domain and adversarial. It provides *turn-level*
evidence (``dia_id``s), so unlike the sibling memory_consolidation loader we keep
``evidence`` and ``category`` -- they drive recall@k / nDCG@k and the temporal
slice.

We do NOT vendor the corpus. ``download()`` fetches locomo10.json into the
gitignored raw dir; ``build(path)`` parses the real file OR the committed raw
sample under fixtures/raw/.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Optional

from harness.datasets.base import load_fixture
from harness.schemas import (
    CAT_ADVERSARIAL,
    CAT_MULTI_SESSION,
    CAT_OPEN_DOMAIN,
    CAT_SINGLE_SESSION,
    CAT_TEMPORAL,
    Benchmark,
    Probe,
    Session,
    Turn,
)

_RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "locomo" / "raw"
_DEFAULT_URL = os.getenv(
    "LOCOMO_URL",
    "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
)
_BASE_TS = 1_700_000_000
_DAY = 86400

# LOCOMO category id (1-5) -> canonical category. (Paper: 1 multi-hop,
# 2 temporal, 3 open-domain, 4 single-hop, 5 adversarial.)
_CAT_MAP = {
    1: CAT_MULTI_SESSION,
    2: CAT_TEMPORAL,
    3: CAT_OPEN_DOMAIN,
    4: CAT_SINGLE_SESSION,
    5: CAT_ADVERSARIAL,
}


def _raw_path() -> Path:
    env = os.getenv("LOCOMO_JSON")
    return Path(env) if env else _RAW_DIR / "locomo10.json"


def download(url: Optional[str] = None) -> Path:
    """Fetch locomo10.json into the gitignored raw dir. Returns the path."""
    import urllib.request

    url = url or _DEFAULT_URL
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = _RAW_DIR / "locomo10.json"
    urllib.request.urlretrieve(url, dest)  # noqa: S310 (trusted public dataset URL)
    return dest


def _session_index(key: str) -> int:
    m = re.search(r"(\d+)", key)
    return int(m.group(1)) if m else 0


def build(path: Path, sample_index: int = 0, max_sessions: Optional[int] = None) -> Benchmark:
    """Parse one LOCOMO sample into a Benchmark, preserving evidence + category."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"LOCOMO raw file not found at {path}. Fetch it once with "
            "harness.datasets.locomo.download(), or set LOCOMO_JSON."
        )
    data = json.loads(path.read_text())
    sample = data[sample_index]
    conversation = sample.get("conversation", sample)

    session_keys = sorted(
        (k for k in conversation if k.startswith("session_") and isinstance(conversation[k], list)),
        key=_session_index,
    )
    if max_sessions:
        session_keys = session_keys[:max_sessions]

    sessions: List[Session] = []
    valid_ids: set = set()
    for si, key in enumerate(session_keys):
        base = _BASE_TS + si * _DAY
        batch: List[Turn] = []
        for ti, turn in enumerate(conversation[key]):
            text = turn.get("text") or turn.get("clean_text") or ""
            if not text:
                continue
            tid = str(turn.get("dia_id") or f"{key}_t{ti}")
            valid_ids.add(tid)
            batch.append(
                Turn(
                    turn_id=tid,
                    speaker=str(turn.get("speaker", "?")),
                    text=str(text),
                    timestamp=base + ti,
                )
            )
        if batch:
            sessions.append(Session(session_id=key, turns=batch))

    probes: List[Probe] = []
    for qi, qa in enumerate(sample.get("qa", [])):
        q = qa.get("question")
        a = qa.get("answer", qa.get("adversarial_answer"))
        if q is None or a is None:
            continue
        cat = _CAT_MAP.get(qa.get("category", 4), CAT_SINGLE_SESSION)
        evidence = [str(e) for e in (qa.get("evidence") or []) if str(e) in valid_ids]
        probes.append(
            Probe(
                probe_id=f"locomo{sample_index}_q{qi}",
                question=str(q),
                answer=str(a),
                category=cat,
                evidence_ids=evidence,
                raw_category=str(qa.get("category")),
            )
        )

    bench = Benchmark(
        name=f"locomo[{sample_index}]",
        sessions=sessions,
        probes=probes,
        meta={
            "source": "github.com/snap-research/locomo",
            "sample_index": sample_index,
            "license": "research use; not redistributed (fetched on demand)",
        },
    )
    bench.validate()
    return bench


def fixture() -> Benchmark:
    return load_fixture("locomo")
