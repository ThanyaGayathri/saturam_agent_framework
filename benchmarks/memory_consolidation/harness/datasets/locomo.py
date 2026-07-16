"""LOCOMO loader (long-conversation memory benchmark).

LOCOMO is a public set of very long, multi-session dialogues with QA probes
(https://github.com/snap-research/locomo). We use it for *external validity* of
the QA-correctness metric; it has no dedup/conflict/provenance labels, so the
label-based metrics are synthetic-only.

The raw file is not vendored (it is gitignored under data/locomo/raw/). Fetch it
once with ``download_locomo()`` or point LOCOMO_JSON at a local copy. Each LOCOMO
sample becomes one ``Dataset``: sessions -> episode batches, qa -> qa_pairs.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from harness.datasets.base import Dataset
from harness.schemas import Episode

_DEFAULT_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
_RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "locomo" / "raw"
_DAY = 86400
_BASE_TS = 1_700_000_000


def _raw_path() -> Path:
    env = os.getenv("LOCOMO_JSON")
    if env:
        return Path(env)
    return _RAW_DIR / "locomo10.json"


def download_locomo(url: Optional[str] = None) -> Path:
    """Download the LOCOMO json to the gitignored raw dir. Returns the path."""
    import urllib.request

    url = url or os.getenv("LOCOMO_URL", _DEFAULT_URL)
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = _RAW_DIR / "locomo10.json"
    urllib.request.urlretrieve(url, dest)  # noqa: S310 (trusted public dataset URL)
    return dest


def _session_index(key: str) -> int:
    m = re.search(r"(\d+)", key)
    return int(m.group(1)) if m else 0


def build_locomo(sample_index: int = 0, max_sessions: Optional[int] = None) -> Dataset:
    """Build a Dataset from one LOCOMO sample.

    Raises a clear error (with the fix) if the raw file is not present.
    """
    path = _raw_path()
    if not path.exists():
        raise FileNotFoundError(
            f"LOCOMO raw file not found at {path}. Fetch it once with:\n"
            "  from harness.datasets.locomo import download_locomo; download_locomo()\n"
            "or set LOCOMO_JSON to a local copy of locomo10.json."
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

    sessions: List[List[Episode]] = []
    for si, key in enumerate(session_keys):
        turns = conversation[key]
        batch: List[Episode] = []
        for ti, turn in enumerate(turns):
            speaker = turn.get("speaker", "?")
            text = turn.get("text") or turn.get("clean_text") or ""
            if not text:
                continue
            eid = turn.get("dia_id") or f"{key}_t{ti}"
            batch.append(
                Episode(
                    episode_id=str(eid),
                    text=f"{speaker}: {text}",
                    timestamp=_BASE_TS + si * _DAY + ti,
                    user_id=f"locomo_{sample_index}",
                    session_id=key,
                )
            )
        if batch:
            sessions.append(batch)

    qa_pairs: List[Dict[str, str]] = []
    for qa in sample.get("qa", []):
        q = qa.get("question")
        a = qa.get("answer", qa.get("adversarial_answer"))
        if q is None or a is None:
            continue
        qa_pairs.append({"question": str(q), "answer": str(a)})

    return Dataset(name=f"locomo[{sample_index}]", sessions=sessions, targets=[], qa_pairs=qa_pairs)
