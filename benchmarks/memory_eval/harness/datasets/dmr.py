"""DMR loader (Deep Memory Retrieval, MemGPT/Letta).

DMR asks the system to retrieve a single fact stated in a prior conversation
session. It is *near-saturated* (strong systems score ~95%+), so per issue #88
it is included only as a low-value sanity baseline / cross-check -- never as the
headline signal.

The upstream dump's exact field names vary across MemGPT/Letta releases, so
``build`` targets a documented, lenient shape and raises a clear error if it does
not match, rather than silently mis-parsing. Confirm against the fetched file and
adjust ``_ITEM_KEYS`` if needed (tracked in findings/01).

Expected raw item shape (lenient):
    {
      "question": str, "answer": str,
      "context"|"session"|"messages": [ {"speaker"|"role", "text"|"content"}, ... ],
      "evidence"?: [turn_index or turn_id, ...]
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from harness.datasets.base import load_fixture
from harness.schemas import CAT_SINGLE_SESSION, Benchmark, Probe, Session, Turn

_RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "dmr" / "raw"
_DEFAULT_URL = os.getenv("DMR_URL", "")  # no stable canonical URL; set DMR_URL/DMR_JSON
_BASE_TS = 1_700_000_000

_CTX_KEYS = ("context", "session", "messages", "conversation")


def _raw_path() -> Path:
    env = os.getenv("DMR_JSON")
    return Path(env) if env else _RAW_DIR / "dmr.json"


def download(url: Optional[str] = None) -> Path:
    """Fetch the DMR json into the gitignored raw dir. Requires DMR_URL to be set."""
    import urllib.request

    url = url or _DEFAULT_URL
    if not url:
        raise RuntimeError(
            "DMR has no stable canonical download URL. Set DMR_URL to the raw json, "
            "or DMR_JSON to a local copy from the MemGPT/Letta repo."
        )
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = _RAW_DIR / "dmr.json"
    urllib.request.urlretrieve(url, dest)  # noqa: S310
    return dest


def _turns(item: dict) -> List[dict]:
    for key in _CTX_KEYS:
        if isinstance(item.get(key), list):
            return item[key]
    raise ValueError(
        f"DMR item has no recognized context field (any of {_CTX_KEYS}); "
        "adjust harness.datasets.dmr._CTX_KEYS to the fetched file's shape."
    )


def build(path: Path) -> Benchmark:
    """Parse a DMR-format json (real download or raw sample) into one Benchmark."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"DMR raw file not found at {path}. Fetch it with "
            "harness.datasets.dmr.download() (needs DMR_URL), or set DMR_JSON."
        )
    items = json.loads(path.read_text())

    sessions: List[Session] = []
    probes: List[Probe] = []
    for ii, item in enumerate(items):
        sid = f"dmr_s{ii}"
        batch: List[Turn] = []
        for ti, msg in enumerate(_turns(item)):
            batch.append(
                Turn(
                    turn_id=f"{sid}_t{ti}",
                    speaker=str(msg.get("speaker") or msg.get("role") or "user"),
                    text=str(msg.get("text") or msg.get("content") or ""),
                    timestamp=_BASE_TS + ii * 3600 + ti,
                )
            )
        sessions.append(Session(session_id=sid, turns=batch))

        evidence: List[str] = []
        for ev in item.get("evidence") or []:
            # Accept either a turn index or an explicit turn_id.
            if isinstance(ev, int) and 0 <= ev < len(batch):
                evidence.append(batch[ev].turn_id)
            elif str(ev) in {t.turn_id for t in batch}:
                evidence.append(str(ev))
        probes.append(
            Probe(
                probe_id=f"dmr_q{ii}",
                question=str(item["question"]),
                answer=str(item.get("answer", "")),
                category=CAT_SINGLE_SESSION,
                evidence_ids=evidence,
                raw_category="dmr",
            )
        )

    bench = Benchmark(
        name="dmr",
        sessions=sessions,
        probes=probes,
        meta={
            "source": "MemGPT/Letta Deep Memory Retrieval",
            "license": "Apache-2.0 upstream; not redistributed (fetched on demand)",
            "caveat": "near-saturated; sanity baseline only (issue #88)",
        },
    )
    bench.validate()
    return bench


def fixture() -> Benchmark:
    return load_fixture("dmr")
